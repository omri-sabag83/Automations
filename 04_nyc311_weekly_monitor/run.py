#!/usr/bin/env python3
"""
Automation #04 — NYC 311 weekly service-request monitor.

Scheduled by a launch agent (schedule/com.omrisabag.nyc311-weekly-monitor.plist)
every Sunday at 09:00 Asia/Jerusalem. Can also be run by hand:

    python run.py                       # analyze last week, write the report
    python run.py --dry-run             # write output/_preview.md only; real Reports/ untouched
    python run.py --date 2026-09-14     # pretend today is that date (for testing/backfill)
    python run.py --print-prompt        # also show the exact prompt sent to Claude

Each run:
  1. Window: the calendar week [most recent Sunday 00:00, +7d) in
     America/New_York — the NYC 311 dataset's own timezone. This is
     independent of RUN_TIMEZONE below, which only controls when launchd
     fires this script.
  2. Guardrails (deterministic, run before any `claude` call):
       a. Duplicate check — if this week's report file already exists, log
          where it is and stop. No API or `claude` call made.
       b. No-new-data check — one cheap Socrata count query for the week. If
          zero, log it and stop without writing a file (so a later retry can
          still succeed if this was just source-data lag, not a real gap).
  3. Analysis: `claude` (via the CLI, with Bash access) fetches the week's
     NYC 311 data itself from the Socrata API, decides what to analyze —
     volume, category, geography, anomalies vs. recent weeks — builds real
     matplotlib charts where warranted, and returns the finished Markdown
     report (with `charts/...png` image references) as its only output.
  4. `run.py` validates that output (starts with the right heading, carries
     the exact time-range and generation-timestamp strings it was given,
     every referenced chart actually exists, isn't suspiciously short) and,
     only then, writes it to
     ../../NYC311 Weekly Monitor/Reports/weekly_service_requests_{key}.md,
     copying the referenced chart PNGs alongside it — one new report per
     week, never upserted, never pruned.

ARCHITECTURE NOTE — this automation deliberately breaks the usual
"run.py computes every number, the model only writes prose" rule (see
Automations/README.md and ../.claude/skills/automation-scaffold). The point
of this job is to see Claude decide its own processing steps and perform the
analysis unattended, not narrate a pre-computed digest. run.py keeps
ownership only of what must stay deterministic: the date window, the
duplicate/no-data guardrails, retries, output validation, and state/log
writing. Everything analytical — which dimensions, what counts as an anomaly,
the writeup — is Claude's job, each run, from scratch.

Stdlib only, plus the already-installed `claude` CLI. Nothing is installed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

# ------------------------------------------------------------------- schedule
# SINGLE SOURCE OF TRUTH for when this runs. Keep in sync with the .plist and
# both READMEs (change it by asking Claude, not by hand-editing one place).
# NOTE: these settings control only the launchd trigger time. The analysis
# window itself is always the calendar week in America/New_York (see
# DATA_TZ / week_window() below) — deliberately independent of this.
RUN_MODE = "scheduled"           # "manual" | "scheduled" (launchd agent installed)
RUN_TIME_LOCAL = "09:00"
RUN_DOW = "Sun"
RUN_TIMEZONE = "Asia/Jerusalem"  # follows the Mac's timezone; governs the trigger only
RUN_WEEKDAY = 6                  # Mon=0..Sun=6
RUN_HOUR, RUN_MINUTE = 9, 0

# The NYC 311 dataset's own timezone — every date-window computation and
# every $where timestamp sent to Socrata uses this, never RUN_TIMEZONE.
DATA_TZ = ZoneInfo("America/New_York")
GENERATION_TZ = ZoneInfo(RUN_TIMEZONE)

# --------------------------------------------------------------------- config
HERE = Path(__file__).resolve().parent
PROJECTS_ROOT = HERE.parent.parent          # for tidy relative paths in the log
REPORTS_DIR = PROJECTS_ROOT / "NYC311 Weekly Monitor" / "Reports"
REPORT_CHARTS_DIR = REPORTS_DIR / "charts"     # final, committed location
PREVIEW_FILE = HERE / "output" / "_preview.md"
CHARTS_STAGING_DIR = HERE / "output" / "charts"   # Claude's Bash cwd is HERE,
                                                  # so it saves charts here
LOG_FILE = HERE / "logs" / "run.log"

# So Claude's own Bash-invoked python/matplotlib calls (inheriting this
# process's environment) get a writable, non-interactive config dir instead
# of guessing — same convention as automation 03.
os.environ.setdefault("MPLCONFIGDIR", str(HERE / "output" / ".mplcache"))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

CLAUDE_BIN = "claude"
CLAUDE_MODEL = "sonnet"
CLAUDE_TIMEOUT_S = 2400          # generous: SoQL calls + analysis + a rigor-pass
                                 # skill invocation + an independent-verification
                                 # sub-agent call, all inside one session
CLAUDE_ALLOWED_TOOLS: list[str] = ["Bash", "Skill", "Agent"]   # Bash: Claude
                                 # fetches data itself. Skill: to invoke
                                 # analysis-rigor-pass on its own draft. Agent:
                                 # independent-verification spawns a fresh
                                 # sub-agent to check the report's own claims
                                 # (that's what makes it independent). Still no
                                 # Write/Edit — run.py is the only thing that
                                 # writes the report file.
HTTP_TIMEOUT_S = 30

SOCRATA_BASE = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"

# Verified live during planning (2026-09-14): no auth needed; fields include
# unique_key, created_date, closed_date, agency, agency_name, complaint_type,
# descriptor, location_type, incident_zip, incident_address, status,
# resolution_description, resolution_action_updated_date, community_board,
# council_district, police_precinct, borough, open_data_channel_type,
# park_facility_name, park_borough, latitude, longitude, location. Null
# fields are simply omitted from a record's JSON. created_date/closed_date
# are naive ISO timestamps ("2026-09-13T01:51:30.000") already in
# America/New_York — no offset conversion needed or wanted.

PROMPT_TEMPLATE = """\
You are producing a recurring weekly monitoring report on NYC 311 service
requests, for an analyst or manager who will skim it once a week. Be
selective and concise — this is NOT a comprehensive statistics dump. Surface
what actually matters: meaningful changes in volume, request categories,
geography, and other relevant dimensions vs. recent historical patterns;
anomalies or emerging trends; areas that warrant further investigation.

REPORTING WEEK (America/New_York, half-open interval):
  start (inclusive): {week_start_s}
  end   (exclusive): {week_end_s}

DATA SOURCE — NYC 311 Service Requests, Socrata SODA API, no auth needed:
  Endpoint: {socrata_base}
  Key fields: unique_key, created_date, closed_date, agency, agency_name,
    complaint_type, descriptor, location_type, incident_zip, status,
    borough, community_board, council_district, police_precinct,
    open_data_channel_type, latitude, longitude.
  created_date / closed_date are naive ISO timestamps already in
  America/New_York (no UTC offset) — use them exactly as given in $where,
  do NOT convert timezones. A field that is null is simply absent from a
  record's JSON.

  $where date-range example (use single quotes, no timezone suffix):
    $where=created_date >= '2026-09-07T00:00:00' AND created_date < '2026-09-14T00:00:00'

  $group aggregate example (prefer this over pulling raw rows — a single
  week is roughly 60-80k requests city-wide, so aggregate queries are both
  faster and sufficient for this report):
    {socrata_base}?$select=complaint_type,count(*) as cnt&$where=...&$group=complaint_type&$order=cnt DESC

  Fetch data with Bash (curl or a short python/urllib snippet) — you have
  Bash access for exactly this. Prefer several targeted $group/count queries
  (by complaint_type, borough, agency, day, open_data_channel_type, status,
  etc. — your call) over downloading raw rows.

COMPARISON BASELINE: you may query trailing weeks (roughly the last 8, your
judgment) purely as a comparison baseline for anomaly/trend detection —
e.g. week-over-week deltas, a rolling average or typical range per category
or borough to judge whether this week is actually unusual. Do NOT produce a
full independent analysis of any prior week; only pull the aggregate figures
you need for comparison.
  - HARD RULE: the period being measured must NEVER appear inside its own
    comparison baseline — not as a 1:1 comparison, and not as one element
    folded into a multi-week average. A "trailing 8-week baseline" for the
    reporting week means the 8 weeks strictly BEFORE it, always excluding
    the reporting week itself. Comparing something to a baseline that
    includes itself silently understates how unusual it is.
  - Any chart or table that shows data from a period OTHER than the
    reporting week above (a baseline week, a prior week used for context,
    etc.) must say so explicitly in its own title/heading/caption — never
    rely on the reader inferring which period a chart or table covers from
    context elsewhere in the report.

ANALYTICAL STANDARDS (apply throughout, this runs unattended so hold
yourself to these without a human checkpoint):
  - Tag findings as Observation / Hypothesis / Conclusion rather than
    blurring the three together.
  - Distinguish correlation from causation.
  - Decompose rate vs. mix, and disentangle correlated dimensions, before
    attributing a change to one cause (e.g. a category's raw count moving
    because of a citywide volume shift, not a change specific to it).
  - Surface data-quality issues, small-sample caveats, and coverage/window
    caveats up front, not buried at the end — e.g. requests created near the
    window's end are more likely to still show status "Open"/"Unspecified"
    and no closed_date yet; retroactive corrections/backfill in the source
    dataset are possible; geocoding gaps (missing lat/long or borough) can
    skew geographic breakdowns.

CHARTS: real chart images are the expected norm for this report, not an
optional extra — every other analytical deliverable in this project uses
real charts routinely, so their absence should be the rare exception, not
the default. If this week's data supports a meaningful trend, comparison,
or distribution (usually true — a multi-week volume trend, a category
comparison, a geographic breakdown), create at least one real chart for it.
Skip charts only if there is genuinely nothing chart-worthy this week.
  - Use Python + matplotlib via Bash: `import matplotlib;
    matplotlib.use("Agg")` before `import matplotlib.pyplot as plt`.
  - Save each chart as a PNG to: output/charts/{key}_<short-slug>.png
    (relative to your Bash working directory). Always use "{key}" as the
    literal filename prefix, so charts from different weeks never collide.
  - In the returned Markdown, reference each chart with a path relative to
    where the FINAL report file will live, not your working directory:
      ![<short description>](charts/{key}_<short-slug>.png)
    i.e. drop the "output/" prefix in the reference itself — the report and
    its charts/ folder will be co-located after this run.
  - Keep charts clean and readable at normal screen width: clear axis
    labels, legible font size, no clutter. A chart's title should state its
    takeaway (e.g. "Noise complaints hit an 8-week high"), not a generic
    label like "Weekly Volume."
  - Charts belong only in the Detailed Findings body (part 2 below), never
    in the Executive Summary.
  - If a chart fails to render for any reason, do not reference a broken
    image — note the gap in Caveats and continue with text/tables for that
    finding instead of failing the report.

VALIDATION — required before finalizing, do not skip:
  1. After drafting the full report (Executive Summary + Detailed Findings),
     invoke the `analysis-rigor-pass` skill against your own draft.
  2. Address every FIX finding it returns by revising the draft. (If it
     returns nothing to fix, that's fine — just note that below.)
  3. Pick the single highest-stakes conclusion in your draft and invoke the
     `independent-verification` skill on it.
  4. If its verdict is CONTRADICTED or HOLDS WITH CAVEAT, revise the draft
     to correct, soften, or add the caveat before finalizing.
  5. In "## Caveats & Data Quality", add one line stating both checks ran
     and their outcome, starting with the literal word "Validation:" —
     e.g. "Validation: rigor-pass run (2 fixes addressed); independent
     verification of '<claim>' → HOLDS." This line is required and will be
     checked; do not omit it or these steps.
  6. Only after all of this, output the final report text.

REQUIRED DELIVERABLE SHAPE — the report has exactly TWO top-level (# )
parts, clearly separated. Do not nest the second part under the first;
they are siblings, not parent/child. (Markdown, plain text only — no code
fences around the whole thing):

  PART 1 — the executive summary:
  1. Start on the very first line with exactly: # Executive Summary
  2. Immediately under that heading, before any other content, include
     these two lines verbatim (reproduce them exactly, character for
     character — they will be checked):
       Time range covered: {week_start_s} to {week_end_s} (America/New_York)
       Report generated: {generated_at_s}
       Total requests analyzed: {count_s}
     The third line is an independently-computed count, not yours to
     calculate — reproduce it exactly as given. Your own analysis should
     naturally arrive at the same figure; if your own count differs, that
     signals a query error on your end, not an error in this number.
  3. Then the executive summary itself: at most one page, brief and
     concise, the most important things only. Text only — no tables,
     charts, or visual elements here; that's what makes it an executive
     summary rather than the report itself.
     Format each distinct point as an unordered bullet (`- `) — not every
     single sentence needs its own bullet if two points genuinely belong
     together, but most points should be one bullet each, so a manager can
     jump from point to point rather than read solid prose. Each bullet
     opens with a short bold lead-in title naming what the point is about —
     verbal only, no numbers/percentages/figures in the title itself (those
     belong in the sentence that follows it) — so the reader can decide
     whether to read on without parsing numbers first. Example:
       - **Volume hit a multi-week high** — NYC 311 logged 77,696 requests
         this week, +4.9% above the 8-week baseline (z ≈ 4.3)... (Observation)

  Then a horizontal rule on its own line: ---

  PART 2 — the detailed findings (a real, separate section — never omit
  this part or fold it into part 1):
  4. A second top-level heading, exactly: # Detailed Findings
  5. This week's substantive findings go in a NUMBERED list (`1. `, `2. `,
     ...) — deliberately a different list style from the Executive
     Summary's bullets, so the two are never visually confused. Each
     numbered item is one finding; give it the same short, bold, verbal-only
     lead-in title as the Executive Summary bullets (e.g. "1. **Volume
     Trend**", "2. **What's Driving the Increase: Category Mix**"), then
     its full analysis, tables, and chart underneath. Use judgment on how
     many findings this week actually earns — don't pad to hit a number,
     don't compress to hit a number either. Different weeks should
     genuinely look different — don't default to repeating last week's
     list or framing out of habit; decide fresh each time. Real charts (see
     CHARTS above) are the primary visual tool here — a compact inline
     unicode sparkline (▁▂▃▄▅▆▇█) or trend arrow (▲▼▬) is a fine
     lightweight supplement inside a table, but not a substitute for a real
     chart where one is warranted.
  6. Standing/recurring report furniture — sections that will typically
     appear most weeks in some form, even though their content changes
     (e.g. Geography, and always Caveats & Data Quality, Data &
     Methodology) — are EXCLUDED from the numbered list. Give each its own
     plain `## ` heading (not numbered), placed after all the numbered
     findings. You won't always know in advance whether a section is truly
     "standing" vs. this week's specific finding; use your judgment, and
     default any section that reads as general reporting hygiene (not a
     specific finding about this week's data) to the unnumbered, trailing
     group. Always end with "## Data & Methodology" (queries + counts, so
     the report is self-auditing) and, just before it, "## Caveats & Data
     Quality" — both effectively guaranteed every week.

Output ONLY the finished Markdown report. No preamble, no commentary before
or after it, no code fences wrapping the whole document. This means: do not
write anything at all — not even a short sentence like "Here is the
report" or "All figures check out" — before the first line, which must be
exactly "# Executive Summary". Any text before that line, however brief,
will cause the report to be rejected outright. Do your checking/thinking
via Bash output during the process; your final message back is the report
and nothing else.
"""


# ------------------------------------------------------------------- helpers
# Set False by --dry-run so test runs never touch logs/run.log or state/.
_LOG_TO_FILE = True


def log(msg: str) -> None:
    print(msg)
    if not _LOG_TO_FILE:
        return
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    with LOG_FILE.open("a") as fh:
        fh.write(f"{stamp}  {msg}\n")


# ------------------------------------------------------------ failure handling
# Identical block to the other automations. Do not edit per-job.
STATE_FILE = HERE / "state" / "last_run.json"
_RUN: dict = {"status": "error", "detail": "run did not complete", "entry": None}


class GatherError(RuntimeError):
    """A data-gathering failure that aborts without touching real files."""


class _TransientHTTP(GatherError):
    """A retryable HTTP failure (5xx / connection). Still a GatherError."""


class ClaudeError(RuntimeError):
    """A retryable `claude` CLI failure (non-zero exit / timeout)."""


def _record(status: str, detail: str, entry: str | None = None) -> None:
    _RUN.update(status=status, detail=detail)
    if entry is not None:
        _RUN["entry"] = entry


def _retry(fn, *, attempts: int, base_delay: float, transient: tuple):
    for i in range(attempts):
        try:
            return fn()
        except transient as exc:
            if i == attempts - 1:
                raise
            wait = base_delay * (2 ** i) + random.uniform(0, base_delay)
            log(f"transient failure ({exc}); retry {i + 1}/{attempts - 1} "
                f"in {wait:.1f}s")
            time.sleep(wait)


def write_state(exit_code: int, started_at: str) -> None:
    prev: dict = {}
    try:
        prev = json.loads(STATE_FILE.read_text())
    except Exception:  # noqa: BLE001 - missing / unreadable is fine
        pass
    cf = int(prev.get("consecutive_failures", 0))
    cf = cf + 1 if _RUN["status"] == "error" else 0
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "automation": HERE.name,
        "started_at": started_at,
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        "status": _RUN["status"],
        "exit_code": exit_code,
        "entry": _RUN["entry"],
        "detail": _RUN["detail"],
        "consecutive_failures": cf,
    }, indent=2) + "\n")


# ------------------------------------------------------------------- http
def http_get_json(url: str, headers: dict | None = None):
    """GET + JSON with retry on 5xx / connection errors. Non-transient -> GatherError."""
    req = urllib.request.Request(url, headers=headers or {})

    def _once():
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (500, 502, 503, 504):
                raise _TransientHTTP(f"HTTP {exc.code} for {url}") from exc
            raise GatherError(f"HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise _TransientHTTP(f"network error for {url}: {exc.reason}") from exc

    return _retry(_once, attempts=3, base_delay=2, transient=(_TransientHTTP,))


# ------------------------------------------------------------------- window
def week_window(anchor_date: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """[week_start, week_end) — the calendar week ending at the most recent
    Sunday 00:00 America/New_York at/before anchor_date (anchor_date itself,
    if it is a Sunday)."""
    days_since_sunday = (anchor_date.weekday() - 6) % 7  # Mon=0..Sun=6
    week_end_date = anchor_date - dt.timedelta(days=days_since_sunday)
    week_end = dt.datetime.combine(week_end_date, dt.time(0, 0), tzinfo=DATA_TZ)
    week_start = week_end - dt.timedelta(days=7)
    return week_start, week_end


def soql_ts(d: dt.datetime) -> str:
    """Naive ISO string matching the dataset's own (offset-less) timestamps."""
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def fetch_week_count(week_start: dt.datetime, week_end: dt.datetime) -> int:
    where = (f"created_date >= '{soql_ts(week_start)}' "
             f"AND created_date < '{soql_ts(week_end)}'")
    url = SOCRATA_BASE + "?" + urllib.parse.urlencode(
        {"$select": "count(*) as cnt", "$where": where})
    data = http_get_json(url)
    return int(data[0]["cnt"]) if data else 0


# ---------------------------------------------------------------- analysis
def run_analysis(key: str, week_start_s: str, week_end_s: str, generated_at_s: str,
                  count_s: str, print_prompt: bool = False) -> str:
    prompt = PROMPT_TEMPLATE.format(
        key=key, week_start_s=week_start_s, week_end_s=week_end_s,
        generated_at_s=generated_at_s, count_s=count_s, socrata_base=SOCRATA_BASE)
    if print_prompt:
        print("\n----- PROMPT SENT TO CLAUDE -----")
        print(prompt)
        print("----- END PROMPT -----\n")

    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "text",
           "--permission-mode", "dontAsk", "--model", CLAUDE_MODEL,
           "--allowedTools", *CLAUDE_ALLOWED_TOOLS]

    def _call() -> str:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=CLAUDE_TIMEOUT_S, cwd=str(HERE))
        if p.returncode != 0:
            raise ClaudeError(f"claude CLI exited {p.returncode}\n{p.stderr.strip()}")
        return p.stdout

    text = _retry(_call, attempts=2, base_delay=5,
                  transient=(ClaudeError, subprocess.TimeoutExpired)).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rstrip().removesuffix("```").rstrip()

    _validate_report(text, week_start_s, week_end_s, generated_at_s, count_s)
    return text


_CHART_REF_RE = re.compile(r"\]\(charts/([^)\s]+\.png)\)")


def _validate_report(text: str, week_start_s: str, week_end_s: str,
                     generated_at_s: str, count_s: str) -> None:
    if not text.startswith("# Executive Summary"):
        raise RuntimeError(f"report did not start with '# Executive Summary':\n{text[:300]}")
    if week_start_s not in text or week_end_s not in text:
        raise RuntimeError("report is missing the required exact time-range strings")
    if generated_at_s not in text:
        raise RuntimeError("report is missing the required exact generation timestamp")
    if count_s not in text:
        raise RuntimeError(f"report is missing (or altered) the required independently-"
                           f"computed total request count: {count_s!r}")
    if "\n# Detailed Findings" not in text:
        raise RuntimeError("report is missing the required '# Detailed Findings' "
                           "section - it must be a real, separate body, not just "
                           "an executive summary")
    if "Validation:" not in text:
        raise RuntimeError("report is missing the required 'Validation:' line "
                           "confirming the rigor-pass and independent-verification "
                           "steps ran")
    if len(text) < 500:
        raise RuntimeError(f"report suspiciously short ({len(text)} chars) - likely truncated/failed")

    referenced = set(_CHART_REF_RE.findall(text))
    missing = [f for f in referenced if not (CHARTS_STAGING_DIR / f).exists()]
    if missing:
        raise RuntimeError(f"report references chart file(s) that were never "
                           f"generated: {missing}")
    if CHARTS_STAGING_DIR.exists():
        on_disk = {p.name for p in CHARTS_STAGING_DIR.glob("*.png")}
        orphans = on_disk - referenced
        if orphans:
            log(f"note: {len(orphans)} generated chart(s) not referenced in "
                f"the report: {sorted(orphans)}")


def _notify_failure(detail: str) -> None:
    """Best-effort native macOS notification on a real run's failure. Never
    lets a notification problem mask or replace the actual error."""
    try:
        msg = detail.replace('"', "'").replace("\\", "").replace("\n", " ")[:200]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg}" with title '
             f'"NYC 311 Weekly Monitor — run failed"'],
            timeout=10, capture_output=True)
    except Exception:  # noqa: BLE001 - notifying is never allowed to raise
        pass


def _reset_charts_staging() -> None:
    """Clean slate before every analysis attempt (dry-run or real), so a
    previous test's leftover charts can never be mistaken for this run's."""
    CHARTS_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    for p in CHARTS_STAGING_DIR.glob("*.png"):
        p.unlink()


def _finalize_charts(text: str) -> None:
    """Real-run only: copy this run's referenced charts into the deliverable
    repo, then clear the scratch staging dir and any stale dry-run preview."""
    referenced = _CHART_REF_RE.findall(text)
    if referenced:
        REPORT_CHARTS_DIR.mkdir(parents=True, exist_ok=True)
        for name in referenced:
            shutil.copy2(CHARTS_STAGING_DIR / name, REPORT_CHARTS_DIR / name)
    for p in CHARTS_STAGING_DIR.glob("*.png"):
        p.unlink()
    if PREVIEW_FILE.exists():
        PREVIEW_FILE.unlink()


# ---------------------------------------------------------------------- main
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate this week's NYC 311 monitoring report.")
    p.add_argument("--date", metavar="YYYY-MM-DD",
                   help="pretend today is this date instead of the real current "
                        "date, for testing/backfill (America/New_York)")
    p.add_argument("--dry-run", action="store_true",
                   help="write output/_preview.md only; real Reports/ untouched")
    p.add_argument("--print-prompt", action="store_true",
                   help="also print the exact prompt sent to the claude CLI")
    a = p.parse_args(argv)
    if a.date:
        try:
            a.date_obj = dt.date.fromisoformat(a.date)
        except ValueError:
            p.error(f"--date must be YYYY-MM-DD, got {a.date!r}")
    else:
        a.date_obj = None
    return a


def main(argv: list[str] | None = None) -> int:
    global _LOG_TO_FILE
    args = parse_args(argv)
    if args.dry_run:
        _LOG_TO_FILE = False

    anchor_date = args.date_obj or dt.datetime.now(DATA_TZ).date()
    week_start, week_end = week_window(anchor_date)
    key = week_start.strftime("%y_%m_%d")
    week_start_s, week_end_s = soql_ts(week_start), soql_ts(week_end)
    report_path = REPORTS_DIR / f"weekly_service_requests_{key}.md"

    mode = "DRY RUN" if args.dry_run else "run"
    log(f"{mode} start - week {key} ({week_start_s} -> {week_end_s} America/New_York)")

    # 1. duplicate check — checked against the REAL report path even on
    #    --dry-run, since this is one of the required guardrails, not part
    #    of what --dry-run is meant to sandbox.
    if report_path.exists():
        msg = f"Last report is already available here: {report_path}"
        log(f"SKIPPED (duplicate) - {msg}")
        _record("skipped_duplicate", msg, entry=key)
        return 0

    # 2. no-new-data check (fetch_week_count -> http_get_json already retries
    #    internally; do not wrap it in a second _retry here too - that was a
    #    real bug (nested retries -> up to 9 attempts instead of 3).
    try:
        count = fetch_week_count(week_start, week_end)
    except GatherError as exc:
        log(f"ERROR checking data availability: {exc}")
        _record("error", str(exc), entry=key)
        return 1

    if count == 0:
        msg = f"No NYC 311 data found for {week_start_s} to {week_end_s}; nothing to report."
        log(f"SKIPPED (no data) - {msg}")
        _record("skipped_no_data", msg, entry=key)
        return 0
    log(f"data available - {count} requests found for the week")

    # 3. analysis (Claude fetches, analyzes, and writes the report text)
    _reset_charts_staging()
    generated_at_s = dt.datetime.now(GENERATION_TZ).strftime(f"%Y-%m-%d %H:%M {RUN_TIMEZONE}")
    count_s = f"{count:,}"
    try:
        report_text = run_analysis(key, week_start_s, week_end_s, generated_at_s,
                                   count_s, print_prompt=args.print_prompt)
    except Exception as exc:  # noqa: BLE001 - keep the real Reports/ folder intact
        log(f"ERROR generating report: {exc}")
        _record("error", str(exc), entry=key)
        return 1

    if args.dry_run:
        PREVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREVIEW_FILE.write_text(report_text)
        log(f"dry run - real Reports/ NOT touched; preview at "
            f"{PREVIEW_FILE.relative_to(PROJECTS_ROOT)} ({len(report_text)} bytes)")
        return 0

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text)
    _finalize_charts(report_text)
    detail = f"wrote {report_path.relative_to(PROJECTS_ROOT)} ({len(report_text)} bytes, {count} requests)"
    log(detail)
    _record("ok", detail, entry=key)
    return 0


if __name__ == "__main__":
    _started = dt.datetime.now().isoformat(timespec="seconds")
    _code, _ran = 1, False
    try:
        _code = main()
        _ran = True
    except Exception as exc:  # noqa: BLE001 - record, then re-raise the exit
        _record("error", f"uncaught: {exc!r}")
        traceback.print_exc()
        _ran = True
    finally:
        # skip on --help / bad args (SystemExit, _ran stays False) and on
        # --dry-run (_LOG_TO_FILE is False)
        if _ran and _LOG_TO_FILE:
            write_state(_code, _started)
            if _RUN["status"] == "error":
                _notify_failure(_RUN["detail"])
    raise SystemExit(_code)

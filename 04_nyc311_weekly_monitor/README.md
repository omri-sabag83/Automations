# 04 — NYC 311 Weekly Monitor

Every Sunday 09:00, produces one new **weekly monitoring report** on NYC 311
service requests and writes it to a **separate deliverable repo**,
[`../../NYC311 Weekly Monitor/Reports/`](../../NYC311%20Weekly%20Monitor/Reports/),
as `weekly_service_requests_YY_MM_DD.md` (the date is the report's own week
start — the previous Sunday). One file per week, never aggregated, never
pruned.

This is the first automation where Claude, not `run.py`, does the actual data
pull and analysis — see *Engine* below for why, and how the guardrails around
it work.

Each report has exactly two top-level parts:

1. **`# Executive Summary`** — at most one page, text only (no tables or
   visuals). Starts with the exact time range covered and the exact
   generation timestamp, then the most important findings as **unordered
   bullets**, each opening with a short bold, verbal-only (no figures) lead
   title so a reader can triage before reading the sentence.
2. **`# Detailed Findings`** — a real, separate body, never folded into the
   summary above. This week's specific findings form a **numbered list**
   (`1.`, `2.`, ...) — deliberately different from the Executive Summary's
   bullets — each item similarly titled with a short bold lead-in, then its
   full analysis/tables/chart. No fixed count or fixed section list:
   different weeks are expected to look different, not follow a repeated
   template. Real **matplotlib charts** are the expected norm here (every
   other analytical deliverable in this workspace uses them routinely) —
   Claude generates them itself via Bash and `run.py` copies them alongside
   the report; a compact inline unicode sparkline/arrow is a fine
   supplement inside a table but not a substitute for a real chart.
   Standing/recurring report furniture — Geography if it recurs, and always
   **`## Caveats & Data Quality`** and **`## Data & Methodology`** (the
   exact Socrata queries run and the record counts they returned, so the
   report is self-auditing) — stays unnumbered and trailing, after all the
   numbered findings.

`_validate_report()` in `run.py` enforces both top-level headings are
present (and in order) before a report is ever written — a report that's
only an executive summary fails validation rather than shipping.

The report itself lives in the other repo (it's the actual deliverable / a
portfolio artifact); this folder is only the orchestration.

## Schedule

**Source of truth:** the `# schedule` block at the top of `run.py`
(`RUN_MODE`, `RUN_DOW`, `RUN_TIME_LOCAL`, `RUN_TIMEZONE`). This section must
match it.

| Setting | Value |
|---|---|
| Mode | **Scheduled** — macOS launch agent `com.omrisabag.nyc311-weekly-monitor` |
| Day / time | **Sunday 09:00**, system-local (the Mac is on Asia/Jerusalem; the time follows the Mac's timezone) — this governs only *when the job fires*, not the data window (see *How the window works*) |
| Also runnable by hand | `python run.py` (and the `--dry-run` / `--date` / `--print-prompt` test flags) |

**To change the day or time, ask Claude** — it updates the `run.py` block,
`schedule/com.omrisabag.nyc311-weekly-monitor.plist`, and this table together.

## Run it

```bash
cd "Automations/04_nyc311_weekly_monitor"
python run.py
```

- **Safe to re-run.** Re-running the same week is a no-op: the duplicate
  guardrail detects the existing report file and stops before any API or
  `claude` call.
- On a data-check or `claude` failure, no report file is written and the
  script exits non-zero, with the reason in [`logs/run.log`](logs/run.log)
  and `state/last_run.json`.

## Testing on demand

| Command | What it does |
|---|---|
| `python run.py --dry-run` | Runs the real guardrails and the real Claude analysis against live data, then writes the report to `output/_preview.md` instead of the real `Reports/` folder, and stops. (The duplicate check still checks the real path — that guardrail isn't sandboxed.) |
| `python run.py --date 2026-09-14` | Pretends today is that date; the analyzed week is worked out from it. Point it at a week with real 311 activity — any past week qualifies — to judge output; also how you'd backfill a missed run. |
| `python run.py --print-prompt` | Also dumps the exact prompt sent to `claude`. Use while tuning `PROMPT_TEMPLATE`. |

Combine freely, e.g. `python run.py --dry-run --date 2026-09-14 --print-prompt`.

## How the window works

The analyzed week is always a **calendar week in America/New_York** — the
NYC 311 dataset's own timezone — independent of the Mac's timezone or when
launchd actually fires the job. `week_end` = the most recent Sunday 00:00
America/New_York at/before the anchor (`--date`, or today); `week_start =
week_end − 7 days`. The report filename's date is `week_start`. Because this
is a pure function of the calendar date, a Sunday scheduled run and a
Monday/Tuesday manual re-run resolve to the identical week and filename —
that's what makes the duplicate check a plain file-existence check.

## Engine — why this one is different

Every other automation here follows "run.py computes every number, the model
only writes prose." This one deliberately doesn't, for its analytical core:
the point of this job is to see Claude retrieve live data, decide the
processing steps, and perform the analysis itself, unattended — not narrate
a digest `run.py` already computed.

- `run.py` keeps ownership of what has to stay deterministic: the date
  window, two guardrails (below), retries, output validation, and
  `state/last_run.json`.
- Claude (`claude -p`, with `--allowedTools Bash`, no `Write`/`Edit`) does
  the rest: fetches the week's data itself from the Socrata API (`curl` /
  `urllib` via Bash), decides what dimensions and comparison baseline to
  use, judges what's anomalous, and writes the report. It returns **only**
  the finished Markdown as stdout — `run.py` is still the only thing that
  ever writes the report file, so file-write control stays centralized even
  though the analysis is delegated.
- `run.py` validates that output before writing it: must start with
  `# Executive Summary`, must contain the exact time-range and
  generation-timestamp strings it was handed (so those two facts stay
  accurate even though the rest of the writeup doesn't), must clear a
  minimum length, and every `charts/....png` image it references must
  actually exist. Any failure is treated as a run error — no partial file.

**Charts, mechanically:** Claude saves PNGs to `output/charts/` (its Bash
working directory) during generation, but references them in the returned
Markdown as `charts/<file>.png` — relative to where the *final* report will
live, not where it's generating them. `output/charts/` is cleared before
every analysis attempt (dry-run or real), so a previous test's leftovers can
never be mistaken for the current run's. On a successful **real** run,
`run.py` copies every referenced PNG into
`NYC311 Weekly Monitor/Reports/charts/` (so the reference resolves from the
report's real location), then clears the scratch copies and any stale
`output/_preview.md` — nothing scratch is left sitting next to a just-published
report. On `--dry-run`, the charts simply stay in `output/charts/`, which
already sits next to `output/_preview.md`, so the reference resolves in
place for inspection.

## Guardrails (before any `claude` call)

1. **Duplicate check** — if this week's report file already exists, log
   `Last report is already available here: {path}`, record
   `status: "skipped_duplicate"`, exit 0.
2. **No-new-data check** — one cheap `$select=count(*)` Socrata query for the
   week. If `0`, log it, record `status: "skipped_no_data"`, exit 0, and
   **write no file** — so a later retry can still succeed if this was source
   lag rather than a genuinely empty week.

## Data source

[NYC 311 Service Requests from 2020 to Present](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-to-Present/erm2-nwe9/about_data)
— Socrata SODA API, `https://data.cityofnewyork.us/resource/erm2-nwe9.json`,
**unauthenticated** (no token needed, no rate-limit concerns at one run/week).
A single week runs ~60–80k requests city-wide, so both `run.py`'s own count
check and Claude's own analysis queries use SoQL `$group`/aggregate queries
rather than pulling raw rows.

## Failure handling

- **Fail-safe & idempotent** — the report is written only after a
  successful, validated generation; re-running a week is a guarded no-op.
- **Retry with backoff** — the Socrata count check retries up to 3× on `5xx`
  / connection errors; the `claude` call retries once. Malformed/invalid
  model output (fails the validation checks above) is not retried — it's
  recorded as an error for review.
- **`state/last_run.json`** — written on every real run (not `--dry-run`):

  ```json
  {
    "automation": "04_nyc311_weekly_monitor",
    "started_at": "...", "finished_at": "...",
    "status": "ok" | "skipped_duplicate" | "skipped_no_data" | "error",
    "exit_code": 0,
    "entry": "26_09_07",
    "detail": "wrote NYC311 Weekly Monitor/Reports/weekly_service_requests_26_09_07.md (...)",
    "consecutive_failures": 0
  }
  ```

  `consecutive_failures` increments only on `error` and resets otherwise
  (including on the two `skipped_*` states — they aren't failures). **No
  alerting yet** — check this file or `logs/run.log`.

## How it's scheduled

A launch agent, [`schedule/com.omrisabag.nyc311-weekly-monitor.plist`](schedule/com.omrisabag.nyc311-weekly-monitor.plist):
`StartCalendarInterval` Weekday 0 (Sunday) 09:00, absolute paths, a `PATH` so
`python` / `claude` resolve, stdout/stderr to `logs/launchd.{out,err}.log`. A
slot missed while asleep runs on the next wake.

**Install / re-install:**

```bash
cp schedule/com.omrisabag.nyc311-weekly-monitor.plist ~/Library/LaunchAgents/
launchctl bootout  gui/$(id -u)/com.omrisabag.nyc311-weekly-monitor 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.omrisabag.nyc311-weekly-monitor.plist
```

**Verify without waiting for Sunday:**

```bash
launchctl kickstart -k gui/$(id -u)/com.omrisabag.nyc311-weekly-monitor
launchctl print gui/$(id -u)/com.omrisabag.nyc311-weekly-monitor | grep -iA2 'last exit'
cat logs/launchd.err.log        # expect empty
```

**Uninstall:**

```bash
launchctl bootout gui/$(id -u)/com.omrisabag.nyc311-weekly-monitor
rm ~/Library/LaunchAgents/com.omrisabag.nyc311-weekly-monitor.plist
```

## Files

| Path | What |
|---|---|
| `run.py` | The automation. |
| `schedule/com.omrisabag.nyc311-weekly-monitor.plist` | The launch-agent definition. |
| `output/_preview.md` | `--dry-run` scratch output. Local only (gitignored); deleted automatically after the next successful real run. |
| `output/charts/` | Chart-generation scratch/staging (cleared before every run). Local only (gitignored). |
| `logs/run.log`, `logs/launchd.{out,err}.log` | Run + agent logs. Local only (gitignored). |
| `state/last_run.json` | Last run's status. Local only (gitignored). |
| [`../../NYC311 Weekly Monitor/Reports/`](../../NYC311%20Weekly%20Monitor/Reports/) | **The actual deliverable** — one Markdown report per week, plus a `charts/` folder of the PNGs they reference. Lives in a separate repo, not here. |

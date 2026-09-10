#!/usr/bin/env python3
"""
Automation #02 — GitHub weekly activity summary.

Scheduled by a launch agent (schedule/com.omrisabag.github-weekly-summary.plist)
every Thursday at 22:00. Can also be run by hand:

    python run.py                       # summarise the past week, write the log
    python run.py --dry-run             # write output/_preview.md only; real log untouched
    python run.py --date 2026-09-10     # anchor the week to that date @ 22:00 (for testing)
    python run.py --print-prompt        # also show the exact prompt sent to Claude

Each run:
  1. Works out the week window: [anchor - 7 days, anchor), where anchor is the
     most recent Thursday 22:00 local (or --date @ 22:00).
  2. Pulls that week's activity from the GitHub REST API for every repo the
     user owns: commits, plus issues / PRs updated in the window.
  3. Asks Claude (via the `claude` CLI) to turn the raw digest into a judged
     summary - snapshot, per-repo breakdown, issues/PRs, notable, a short read.
  4. Prepends that entry to output/github_weekly_summary.md (newest first),
     replacing any existing entry for the same week so re-runs are safe.

Nothing is installed - stdlib only, plus the existing `claude` CLI. The GitHub
API is called unauthenticated (all repos are public); set GITHUB_TOKEN in the
environment or a gitignored .env here to raise rate limits / see private repos.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import subprocess
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ------------------------------------------------------------------- schedule
# SINGLE SOURCE OF TRUTH for when this runs. Keep it in sync with both READMEs.
# Scheduled by a macOS launch agent:
#     schedule/com.omrisabag.github-weekly-summary.plist -> ~/Library/LaunchAgents/
#     fires `python run.py` every Thursday 22:00 system-local (Asia/Jerusalem).
# `python run.py` by hand still works, for manual and --dry-run testing.
# To change the day/time, ask Claude to update this block, the .plist, and both
# READMEs together.
RUN_MODE = "scheduled"          # "manual" | "scheduled" (launchd agent installed)
RUN_TIME_LOCAL = "22:00"         # HH:MM, 24h
RUN_DOW = "Thu"                  # day of week
RUN_TIMEZONE = "Asia/Jerusalem"  # Israel local time (follows the Mac's timezone)

RUN_WEEKDAY = 3                  # Mon=0 .. Thu=3  (must match RUN_DOW)
RUN_HOUR, RUN_MINUTE = 22, 0

# --------------------------------------------------------------------- config
HERE = Path(__file__).resolve().parent
PROJECTS_ROOT = HERE.parent.parent  # ~/Documents/Python_Projects (for log paths)
OUTPUT_FILE = HERE / "output" / "github_weekly_summary.md"
PREVIEW_FILE = HERE / "output" / "_preview.md"  # --dry-run target; gitignored
LOG_FILE = HERE / "logs" / "run.log"

GITHUB_USER = "omri-sabag83"
REPOS = None                    # None = every non-archived repo the user owns
GITHUB_API = "https://api.github.com"
HTTP_TIMEOUT_S = 30

CLAUDE_BIN = "claude"
CLAUDE_MODEL = "sonnet"
CLAUDE_TIMEOUT_S = 600

COMMIT_CAP = 40                 # commit subjects listed per repo in the digest
ISSUE_CAP = 20                  # issue / PR lines listed per repo
MAX_DIGEST_CHARS = 14000        # hard cap on the text handed to Claude

INTRO = """\
# GitHub Weekly Activity Summary

A running log, newest entry first, generated every Thursday 22:00 by
`Automations/02_github_weekly_summary/run.py`. Each entry covers the preceding
seven days across the owner's GitHub repositories: a snapshot, a per-repo
breakdown, issues / PRs worth noting, anything notable, and a short read of
where the work is heading.

---
"""

PROMPT_TEMPLATE = """\
You are writing this week's entry for a personal log that tracks a data \
analyst's GitHub activity. The week being summarised ran from {start} to {end} \
({tz}). The "week ending" date is {anchor_date}.

Produce ONE Markdown entry and nothing else - no preamble, no code fences. \
Start on the very first line with this exact heading:

## Week ending {anchor_date}

Then exactly these sections, in this order, with these headings:

### Snapshot
One line, from the TOTALS below: commits, active repos, issues, PRs.

### By repository
For each repo that had activity: a line `**<repo>** - <N> commits`, then 1-3 \
bullets describing what actually changed in judgement terms (features, fixes, \
docs, new work, refactors) - not a paraphrase of every commit message. If a \
repo saw only trivial churn (a formatting pass, a typo fix), fold it into a \
single line instead of its own block. Write each repo name as plain bold \
`**<repo>**` - a later step turns it into a link; do not add your own links.

### Issues & PRs
Only what is worth noting - opened, merged, closed, or real discussion. If \
nothing is noteworthy, write exactly: _None noteworthy this week._

### Notable
Cross-cutting things: a new project, a release, a big refactor, a visible shift \
in focus. If nothing stands out, write exactly: _Nothing stands out._

### Read
2-4 sentences - your concise interpretation of what this week's activity says \
about where the work is heading. Plain and honest, not promotional.

End the entry with a line containing only three dashes:

---

--- TOTALS ---
{totals}

--- ACTIVITY DATA ({start} to {end}) ---
{digest}
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


class GatherError(RuntimeError):
    """A GitHub API / network failure that should abort without touching files."""


class _TransientHTTP(GatherError):
    """A retryable GitHub failure (5xx / connection error). Still a GatherError."""


# ------------------------------------------------------------ failure handling
# Identical block across automations 01 / 02 / 03 (01 omits _TransientHTTP).
STATE_FILE = HERE / "state" / "last_run.json"
_RUN: dict = {"status": "error", "detail": "run did not complete", "entry": None}


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


def most_recent_anchor(now: dt.datetime) -> dt.datetime:
    """The most recent Thursday 22:00 local, with a 1 h grace either side."""
    days_since = (now.weekday() - RUN_WEEKDAY) % 7
    anchor = (now - dt.timedelta(days=days_since)).replace(
        hour=RUN_HOUR, minute=RUN_MINUTE, second=0, microsecond=0
    )
    if anchor > now + dt.timedelta(hours=1):
        anchor -= dt.timedelta(days=7)
    return anchor


def _github_token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok.strip()
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _gh_get(path: str, params: dict | None = None) -> tuple[object, str]:
    url = GITHUB_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": f"automations-github-weekly-summary ({GITHUB_USER})",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    token = _github_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    def _once() -> tuple[object, str]:
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode()), resp.headers.get("Link", "")
        except urllib.error.HTTPError as exc:
            if exc.code in (500, 502, 503, 504):
                raise _TransientHTTP(f"GitHub API {exc.code} for {path}") from exc
            extra = ""
            if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
                extra = (f" (unauthenticated rate limit exhausted; resets at epoch "
                         f"{exc.headers.get('X-RateLimit-Reset', '?')})")
            raise GatherError(f"GitHub API {exc.code} for {path}{extra}") from exc
        except urllib.error.URLError as exc:
            raise _TransientHTTP(f"network error for {path}: {exc.reason}") from exc

    return _retry(_once, attempts=3, base_delay=2, transient=(_TransientHTTP,))


def _gh_paged(path: str, params: dict | None = None) -> list:
    params = dict(params or {})
    params.setdefault("per_page", 100)
    out: list = []
    page = 1
    while page <= 10:  # safety ceiling
        params["page"] = page
        data, link = _gh_get(path, params)
        if not isinstance(data, list):
            break
        out.extend(data)
        if 'rel="next"' not in link or len(data) < params["per_page"]:
            break
        page += 1
    return out


def _iso_utc(local: dt.datetime) -> str:
    return local.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gather_activity(start: dt.datetime, end: dt.datetime) -> tuple[str, dict, list, list]:
    start_iso, end_iso = _iso_utc(start), _iso_utc(end)

    if REPOS:
        repo_names = list(REPOS)
    else:
        repos = _gh_paged(f"/users/{GITHUB_USER}/repos", {"sort": "pushed", "type": "owner"})
        repo_names = [r["name"] for r in repos if not r.get("archived")]

    totals = {"commits": 0, "repos_active": 0, "issues": 0, "prs": 0}
    blocks: list[str] = []
    active: list[str] = []
    failed: list[str] = []

    for name in sorted(repo_names):
        try:
            commits = _gh_paged(
                f"/repos/{GITHUB_USER}/{name}/commits",
                {"since": start_iso, "until": end_iso},
            )
            issues = _gh_paged(
                f"/repos/{GITHUB_USER}/{name}/issues",
                {"since": start_iso, "state": "all", "sort": "updated"},
            )
        except GatherError as exc:
            log(f"[{name}] skipped: {exc}")
            failed.append(name)
            continue
        issues = [i for i in issues if i.get("updated_at", "") < end_iso]
        prs = [i for i in issues if "pull_request" in i]

        if not commits and not issues:
            continue

        active.append(name)
        totals["commits"] += len(commits)
        totals["repos_active"] += 1
        totals["issues"] += len(issues) - len(prs)
        totals["prs"] += len(prs)

        lines = [f"### repo: {name}", f"commits: {len(commits)}"]
        for c in commits[:COMMIT_CAP]:
            commit = c.get("commit", {})
            subject = (commit.get("message", "") or "").splitlines()[0]
            day = (commit.get("author", {}) or {}).get("date", "")[:10]
            lines.append(f"- {day} {c.get('sha', '')[:7]} {subject}")
        if len(commits) > COMMIT_CAP:
            lines.append(f"- ... (+{len(commits) - COMMIT_CAP} more commits)")
        for i in issues[:ISSUE_CAP]:
            kind = "PR" if "pull_request" in i else "issue"
            lines.append(
                f"- {kind} #{i['number']} [{i.get('state', '?')}] "
                f"opened {i.get('created_at', '')[:10]} "
                f"updated {i.get('updated_at', '')[:10]}: {i.get('title', '')}"
            )
        if len(issues) > ISSUE_CAP:
            lines.append(f"- ... (+{len(issues) - ISSUE_CAP} more issues/PRs)")
        blocks.append("\n".join(lines))

    if failed and len(failed) == len(repo_names):
        raise GatherError(f"all {len(failed)} repos failed to fetch: {', '.join(failed)}")

    digest = "\n\n".join(blocks) if blocks else "(no repository activity in the window)"
    if len(digest) > MAX_DIGEST_CHARS:
        digest = digest[:MAX_DIGEST_CHARS] + "\n... (digest truncated)"
    if failed:
        digest = f"PARTIAL: could not fetch {', '.join(failed)}\n\n" + digest
    return digest, totals, active, failed


def link_repo_names(entry: str, repo_names: list[str]) -> str:
    """Turn bold repo names (`**name**`) into underlined links to the repo."""
    if not repo_names:
        return entry
    pattern = re.compile(
        r"\*\*(" + "|".join(re.escape(n) for n in repo_names) + r")\*\*"
    )
    return pattern.sub(
        lambda m: f"**[<ins>{m.group(1)}</ins>]"
                  f"(https://github.com/{GITHUB_USER}/{m.group(1)})**",
        entry,
    )


def generate_entry(
    anchor_date: str, start: str, end: str, totals: str, digest: str,
    print_prompt: bool = False,
) -> str:
    prompt = PROMPT_TEMPLATE.format(
        anchor_date=anchor_date, start=start, end=end, tz=RUN_TIMEZONE,
        totals=totals, digest=digest,
    )
    if print_prompt:
        print("\n----- PROMPT SENT TO CLAUDE -----")
        print(prompt)
        print("----- END PROMPT -----\n")

    def _call() -> subprocess.CompletedProcess:
        p = subprocess.run(
            [
                CLAUDE_BIN, "-p", prompt,
                "--output-format", "text",
                "--permission-mode", "dontAsk",
                "--model", CLAUDE_MODEL,
            ],
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S, cwd=str(HERE),
        )
        if p.returncode != 0:
            raise ClaudeError(f"claude CLI exited {p.returncode}\n{p.stderr.strip()}")
        return p

    proc = _retry(_call, attempts=2, base_delay=5,
                  transient=(ClaudeError, subprocess.TimeoutExpired))

    entry = proc.stdout.strip()
    if entry.startswith("```"):
        entry = entry.split("\n", 1)[1] if "\n" in entry else ""
        entry = entry.rstrip()
        if entry.endswith("```"):
            entry = entry[:-3].rstrip()
    if not entry.startswith(f"## Week ending {anchor_date}"):
        raise RuntimeError(f"unexpected entry start:\n{entry[:300]}")
    return entry


def _drop_existing_entry(entries_block: str, anchor_date: str) -> str:
    out: list[str] = []
    skipping = False
    for line in entries_block.splitlines():
        if re.match(r"^## Week ending \d{4}-\d{2}-\d{2}\b", line):
            skipping = line.startswith(f"## Week ending {anchor_date}")
        if not skipping:
            out.append(line)
    return "\n".join(out).strip("\n")


def upsert_entry(entry: str, anchor_date: str) -> str:
    content = OUTPUT_FILE.read_text() if OUTPUT_FILE.exists() else INTRO

    marker = "\n---\n"
    idx = content.find(marker)
    if idx == -1:
        head, rest = INTRO, ""
    else:
        head = content[: idx + len(marker)]
        rest = content[idx + len(marker):].lstrip("\n")

    rest = _drop_existing_entry(rest, anchor_date)

    parts = [head.rstrip(), "", entry.rstrip()]
    if rest:
        parts += ["", rest.rstrip()]
    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------- main
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the weekly GitHub activity summary.",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help=f"anchor the week to this date @ {RUN_TIME_LOCAL} local instead of "
             f"the most recent scheduled slot (for testing)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="write output/_preview.md only; touch no real files",
    )
    parser.add_argument(
        "--print-prompt", action="store_true",
        help="also print the exact prompt sent to the claude CLI",
    )
    args = parser.parse_args(argv)
    if args.date:
        try:
            args.date_obj = dt.date.fromisoformat(args.date)
        except ValueError:
            parser.error(f"--date must be YYYY-MM-DD, got {args.date!r}")
    else:
        args.date_obj = None
    return args


def main(argv: list[str] | None = None) -> int:
    global _LOG_TO_FILE
    args = parse_args(argv)
    if args.dry_run:
        _LOG_TO_FILE = False

    if args.date_obj:
        anchor = dt.datetime.combine(args.date_obj, dt.time(RUN_HOUR, RUN_MINUTE))
    else:
        anchor = most_recent_anchor(dt.datetime.now())
    start = anchor - dt.timedelta(days=7)
    anchor_date = anchor.date().isoformat()
    start_s, end_s = start.strftime("%Y-%m-%d %H:%M"), anchor.strftime("%Y-%m-%d %H:%M")

    mode = "DRY RUN" if args.dry_run else "run"
    log(f"{mode} start - week ending {anchor_date} ({start_s} -> {end_s} {RUN_TIMEZONE})")

    try:
        digest, totals, active, failed = gather_activity(start, anchor)
    except GatherError as exc:
        log(f"ERROR gathering GitHub activity: {exc}")
        _record("error", str(exc), entry=anchor_date)
        return 1

    tot_line = (f"{totals['commits']} commits, {totals['repos_active']} active repos, "
                f"{totals['issues']} issues, {totals['prs']} PRs")
    log(f"gathered - {tot_line}; digest {len(digest)} chars"
        + (f"; PARTIAL ({len(failed)} repo(s) failed: {', '.join(failed)})" if failed else ""))

    try:
        entry = generate_entry(
            anchor_date, start_s, end_s, tot_line, digest,
            print_prompt=args.print_prompt,
        )
    except Exception as exc:  # noqa: BLE001 - keep the existing file intact
        log(f"ERROR: {exc}")
        _record("error", str(exc), entry=anchor_date)
        return 1

    entry = link_repo_names(entry, active)
    new_body = upsert_entry(entry, anchor_date)

    if args.dry_run:
        PREVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREVIEW_FILE.write_text(new_body)
        log(f"dry run - {OUTPUT_FILE.name} NOT touched; full preview at "
            f"{PREVIEW_FILE.relative_to(PROJECTS_ROOT)} ({len(new_body)} bytes) "
            f"- open it in Markdown preview")
        return 0

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(new_body)
    detail = f"wrote {OUTPUT_FILE.relative_to(PROJECTS_ROOT)} ({len(new_body)} bytes)"
    if failed:
        detail += f"; PARTIAL - {len(failed)} repo(s) not fetched: {', '.join(failed)}"
    log(detail)
    _record("partial" if failed else "ok", detail, entry=anchor_date)
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
        # skip on --help / bad args (SystemExit from argparse, _ran stays False)
        # and on --dry-run (_LOG_TO_FILE is False)
        if _ran and _LOG_TO_FILE:
            write_state(_code, _started)
    raise SystemExit(_code)

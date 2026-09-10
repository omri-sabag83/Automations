# 02 — GitHub Weekly Summary

Every Thursday 22:00, summarises the previous seven days of activity across the
owner's GitHub repositories and **prepends** a dated entry to a running log:
`output/github_weekly_summary.md` (newest first).

Each entry has five sections:

1. **Snapshot** — one line: commits, active repos, issues, PRs.
2. **By repository** — per active repo, a judged 1–3 bullet read of what changed
   (not a commit-message dump).
3. **Issues & PRs** — only what's noteworthy, else *None noteworthy this week.*
4. **Notable** — new projects, releases, big refactors, a shift in focus.
5. **Read** — 2–4 sentences on where the work is heading.

The running log itself is **not** in this repo (`output/` is gitignored, to keep
every automation's output handling uniform). Redacted sample of one entry:

```markdown
## Week ending 2026-05-14

### Snapshot
23 commits across 3 repos · 2 PRs · 0 issues.

### By repository
**portfolio-site** — 14 commits
- Reworked the projects page into a filterable grid; added three case-study writeups.
- Fixed the dark-mode contrast on code blocks.

**data-utils** — 9 commits
- New `read_any()` loader that sniffs CSV / Parquet / Excel from the extension.
- Test coverage for the date-parsing helpers.

### Issues & PRs
- PR #12 (merged): `read_any()` loader.
- PR #13 (open): draft of the caching layer — discussion ongoing.

### Notable
First case-study content on the portfolio site; the utils repo is starting to
look like a real package rather than a scratchpad.

### Read
Two clear threads this week: making past work presentable, and hardening the
helper code that supports it. The PR discussion suggests the caching layer is
the next real feature rather than more polish.

---
```

## Schedule

**Source of truth:** the `# schedule` block at the top of `run.py`
(`RUN_MODE`, `RUN_DOW`, `RUN_TIME_LOCAL`, `RUN_TIMEZONE`). This section must
match it.

| Setting | Value |
|---|---|
| Mode | **Scheduled** — macOS launch agent `com.omrisabag.github-weekly-summary` |
| Day / time | **Thursday 22:00**, system-local (the Mac is on Asia/Jerusalem; the time follows the Mac's timezone) |
| Also runnable by hand | `python run.py` (and the `--dry-run` / `--date` test flags) |

**To change the day or time, ask Claude** — it updates the `run.py` block,
`schedule/com.omrisabag.github-weekly-summary.plist`, and this table together.

## Run it

```bash
cd Automations/02_github_weekly_summary
python run.py
```

- **Safe to re-run.** A second run for the same week replaces that week's entry
  instead of adding a duplicate.
- If the GitHub API or the `claude` call fails (rate limit, network, auth), the
  existing log is left untouched and the script exits non-zero with the error
  in [`logs/run.log`](logs/run.log).

## Testing on demand

| Command | What it does |
|---|---|
| `python run.py --dry-run` | Full run — pulls the week from GitHub, calls Claude — then writes the **complete would-be file** to `output/_preview.md` and stops. The real log and `logs/run.log` are untouched. Open `_preview.md` in the Markdown preview. |
| `python run.py --date 2026-09-10` | Anchors the week to 2026-09-10 @ 22:00, so the window is 2026-09-03 22:00 → 2026-09-10 22:00. Point it at a busy week to exercise every section. Without `--dry-run` it writes that week's entry (safe — re-running the same `--date` replaces it). |
| `python run.py --print-prompt` | Also dumps the exact text sent to the `claude` CLI, to the terminal. Use while tuning `PROMPT_TEMPLATE`. |

Combine freely, e.g. `python run.py --date 2026-09-10 --dry-run --print-prompt`.

**Suggested rich window for a first look:** `--date 2026-09-10` — that week has
~35 commits on `The-Seaborn-Portfolio` plus `Automations` activity, enough to
fill every section.

## How the week window works

`anchor` = the most recent Thursday 22:00 local (with a 1-hour grace so a
22:00:00 scheduled launch doesn't slip a week); `--date D` sets `anchor` to
`D @ 22:00`. The window is `[anchor − 7 days, anchor)`. The entry heading is
`## Week ending <anchor date>`, which is also the key used to replace an entry
on a re-run. A missed run (machine off all evening and the next wake is past
the grace) means that week is skipped — re-run it later with `--date`.

## GitHub access

The REST API is called **unauthenticated** — all repos are public and a weekly
run is well under the 60-requests/hour limit. To raise the limit or include
private repos later, set `GITHUB_TOKEN` in the environment or add a
`GITHUB_TOKEN=...` line to a gitignored `.env` in this folder; `run.py` picks it
up automatically.

## Failure handling

- **Fail-safe & idempotent** — the log is written only after a successful
  generation; re-running a week just replaces that entry.
- **Retry with backoff** — GitHub calls retry up to 3× (2s, 4s, … + jitter) on
  `5xx` / connection errors; the `claude` call retries once. Rate-limit (`403`),
  auth (`401`) and `404` fail fast — retrying won't help.
- **Graceful degradation** — if one repo's API calls fail, it's logged and
  skipped, the rest of the summary is still produced, the digest carries a
  `PARTIAL:` note, and the run records `status:"partial"` (exit 0). The run only
  aborts if the repo-list call fails or **every** repo fails.
- **`state/last_run.json`** — written on every real run (not `--dry-run`):

  ```json
  {
    "automation": "02_github_weekly_summary",
    "started_at": "...", "finished_at": "...",
    "status": "ok" | "partial" | "error",
    "exit_code": 0,
    "entry": "2026-09-10",
    "detail": "wrote ... (3458 bytes)",
    "consecutive_failures": 0
  }
  ```

  `consecutive_failures` increments on each `error` (not `partial`) and resets on
  a clean run. **No alerting yet** — check this file or `logs/run.log`.
  Notifications are a planned next layer.

## How it's scheduled

A launch agent, [`schedule/com.omrisabag.github-weekly-summary.plist`](schedule/com.omrisabag.github-weekly-summary.plist):
`StartCalendarInterval` Weekday 4 (Thursday) 22:00, absolute paths, a `PATH` so
`claude` resolves, stdout/stderr to `logs/launchd.{out,err}.log`. A slot missed
while asleep runs on the next wake.

**Install / re-install:**

```bash
cp schedule/com.omrisabag.github-weekly-summary.plist ~/Library/LaunchAgents/
launchctl bootout  gui/$(id -u)/com.omrisabag.github-weekly-summary 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.omrisabag.github-weekly-summary.plist
```

**Verify without waiting for Thursday:**

```bash
launchctl kickstart -k gui/$(id -u)/com.omrisabag.github-weekly-summary
launchctl print gui/$(id -u)/com.omrisabag.github-weekly-summary | grep -iA2 'last exit'
cat logs/launchd.err.log        # expect empty
```

**Uninstall:**

```bash
launchctl bootout gui/$(id -u)/com.omrisabag.github-weekly-summary
rm ~/Library/LaunchAgents/com.omrisabag.github-weekly-summary.plist
```

## Engine

Stdlib only for the data pull (`urllib` against the GitHub REST API). The
summary itself is shelled out to the already-installed `claude` CLI:

```
claude -p "<prompt>" --output-format text --permission-mode dontAsk --model sonnet
```

No web tools — all the activity data is in the prompt. `run.py` does all file
I/O itself.

## Files

| Path | What |
|---|---|
| `run.py` | The automation. |
| `schedule/com.omrisabag.github-weekly-summary.plist` | The launch-agent definition. Copied to `~/Library/LaunchAgents/` to install. |
| `output/github_weekly_summary.md` | The running log — the deliverable. Local only (gitignored). |
| `logs/run.log` | One line per run. Local only (gitignored). |
| `logs/launchd.{out,err}.log` | The launch agent's captured stdout/stderr. Local only (gitignored). |

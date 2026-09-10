# 03 — GitHub Weekly Trend Report

Every Sunday 10:00, produces an **analytical report** on the last 13 weeks of
activity across the owner's public GitHub repos (excluding
`omri-sabag83.github.io`) and **prepends** it to a rolling log:
`output/github_weekly_trend.md` (newest first, **13 entries max**).

This is the template for **report-style** automations — text + a metrics table +
charts, capped length — as opposed to the plain text-digest style of
automations 01 and 02.

Each entry:

1. **Headline** — the single most important movement this week.
2. **This week at a glance** —
   - a portfolio KPI line: total commits, 13-wk range, ▲/▼/▬ vs the 4-week
     average, active days (0–7), active-weeks streak, top-repo share;
   - a table: repo | commits | active days | 13-week trend (unicode sparkline) |
     Δ vs last wk | status, where status carries a 4-week slope arrow
     (↑ / ↓ / →) so level and direction are separate.
3. **Trend chart** — a two-panel PNG: portfolio total on top, per-repo weekly
   commits below with dormant/quiet repos greyed and only movers coloured; the
   chart title states the week's takeaway, not a generic label.
4. **Movements** — what accelerated / slowed / went quiet / started.
5. **Snapshot chart** — weeks since last commit, per repo, vs the dormancy line.
6. **Patterns & benchmarks** — concentration, cadence regularity, dormancy,
   read against rules-of-thumb (flagged as interpretation).
7. **Watchlist** — repos drifting toward dormancy or over-concentration.

A standing note at the top of the file — *commit activity is a consistency /
momentum signal, not a measure of output quality or progress* — frames every
entry.

**Only the newest entry keeps its table and charts.** When a new entry is added,
the previous one is trimmed to its prose (Headline / Movements / Patterns /
Watchlist) — the quantitative history already lives in the newest entry's
13-week trend chart, so it isn't duplicated 13 times. Charts on disk exist only
for the current week.

The log and its charts are **not** in this repo (`output/` is gitignored).
Redacted sample of a (newest) entry:

```markdown
## Week ending 2026-05-17

### Headline
Commit volume held steady week over week, but it is now almost entirely in one
repo — **portfolio-site** — while **data-utils** has gone three weeks without a
commit.

### This week at a glance

**19 commits** this week · 13-wk range 12–28 · ▬ vs 4-week avg of 20
**3 / 7** active days · **9-week** active streak · top repo = 95% of this week's commits

| Repository | Commits | Days | Trend (13 wk) | Δ vs last wk | Status |
|---|--:|--:|:--|--:|---|
| **portfolio-site** | 18 | 3 | `▃▄▄▅▅▆▆▇▆▇▇▇█` | +3 | Active → |
| **data-utils** | 0 | 0 | `▆▇█▅▆▄▂▁▁▁▁▁▁` | -2 | Dormant |
| **scratch-notebooks** | 1 | 1 | `▁▁▁▁▁▁▁▁▁▁▁▁▂` | +1 | New · Active ↑ |

![Weekly commits by repository, 13 weeks to 2026-05-17](charts/2026-05-17_activity_13wk.png)

### Movements
- **portfolio-site** up a third straight week (12 → 15 → 18), 4-week slope
  +1.4/wk; case-study pages, on only 3 active days.
- **data-utils** silent since 2026-04-26 — 4 weeks since last commit, its
  13-week average now carried entirely by history.
- **scratch-notebooks** created this week, one commit so far.

![Weeks since last commit per repository](charts/2026-05-17_weeks_since_commit.png)

### Patterns & benchmarks
This section is interpretation, not measurement. Concentration is high —
**portfolio-site** is ~95% of this week's commits, past the ~70% mark where a
single-repo dependency starts to look like key-person risk. Cadence is regular
(CV 0.24 over 13 weeks) and the 9-week active streak is intact, the healthy end
for solo work. One repo is dormant by the "3+ silent weeks" heuristic.

### Watchlist
- **data-utils** — dormant; decide whether it's finished or parked.
- Concentration in **portfolio-site** — fine while it's the active project.

---
```

## Schedule

**Source of truth:** the `# schedule` block at the top of `run.py`
(`RUN_MODE`, `RUN_DOW`, `RUN_TIME_LOCAL`, `RUN_TIMEZONE`). This section must
match it.

| Setting | Value |
|---|---|
| Mode | **Scheduled** — macOS launch agent `com.omrisabag.github-weekly-trend` |
| Day / time | **Sunday 10:00**, system-local (the Mac is on Asia/Jerusalem; the time follows the Mac's timezone) |
| Also runnable by hand | `python run.py` (and the `--dry-run` / `--demo` / `--date` test flags) |

**To change the day or time, ask Claude** — it updates the `run.py` block,
`schedule/com.omrisabag.github-weekly-trend.plist`, and this table together.

## Run it

```bash
cd Automations/03_github_weekly_trend
python run.py
```

- **Safe to re-run.** A second run for the same week replaces that week's entry
  and its two charts.
- On a GitHub API or `claude` failure the existing log is left untouched and the
  script exits non-zero, with the error in [`logs/run.log`](logs/run.log).

## Testing on demand

| Command | What it does |
|---|---|
| `python run.py --dry-run` | Full run against real GitHub data — then writes the **complete would-be file** to `output/_preview.md` and the charts to `output/charts/`, and stops. The real log / `logs/run.log` / entry-trimming / chart cleanup are skipped. |
| `python run.py --demo` | Same, but from a **fabricated** 13-week, 5-repo dataset (steady / cooling / spiky / dormant / new). Implies `--dry-run` — it can never write the real log. Seeded, so the layout is stable across runs. Use this to judge layout while the real history is only a few weeks deep. |
| `python run.py --date 2026-09-13` | Anchors the report to Sunday 2026-09-13 @ 10:00 (this week = 2026-09-06 10:00 → 2026-09-13 10:00). Without `--dry-run` it writes that week's entry (safe — re-running the same `--date` replaces it). |
| `python run.py --print-prompt` | Also dumps the exact prompt (metrics digest + table) sent to `claude`. |

Combine freely, e.g. `python run.py --demo --print-prompt`.

**To judge layout:** `python run.py --demo` — open `output/_preview.md` in the
Markdown preview (charts render from `charts/`), and open the two PNGs.

## How the window works

`anchor` = the most recent Sunday 10:00 local (1-hour grace so a 10:00:00
scheduled launch doesn't slip a week); `--date D` sets `anchor` to `D @ 10:00`.
"This week" = `[anchor − 7d, anchor)`; the trend covers the **13** cadence-aligned
weeks ending at `anchor`. The entry heading `## Week ending <anchor date>` is the
key used to replace an entry on a re-run. The file keeps the newest 13 entries;
older entries are trimmed to prose and their charts are deleted.

## GitHub access

The REST API is called **unauthenticated** — all repos are public and a weekly
run is ~5–15 requests (`/commits` per repo across the 13-week window, bucketed
by week client-side). To raise the limit or include private repos later, set
`GITHUB_TOKEN` in the environment or add `GITHUB_TOKEN=...` to a gitignored
`.env` in this folder.

## Failure handling

- **Fail-safe & idempotent** — the log + charts are written only after a
  successful generation; re-running a week just replaces that entry and its
  charts.
- **Retry with backoff** — GitHub calls retry up to 3× (2s, 4s, … + jitter) on
  `5xx` / connection errors; the `claude` call retries once. Rate-limit (`403`),
  auth (`401`) and `404` fail fast.
- **Graceful degradation** — a repo whose commits can't be fetched is logged and
  dropped from the report; the rest still renders, the digest carries a
  `PARTIAL:` note, and the run records `status:"partial"` (exit 0). Aborts only
  if the repo-list call fails or **every** repo fails.
- **`state/last_run.json`** — written on every real run (not `--dry-run` /
  `--demo`):

  ```json
  {
    "automation": "03_github_weekly_trend",
    "started_at": "...", "finished_at": "...",
    "status": "ok" | "partial" | "error",
    "exit_code": 0,
    "entry": "2026-09-06",
    "detail": "wrote ... ; 1 entry kept; 0 old chart(s) removed",
    "consecutive_failures": 0
  }
  ```

  `consecutive_failures` increments on each `error` (not `partial`) and resets on
  a clean run. **No alerting yet** — check this file or `logs/run.log`.
  Notifications are a planned next layer.

## How it's scheduled

A launch agent, [`schedule/com.omrisabag.github-weekly-trend.plist`](schedule/com.omrisabag.github-weekly-trend.plist):
`StartCalendarInterval` Weekday 0 (Sunday) 10:00, absolute paths, a `PATH` so
`python` / `claude` resolve, stdout/stderr to `logs/launchd.{out,err}.log`. A
slot missed while asleep runs on the next wake.

**Install / re-install:**

```bash
cp schedule/com.omrisabag.github-weekly-trend.plist ~/Library/LaunchAgents/
launchctl bootout  gui/$(id -u)/com.omrisabag.github-weekly-trend 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.omrisabag.github-weekly-trend.plist
```

**Verify without waiting for Sunday:**

```bash
launchctl kickstart -k gui/$(id -u)/com.omrisabag.github-weekly-trend
launchctl print gui/$(id -u)/com.omrisabag.github-weekly-trend | grep -iA2 'last exit'
cat logs/launchd.err.log        # expect empty
```

**Uninstall:**

```bash
launchctl bootout gui/$(id -u)/com.omrisabag.github-weekly-trend
rm ~/Library/LaunchAgents/com.omrisabag.github-weekly-trend.plist
```

## Engine

- **Data + charts:** the Anaconda Python — `urllib` (stdlib) for the GitHub API,
  `matplotlib` (Agg backend) + `numpy` for the two PNG charts. `run.py` computes
  every number and builds both charts; nothing numeric is left to the model.
- **Prose:** shelled out to the `claude` CLI, which writes only the four
  interpretation sections:

  ```
  claude -p "<prompt>" --output-format text --permission-mode dontAsk --model sonnet
  ```

  No web tools — the metrics digest is in the prompt.

## Files

| Path | What |
|---|---|
| `run.py` | The automation. |
| `schedule/com.omrisabag.github-weekly-trend.plist` | The launch-agent definition. |
| `output/github_weekly_trend.md` | The rolling report (13 entries; only the newest has charts + table). Local only (gitignored). |
| `output/charts/*.png` | The current week's two charts, `<week-ending>_<name>.png`. Local only (gitignored). |
| `logs/run.log`, `logs/launchd.{out,err}.log` | Run + agent logs. Local only (gitignored). |

# Automations

Personal automations. One self-contained folder per automation — start simple,
add complexity over time.

| # | Automation | What it does | Trigger | Status |
|---|---|---|---|---|
| 01 | [daily_ai_briefing](01_daily_ai_briefing/) | Builds a daily AI + data-analytics learning briefing (one AI development, one analyst topic, one learning prompt, a recap of yesterday's Python_Projects work) and prepends it to a running log | Scheduled — launchd, daily 08:00 Asia/Jerusalem (`python run.py` for manual/testing) | Active |
| 02 | [github_weekly_summary](02_github_weekly_summary/) | Weekly judged digest of GitHub repo activity (commits, repos touched, issues/PRs, notable changes, a short read) prepended to a running log | Scheduled — launchd, Thu 22:00 Asia/Jerusalem (`python run.py` for manual/testing) | Active |
| 03 | [github_weekly_trend](03_github_weekly_trend/) | Weekly analytical trend report on repo activity — active / dormant, WoW change, 13-week patterns, metrics table + trend & snapshot charts; keeps last 13 entries (newest keeps charts, older trimmed to prose) | Scheduled — launchd, Sun 10:00 Asia/Jerusalem (`python run.py` for manual/testing) | Active |

## Conventions

- **One folder per automation**, named `NN_short_name/`.
- Each folder is self-contained: its own `README.md`, entrypoint (`run.py`),
  `logs/`, and `state/`.
- Config or secrets that must not be committed go in `.env` / `config.local.*`
  (both gitignored).
- Outputs, run logs, and `state/` stay local (gitignored). Code + docs are
  committed.
- Automations begin **manual**. Once one has earned trust, it graduates to a
  scheduler (macOS `launchd`, or GitHub Actions).
- **Failure handling** (all automations): transient failures retry with backoff;
  a per-item failure degrades gracefully rather than aborting the run; every
  real run writes `state/last_run.json` (`status`, `detail`,
  `consecutive_failures`). No alerting yet — see each README's *Failure
  handling* section.

## Environment

Runs on the existing Anaconda Python and the already-installed `claude` CLI.
Nothing here installs packages or configures services without asking first.

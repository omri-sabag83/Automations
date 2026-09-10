# Automations

Personal automations. One self-contained folder per automation — start simple,
add complexity over time.

| # | Automation | What it does | Trigger | Status |
|---|---|---|---|---|
| 01 | [daily_ai_briefing](01_daily_ai_briefing/) | Builds a daily AI + data-analytics learning briefing (one AI development, one analyst topic, one learning prompt, a recap of yesterday's Python_Projects work) and prepends it to a running log | Scheduled — launchd, daily 08:00 Asia/Jerusalem (`python run.py` for manual/testing) | Active |

## Conventions

- **One folder per automation**, named `NN_short_name/`.
- Each folder is self-contained: its own `README.md`, entrypoint (`run.py`),
  and `logs/`.
- Config or secrets that must not be committed go in `.env` / `config.local.*`
  (both gitignored).
- Outputs worth keeping (an accumulating log of knowledge, a report) are
  committed. Run logs and scratch are not.
- Automations begin **manual**. Once one has earned trust, it graduates to a
  scheduler (macOS `launchd`, or GitHub Actions).

## Environment

Runs on the existing Anaconda Python and the already-installed `claude` CLI.
Nothing here installs packages or configures services without asking first.

# Automations

Personal automations. One self-contained folder per automation — start simple,
add complexity over time.

| # | Automation | What it does | Trigger | Status |
|---|---|---|---|---|
| 01 | [daily_ai_briefing](01_daily_ai_briefing/) | Builds a daily AI + data-analytics learning briefing (one AI development, one analyst topic, one learning prompt, a recap of yesterday's Python_Projects work) and prepends it to a running log | Scheduled — launchd, daily 08:00 Asia/Jerusalem (`python run.py` for manual/testing) | Active |
| 02 | [github_weekly_summary](02_github_weekly_summary/) | Weekly judged digest of GitHub repo activity (commits, repos touched, issues/PRs, notable changes, a short read) prepended to a running log. Covers **public + private repos** (token-gated, see Conventions) | Scheduled — launchd, Thu 22:00 Asia/Jerusalem (`python run.py` for manual/testing) | Active |
| 03 | [github_weekly_trend](03_github_weekly_trend/) | Weekly analytical trend report on repo activity — active / dormant, WoW change, 13-week patterns, metrics table + trend & snapshot charts; keeps last 13 entries (newest keeps charts, older trimmed to prose). Covers **public + private repos** (token-gated, see Conventions) | Scheduled — launchd, Sun 10:00 Asia/Jerusalem (`python run.py` for manual/testing) | Active |
| 04 | [nyc311_weekly_monitor](04_nyc311_weekly_monitor/) | Weekly NYC 311 service-request monitoring report — volume/category/geography shifts vs. recent weeks, anomalies, areas worth investigating, real matplotlib charts; unlike 01–03, Claude itself fetches the data (Socrata API) and performs the analysis each run, not just the prose. One new report per week, written to the sibling `NYC311 Weekly Monitor` repo, never aggregated | Scheduled — launchd, Sun 09:00 Asia/Jerusalem (`python run.py` for manual/testing) | Active |

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
- **GitHub API scope** (02, 03 — both call the GitHub REST API for repo
  activity): **unauthenticated by default, which only sees public repos.**
  Dropping a `GITHUB_TOKEN` into either automation's own `.env` (fine-grained
  PAT, `Contents: Read-only` + `Metadata: Read-only`, plus `Issues: Read-only`
  for `02`) switches the repo listing to the authenticated `/user/repos`
  endpoint, which includes **private repos too** — the same token works for
  both, each folder just keeps its own copy of the `.env`. No token → still
  works, just public-only. (01 reads local `git log`, not the GitHub API, so
  visibility doesn't apply to it; 04 doesn't touch GitHub at all.)

## Environment

Runs on the existing Anaconda Python and the already-installed `claude` CLI.
Nothing here installs packages or configures services without asking first.

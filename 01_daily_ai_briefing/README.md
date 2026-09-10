# 01 — Daily AI & Data Analytics Briefing

Generates one dated entry per day and **prepends** it to a running log:
[`output/daily_ai_analytics_briefing.md`](output/daily_ai_analytics_briefing.md) (newest first).

Each entry has four parts:

1. **AI development** — one recent, interesting thing from the AI world, with a
   source (found via live web search).
2. **Analyst topic worth knowing** — one concept, technique, or tool.
3. **Learning prompt** — a small 15–30 min exercise, usually tied to part 2.
4. **Yesterday's ground covered** — a judged recap of what was actually worked
   on in `Python_Projects/` the previous calendar day.

The running log itself is **not** in this repo — part 4 names private work, so
`output/` is gitignored. Redacted sample of one entry:

```markdown
## 2026-05-14

### 1. AI development
A frontier lab published an approach that lets a model call tools mid-generation
without breaking its reasoning trace, reporting a double-digit gain on an agentic
benchmark. Notable less for the score than for shipping the eval harness so the
result is reproducible. [source](https://example.com)

### 2. Analyst topic worth knowing
The empirical CDF is often a better first look at a distribution than a
histogram: no bin-width choice, every point used, and percentiles read straight
off the curve. Overlay two groups and the largest vertical gap is the
Kolmogorov–Smirnov statistic.

### 3. Learning prompt
Take a numeric metric split by a 2–4 level category, draw overlaid ECDFs, read
the median and 90th percentile off each curve, and write two sentences on what a
bar chart of group means would have hidden.

### 4. Yesterday's ground covered
- Scaffolded a new analysis exercise on a fresh dataset: loader, analysis
  script, evidence notebook.
- Built ~8 charts (trend, segment breakdown, a driver tornado, a leakage check).
- Wrote the summary and index for a prior exercise.

---
```

## Schedule

**Source of truth:** the `# schedule` block at the top of `run.py`
(`RUN_MODE`, `RUN_TIME_LOCAL`, `RUN_TIMEZONE`). This section must match it.

| Setting | Value |
|---|---|
| Mode | **Scheduled** — macOS launch agent `com.omrisabag.daily-ai-briefing` |
| Daily time | **08:00**, system-local (the Mac is on Asia/Jerusalem — the time follows the Mac's timezone) |
| Also runnable by hand | `python run.py` (and the `--dry-run` / `--date` test flags) |

**To change the time or cadence, ask Claude** — it updates the `run.py` block,
`schedule/com.omrisabag.daily-ai-briefing.plist`, and this table together.

## Run it

```bash
cd Automations/01_daily_ai_briefing
python run.py
```

Runs the same code the scheduler runs. Use it for a manual catch-up or, with the
[test flags](#testing-on-demand), for iterating on output.

- **Safe to re-run the same day.** A second run for the same date replaces that
  day's entry instead of adding a duplicate — so testing is free.
- If the `claude` call fails (network, auth, rate limit), the existing log is
  left untouched and the script exits non-zero with the error in
  [`logs/run.log`](logs/run.log).

## Testing on demand

Three flags let you exercise the automation now, without waiting for a schedule
and without disturbing the real log:

| Command | What it does |
|---|---|
| `python run.py --dry-run` | Full run — gathers activity, calls Claude — then writes the **complete would-be file** to `output/_preview.md` and stops. The real `output/daily_ai_analytics_briefing.md` and `logs/run.log` are not touched. Open `_preview.md` in the Markdown preview to judge it as it would render. |
| `python run.py --date 2026-09-08` | Treats 2026-09-08 as "today", so "yesterday" is 2026-09-07. Point it at a day you know had activity to check the recap. Without `--dry-run` it does write that dated entry to the real log (safe — re-running the same `--date` replaces it). |
| `python run.py --print-prompt` | Also dumps the exact text sent to the `claude` CLI, to the terminal. Use while tuning `PROMPT_TEMPLATE`. |

Combine freely, e.g. `python run.py --date 2026-09-08 --dry-run --print-prompt`.

`output/_preview.md` is gitignored — it is scratch, overwritten by every
`--dry-run`, never committed.

The typical loop while dialling in output quality: run with `--dry-run` (add
`--date` to target a richer day), open `output/_preview.md` in the preview,
adjust `PROMPT_TEMPLATE` in `run.py`, repeat. Plain `python run.py` with no
flags is unchanged.

## How "yesterday's ground covered" is worked out

The script gathers raw activity for the previous calendar day and hands it to
Claude to summarise with judgement (new project / analysis / deliverable = a
bullet; routine re-saves and cache files = ignored):

- **Git commits** — `git log` for that day's window, from every repo directly
  under `Python_Projects/` (`The Seaborn Portfolio`, `Python for Data
  Analytics`, `Automations`).
- **File mtimes** — a walk of the whole `Python_Projects/` tree for files
  modified in that window, skipping `.git`, `__pycache__`, `.DS_Store`,
  `.ipynb_checkpoints`, `node_modules`, caches, and this automation's own
  output/log.

**Local work counts.** The mtime walk is deliberate: it catches work that never
touched git — most importantly `AI Analyst Workspace/`, which is not a repo, so
scaffolding E06 there shows up even with no commit. Anything you create or edit
anywhere under `Python_Projects/` that day is in scope.

Caveats of the mtime approach: a file created earlier but merely *re-saved*
yesterday reads as yesterday's work, and a bulk operation (a checkout, a
find-and-replace across a folder, moving a directory) can light up hundreds of
files at once. The "apply judgement" instruction to Claude and the 200-file cap
are there to absorb that noise, but a very large mechanical change can still
skew a recap.

On day one (or any day with no prior activity) part 4 just says *Nothing
notable recorded*.

## Engine

Shells out to the already-installed `claude` CLI:

```
claude -p "<prompt>" --output-format text \
  --permission-mode dontAsk --allowedTools WebSearch WebFetch --model sonnet
```

No API key, no packages, no MCP. `run.py` does all file I/O itself; the CLI only
returns text, so `WebSearch` / `WebFetch` are the only tools it needs.

## Config

v1 keeps settings as constants at the top of `run.py` (`CLAUDE_MODEL`,
`CLAUDE_TIMEOUT_S`, the skip lists, `MAX_CHANGED_FILES`). Pulling them into a
`config.yaml` is a later step, once there's a second automation to share
patterns with.

## Failure handling

- **Fail-safe & idempotent** — the log is written only after a successful
  generation, so a failure never half-writes it; re-running any date just
  replaces that entry.
- **Retry with backoff** — the `claude` call is retried once (5s + jitter) on a
  non-zero exit or timeout. Non-transient errors (bad output shape) fail fast.
- **`state/last_run.json`** — written on every real run (not `--dry-run`):

  ```json
  {
    "automation": "01_daily_ai_briefing",
    "started_at": "...", "finished_at": "...",
    "status": "ok" | "error",
    "exit_code": 0,
    "entry": "2026-09-10",
    "detail": "wrote ... (3458 bytes)",
    "consecutive_failures": 0
  }
  ```

  `consecutive_failures` increments on each `error` and resets to `0` on success.
  There is **no alerting yet** — check this file (or `logs/run.log`) to see if
  the last scheduled run was healthy. Notifications are a planned next layer.

## How the schedule works

`launchd` is macOS's built-in job scheduler (the modern replacement for `cron`,
already part of the OS). The job is defined by
[`schedule/com.omrisabag.daily-ai-briefing.plist`](schedule/com.omrisabag.daily-ai-briefing.plist):
`StartCalendarInterval` 08:00, absolute paths to `python` and `run.py`, a `PATH`
so the script's `claude` / `git` calls resolve, and stdout/stderr to
`logs/launchd.{out,err}.log`. If the Mac is asleep at 08:00, `launchd` runs the
missed slot on the next wake (`cron` would skip the day).

**Install / re-install** (after editing the plist):

```bash
cp schedule/com.omrisabag.daily-ai-briefing.plist ~/Library/LaunchAgents/
launchctl bootout  gui/$(id -u)/com.omrisabag.daily-ai-briefing 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.omrisabag.daily-ai-briefing.plist
```

**Verify without waiting for 08:00:**

```bash
launchctl kickstart -k gui/$(id -u)/com.omrisabag.daily-ai-briefing
launchctl print gui/$(id -u)/com.omrisabag.daily-ai-briefing | grep -iA2 'last exit'
cat logs/launchd.err.log        # expect empty
```

**Uninstall:**

```bash
launchctl bootout gui/$(id -u)/com.omrisabag.daily-ai-briefing
rm ~/Library/LaunchAgents/com.omrisabag.daily-ai-briefing.plist
```

Note: `claude` reads its auth from the macOS login keychain, which a launch
agent can access while you're logged in (a locked screen is fine).

## Files

| Path | What |
|---|---|
| `run.py` | The automation. |
| `schedule/com.omrisabag.daily-ai-briefing.plist` | The launch-agent definition. Copied to `~/Library/LaunchAgents/` to install. |
| `output/daily_ai_analytics_briefing.md` | The running log — the deliverable. Local only (gitignored: it names private work). |
| `logs/run.log` | One line per manual/scheduled run. Local only (gitignored). |
| `logs/launchd.{out,err}.log` | The launch agent's captured stdout/stderr. Local only (gitignored). |

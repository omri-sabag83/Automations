# 05 — Weekly Music Release Tracker

Every Sunday 12:00, checks whether artists you follow released a new
**studio album** in the past week and writes the result to a single running
log, [`output/weekly_music_releases.md`](output/weekly_music_releases.md)
(newest week first). Unlike 01-03, this automation calls no `claude` model —
the output is a plain data table with no prose or judgment involved, so
there's nothing for a model to usefully add. Entirely deterministic.

Who you follow isn't a hardcoded list: it's read fresh every run from two
tabs of a personal spreadsheet (`Music` col C, `Listen` col A), deduped, so
the watch list stays current automatically as your listening habits change.

Each week's entry:

1. **Heading** — the date the check ran (e.g. `## Sun, 27.09.2026`).
2. **Table** — `Artist | Album | Number | Release Date`, sorted oldest→newest
   release date, covering only new studio albums (no live albums,
   compilations, EPs, or soundtracks) with a release date inside that week.
   `Number` is the album's position in that artist's studio discography;
   left blank if it can't be confirmed. On a week with no matches, the table
   is replaced with a single line: *"No new studio album releases this
   week."*

```
## Sun, 15.05.2016

| Artist | Album | Number | Release Date |
|---|---|---|---|
| Radiohead | A Moon Shaped Pool | 10 | 2016-05-08 |
```

## Schedule

**Source of truth:** the `# schedule` block at the top of `run.py`
(`RUN_MODE`, `RUN_DOW`, `RUN_TIME_LOCAL`, `RUN_TIMEZONE`). This section must
match it.

| Setting | Value |
|---|---|
| Mode | **Scheduled** — macOS launch agent `com.omrisabag.weekly-music-releases` |
| Day / time | **Sunday 12:00**, system-local (the Mac is on Asia/Jerusalem; the time follows the Mac's timezone) |
| Also runnable by hand | `python run.py` (and the `--dry-run` / `--date` test flags) |

**To change the day or time, ask Claude** — it updates the `run.py` block,
the `.plist`, and this table together.

## Run it

```bash
cd "Automations/05_weekly_music_releases"
python run.py
```

- **Guarded against accidental re-scans.** If this week is already logged,
  the run exits immediately (`status: "skipped_duplicate"`) instead of
  redoing the ~1,150-artist MusicBrainz scan — a launchd double-fire or a
  stray manual run doesn't cost anything. Use `--force` to deliberately redo
  a week (e.g. something looked wrong the first time); the idempotent upsert
  still just replaces that one entry, never duplicates it.
- On a watch-list or MusicBrainz failure, no report file is written and the
  script exits non-zero, with the reason in `logs/run.log` and
  `state/last_run.json`.

## Testing on demand

| Command | What it does |
|---|---|
| `python run.py --dry-run` | Runs the real lookups against live MusicBrainz data, then writes the result to `output/_preview.md` instead of the real log, and stops. The real log / `logs/run.log` / `state/last_run.json` are untouched — but `state/artist_cache.json` **is** still updated, since resolving an artist's MusicBrainz ID is reusable regardless of dry-run. The duplicate-skip guard still applies (checks the real log, not sandboxed) unless paired with `--force`. |
| `python run.py --date 2026-09-14` | Anchors the check to the week containing that date instead of today. Also how you'd backfill a missed run. |
| `python run.py --force` | Redoes the current week's scan even though it's already logged. |

## How the window works

The analyzed week is always a **calendar week, Sunday 00:00 to the following
Sunday 00:00, Asia/Jerusalem** (the Mac's own local date — MusicBrainz
release dates aren't tied to one specific timezone the way a live data feed
would be, so there's no separate data-source zone to reconcile).
`week_end` = the most recent Sunday at/before the anchor (`--date`, or
today); `week_start = week_end − 7 days`. This is a pure function of the
calendar date, so re-running any day within the same week resolves to the
same entry and just replaces it.

## Engine — how the watch list becomes a report

1. **Read the watch list** — `Music` tab col C + `Listen` tab col A (parsed
   `"artist - album"`, keeping the artist half) from the source spreadsheet,
   deduped and unioned. Re-read fresh every run, never cached.
2. **Resolve each artist to a MusicBrainz ID** — a name search against
   MusicBrainz's API (score ≥ 90 required to count as a confident match); a
   light fallback normalization (`&`/`and`, leading "The", whitespace) is
   tried once if the exact name doesn't match; still unmatched → skipped and
   logged, never guessed. Resolved IDs are cached in
   `state/artist_cache.json` so only genuinely new artist names hit the
   search API on later runs — most of the watch list is unchanged week to
   week, so this is the main thing keeping run time reasonable.
3. **Check for a studio album in this week's window** — a date-ranged query
   per artist, filtered to release-groups with no secondary type (excludes
   Live/Compilation/Soundtrack/etc.; `type=album` already excludes
   Singles/EPs at the primary-type level).
4. **For each hit, compute its `Number`** — the artist's full studio
   discography is paginated and sorted by release date; the hit's 1-based
   position in that list is `Number`. This second call only happens for
   artists with an actual hit that week, not the whole watch list.

**Why no `claude` call:** every other automation here follows "run.py
computes every number, the model writes prose only" — this one just skips
the prose half entirely, since a release table needs no interpretation.

**Scale:** the watch list currently runs ~1,150 unique artists. MusicBrainz
asks for ~1 request/second unauthenticated, but throttles more heavily than
that in practice — the first real run (2026-09-29, resolving all 1,153 new
artist names from scratch) took **~1 hour**, all of it absorbed by the retry
logic (0 failures). A steady-state run only needs the release-check call per
artist (names already in `state/artist_cache.json` skip the resolve step),
so expect somewhat less — but budget closer to an hour than the original
~20-minute estimate, and don't expect it to be fast.

## Failure handling

- **Fail-safe & idempotent** — the log is written only after a complete,
  successful gather; re-running a week just replaces that entry.
- **Duplicate-guarded** — a week already logged is skipped
  (`status: "skipped_duplicate"`) rather than silently re-scanning ~1,150
  artists; `--force` overrides this.
- **Retry with backoff** — MusicBrainz calls retry up to 3× on `5xx` /
  connection errors. In practice during the first real run this triggered
  often — MusicBrainz throttled more than its nominal ~1 req/sec etiquette
  suggests — but every retry self-healed; the run finished with 0 failures.
- **Graceful degradation** — one artist's lookup failure is logged, skipped,
  and counted in `PARTIAL:`; the run records `status:"partial"` (exit 0).
  Aborts only if the watch-list spreadsheet itself can't be read, or if
  every artist lookup fails.
- **`state/last_run.json`** — written on every real run (not `--dry-run`):

  ```json
  {
    "automation": "05_weekly_music_releases",
    "started_at": "...", "finished_at": "...",
    "status": "ok" | "partial" | "error",
    "exit_code": 0,
    "entry": "Sun, 27.09.2026",
    "detail": "wrote ... (...); N release(s)",
    "consecutive_failures": 0
  }
  ```

  `consecutive_failures` increments only on `error`. **No alerting yet** —
  check this file or `logs/run.log`.

## How it's scheduled

A launch agent, [`schedule/com.omrisabag.weekly-music-releases.plist`](schedule/com.omrisabag.weekly-music-releases.plist):
`StartCalendarInterval` Weekday 0 (Sunday) 12:00, absolute paths, a `PATH` so
`python` resolves, stdout/stderr to `logs/launchd.{out,err}.log`. A slot
missed while asleep runs on the next wake.

**Install / re-install:**

```bash
cp schedule/com.omrisabag.weekly-music-releases.plist ~/Library/LaunchAgents/
launchctl bootout  gui/$(id -u)/com.omrisabag.weekly-music-releases 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.omrisabag.weekly-music-releases.plist
```

**Verify without waiting for Sunday:**

```bash
launchctl kickstart -k gui/$(id -u)/com.omrisabag.weekly-music-releases
launchctl print gui/$(id -u)/com.omrisabag.weekly-music-releases | grep -iA2 'last exit'
cat logs/launchd.err.log        # expect empty
```

**Uninstall:**

```bash
launchctl bootout gui/$(id -u)/com.omrisabag.weekly-music-releases
rm ~/Library/LaunchAgents/com.omrisabag.weekly-music-releases.plist
```

## Data source

[MusicBrainz](https://musicbrainz.org/) — free, public, unauthenticated REST
API (`https://musicbrainz.org/ws/2/`), no signup or key required. Chosen
over the Google Sheets API (setup friction hit and abandoned earlier) and
over needing 2-3 combined sources (an initial idea, dropped once live
testing showed MusicBrainz alone cleanly handles both English and Hebrew
artist names with proper release-type tagging). A second source may be
added later if real coverage gaps show up in practice.

## Files

| Path | What |
|---|---|
| `run.py` | The automation. |
| `schedule/com.omrisabag.weekly-music-releases.plist` | The launch-agent definition. |
| `output/weekly_music_releases.md` | The running log. Local only (gitignored). |
| `output/_preview.md` | `--dry-run` scratch output. Local only (gitignored). |
| `logs/run.log`, `logs/launchd.{out,err}.log` | Run + agent logs. Local only (gitignored). |
| `state/last_run.json` | Last run's status. Local only (gitignored). |
| `state/artist_cache.json` | Artist name → MusicBrainz ID cache. Local only (gitignored). |

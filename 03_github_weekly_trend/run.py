#!/usr/bin/env python3
"""
Automation #03 — GitHub weekly trend report.

Scheduled by a launch agent (schedule/com.omrisabag.github-weekly-trend.plist)
every Sunday at 10:00. Can also be run by hand:

    python run.py                       # report on the past week, write the log
    python run.py --dry-run             # write output/_preview.md + charts only
    python run.py --demo                # same, from FABRICATED data (implies --dry-run)
    python run.py --date 2026-09-13     # anchor the week to that date @ 10:00 (testing)
    python run.py --print-prompt        # also show the exact prompt sent to Claude

Each run:
  1. Window: anchor = most recent Sunday 10:00 local (or --date @ 10:00);
     this week = [anchor - 7d, anchor); trend window = the 13 weeks up to anchor.
  2. Pulls commits per owned repo across the 13-week window from the GitHub REST
     API (minus EXCLUDE_REPOS), buckets them by week, and derives per-repo and
     portfolio metrics plus two charts (one trend, one snapshot).
  3. Asks Claude (via the `claude` CLI) for the analytical prose only -
     Headline / Movements / Patterns & benchmarks / Watchlist.
  4. Assembles the entry (prose + table + charts), prepends it to
     output/github_weekly_trend.md (newest first, same-week replaced). Only the
     newest entry keeps its table + charts; older entries are trimmed to prose.
     The file keeps the newest 13 entries; charts exist only for the newest.

This is the template for report-style (analytical, chart-bearing) automations,
as distinct from the text-digest style of automations 01 and 02.

Uses the existing Anaconda Python (matplotlib + numpy) and the `claude` CLI.
The GitHub API is called unauthenticated (all repos public); set GITHUB_TOKEN
in the environment or a gitignored .env here to raise limits / see private repos.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(_HERE / "output" / ".mplcache"))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

# ------------------------------------------------------------------- schedule
# SINGLE SOURCE OF TRUTH for when this runs. Keep it in sync with both READMEs.
# Scheduled by a macOS launch agent:
#     schedule/com.omrisabag.github-weekly-trend.plist -> ~/Library/LaunchAgents/
#     fires `python run.py` every Sunday 10:00 system-local (Asia/Jerusalem).
# `python run.py` by hand still works, for manual and --dry-run testing.
# To change the day/time, ask Claude to update this block, the .plist, and both
# READMEs together.
RUN_MODE = "scheduled"          # "manual" | "scheduled" (launchd agent installed)
RUN_TIME_LOCAL = "10:00"        # HH:MM, 24h
RUN_DOW = "Sun"                 # day of week
RUN_TIMEZONE = "Asia/Jerusalem"  # Israel local time (follows the Mac's timezone)

RUN_WEEKDAY = 6                 # Mon=0 .. Sun=6  (must match RUN_DOW)
RUN_HOUR, RUN_MINUTE = 10, 0

# --------------------------------------------------------------------- config
HERE = _HERE
PROJECTS_ROOT = HERE.parent.parent  # ~/Documents/Python_Projects (for log paths)
OUTPUT_FILE = HERE / "output" / "github_weekly_trend.md"
PREVIEW_FILE = HERE / "output" / "_preview.md"  # --dry-run target; gitignored
CHARTS_DIR = HERE / "output" / "charts"         # gitignored
LOG_FILE = HERE / "logs" / "run.log"

GITHUB_USER = "omri-sabag83"
EXCLUDE_REPOS = {"omri-sabag83.github.io"}      # not projects; kept out of the report
GITHUB_API = "https://api.github.com"
HTTP_TIMEOUT_S = 30

TREND_WEEKS = 13               # weeks of history per entry
MAX_ENTRIES = 13              # prose entries kept in the file
NEW_REPO_DAYS = 21           # younger than this => "New" tag on the status
DORMANT_WEEKS = 3           # zero commits this many recent weeks => "Dormant"
DEMO_SEED = 20260913       # fixed so --demo layout is stable across runs

CLAUDE_BIN = "claude"
CLAUDE_MODEL = "sonnet"
CLAUDE_TIMEOUT_S = 600
PROSE_WORD_TARGET = 600

SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
ACTIVITY_CAVEAT = ("_Commit activity is a consistency and momentum signal - not "
                   "a measure of output quality or progress._")

INTRO = f"""\
# GitHub Weekly Trend Report

A rolling analytical report, newest entry first, generated every Sunday 10:00 by
`Automations/03_github_weekly_trend/run.py`. Each entry looks across the owner's
public repositories over the last 13 weeks - which are most active, which have
gone dormant, what changed versus prior weeks, and the patterns worth noting.
Only the newest entry carries the metrics table and the two charts; older
entries are trimmed to their prose. The file keeps the 13 most recent entries.

{ACTIVITY_CAVEAT}

---
"""

_SECTIONS = ["Headline", "Movements", "Patterns & benchmarks", "Watchlist"]
_PROSE_SECTIONS = set(_SECTIONS)  # kept when an entry is demoted to prose-only

PROMPT_TEMPLATE = """\
You are the analyst writing this week's entry in a running "GitHub activity \
trend" report. It tracks a solo data analyst's public repositories week over \
week, with up to 13 weeks of history. Below is a computed metrics digest and \
the exact table that will appear in the entry. Work only from what is given - \
do not reproduce the table, invent numbers, or add images.

Write EXACTLY these four sections, each starting with the given `### ` heading, \
in this order, and nothing else (no `## ` heading, no table, no closing rule):

### Headline
1-2 sentences naming the single most important movement this week.

### Movements
2-4 bullets: what accelerated, slowed, went quiet, went dormant, started, or \
spiked - week over week and against the 13-week pattern. Cite specific repos \
and numbers from the digest.

### Patterns & benchmarks
Open by noting this section is interpretation, then give 3-5 sentences (do not \
repeat the word each sentence). Cover concentration (top-repo share of this \
week's commits), cadence regularity (use the coefficient of variation), and \
dormancy. Compare against reasonable rules-of-thumb - e.g. one repo above ~70% \
of commits = concentration risk; no commits for 3+ weeks = trending dormant; a \
stable week-to-week total = healthy solo cadence - and name them as heuristics, \
not laws.

### Watchlist
1-3 bullets: repos drifting toward dormancy, over-concentration, or other \
risks worth checking next week. If there is nothing, write exactly: \
_Nothing on the watchlist._

Keep the four sections to {word_target} words total. Plain and honest, not \
promotional. Write repo names as plain bold `**<repo>**` - a later step turns \
them into links; do not add your own links.

The digest also gives, per repo: active days this week (0-7, distinct calendar \
days with a commit - harder to game than a raw count), the current active-weeks \
streak, and a 4-week slope (rising / falling / flat). Use these where they \
sharpen the read; do not just restate them.

--- METRICS DIGEST ---
{digest}

--- TABLE (already in the entry; for your reference only) ---
{table}
"""


# ------------------------------------------------------------------- helpers
# Set False by --dry-run / --demo so test runs never touch logs/run.log.
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


def most_recent_anchor(now: dt.datetime) -> dt.datetime:
    """The most recent Sunday 10:00 local, with a 1 h grace either side."""
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
        "User-Agent": f"automations-github-weekly-trend ({GITHUB_USER})",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    token = _github_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode()), resp.headers.get("Link", "")
    except urllib.error.HTTPError as exc:
        extra = ""
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            extra = (f" (unauthenticated rate limit exhausted; resets at epoch "
                     f"{exc.headers.get('X-RateLimit-Reset', '?')})")
        raise GatherError(f"GitHub API {exc.code} for {path}{extra}") from exc
    except urllib.error.URLError as exc:
        raise GatherError(f"network error for {path}: {exc.reason}") from exc


def _gh_paged(path: str, params: dict | None = None) -> list:
    params = dict(params or {})
    params.setdefault("per_page", 100)
    out: list = []
    page = 1
    while page <= 15:  # safety ceiling
        params["page"] = page
        data, link = _gh_get(path, params)
        if not isinstance(data, list):
            break
        out.extend(data)
        if 'rel="next"' not in link or len(data) < params["per_page"]:
            break
        page += 1
    return out


def _parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _week_end_dates(anchor: dt.datetime) -> list[dt.date]:
    """Week-ending dates, oldest first; index TREND_WEEKS-1 ends at the anchor."""
    return [(anchor - dt.timedelta(days=7 * (TREND_WEEKS - 1 - i))).date()
            for i in range(TREND_WEEKS)]


# ------------------------------------------------------------ gather / metrics
def _sparkline(series: list[int]) -> str:
    hi = max(series) or 1
    n = len(SPARK_BLOCKS) - 1
    return "".join(SPARK_BLOCKS[min(n, round(v / hi * n))] for v in series)


def _trailing_streak(series: list[int]) -> int:
    s = 0
    for v in reversed(series):
        if v > 0:
            s += 1
        else:
            break
    return s


def _slope(series: list[int], k: int) -> float:
    k = min(k, len(series))
    if k < 2:
        return 0.0
    return float(np.polyfit(range(k), series[-k:], 1)[0])


def _is_mover(m: dict) -> bool:
    return (m["status"] in ("Active", "Cooling")
            or (m["is_new"] and m["this_week"] > 0)
            or abs(m["slope4"]) >= max(0.5, 0.15 * m["avg"]))


def _compute(weekly: dict, created: dict, anchor_utc: dt.datetime,
             week_dates: dict) -> tuple[dict, dict]:
    trend_start_utc = anchor_utc - dt.timedelta(days=7 * TREND_WEEKS)
    metrics: dict[str, dict] = {}
    for name, series in weekly.items():
        born = created[name]
        weeks_alive = max(1, min(TREND_WEEKS, int(
            (anchor_utc - max(trend_start_utc, born)).total_seconds() // (7 * 86400)
        ) + 1))
        this_week, last_week = series[-1], series[-2]
        total = sum(series)
        avg = total / weeks_alive
        age_days = (anchor_utc - born).days
        last3 = sum(series[-DORMANT_WEEKS:])
        last_active = next((i for i in range(TREND_WEEKS - 1, -1, -1) if series[i] > 0), None)
        weeks_since = (TREND_WEEKS - 1 - last_active) if last_active is not None else None
        if total == 0 or last3 == 0:
            status = "Dormant"
        elif this_week == 0:
            status = "Quiet"
        elif avg > 0 and this_week < 0.5 * avg:
            status = "Cooling"
        else:
            status = "Active"
        is_new = age_days < NEW_REPO_DAYS
        slope4 = _slope(series, min(4, weeks_alive))
        thr = max(0.5, 0.15 * avg)
        arrow = "↑" if slope4 > thr else ("↓" if slope4 < -thr else "→")
        base_label = f"New · {status}" if is_new else status
        label = base_label if status == "Dormant" else f"{base_label} {arrow}"
        metrics[name] = dict(
            series=series, total=total, this_week=this_week, last_week=last_week,
            avg=avg, weeks_alive=weeks_alive, delta=this_week - last_week,
            status=status, label=label, is_new=is_new,
            age_days=age_days, weeks_since=weeks_since,
            active_days=len(week_dates.get(name, set())),
            streak=_trailing_streak(series), slope4=slope4, arrow=arrow,
            spark=_sparkline(series),
        )

    total_tw = sum(m["this_week"] for m in metrics.values())
    total_lw = sum(m["last_week"] for m in metrics.values())
    for m in metrics.values():
        m["share"] = (m["this_week"] / total_tw) if total_tw else 0.0

    pseries = [sum(m["series"][i] for m in metrics.values()) for i in range(TREND_WEEKS)]
    mean = sum(pseries) / len(pseries) if pseries else 0.0
    cv = ((sum((v - mean) ** 2 for v in pseries) / len(pseries)) ** 0.5 / mean
          ) if mean > 0 else 0.0
    top_repo, top_share = (max(((n, m["share"]) for n, m in metrics.items()),
                               key=lambda kv: kv[1]) if metrics else ("-", 0.0))
    alive_from = min((TREND_WEEKS - m["weeks_alive"] for m in metrics.values()), default=0)
    peff = pseries[alive_from:] or pseries
    avg4 = sum(pseries[-4:]) / min(4, len(pseries)) if pseries else 0.0
    p_arrow = ("▲" if total_tw > avg4 * 1.1 else "▼" if total_tw < avg4 * 0.9 else "▬")
    all_days: set[str] = set().union(*(week_dates.get(n, set()) for n in metrics)) if metrics else set()
    portfolio = dict(
        total_this_week=total_tw, total_last_week=total_lw,
        wow_delta=total_tw - total_lw,
        active=sum(1 for m in metrics.values() if m["status"] in ("Active", "Cooling")),
        dormant=sum(1 for m in metrics.values() if m["status"] == "Dormant"),
        new=sum(1 for m in metrics.values() if m["is_new"]),
        cv=cv, top_repo=top_repo, top_share=top_share,
        pseries=pseries, range_lo=min(peff), range_hi=max(peff), avg4=avg4,
        arrow=p_arrow, streak=_trailing_streak(pseries), active_days=len(all_days),
    )
    return metrics, portfolio


def gather_trends(anchor: dt.datetime) -> tuple[dict, dict, dict]:
    anchor_utc = anchor.astimezone(dt.timezone.utc)
    trend_start_utc = anchor_utc - dt.timedelta(days=7 * TREND_WEEKS)
    since = trend_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    until = anchor_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    repos = _gh_paged(f"/users/{GITHUB_USER}/repos",
                      {"sort": "full_name", "type": "owner"})
    created = {
        r["name"]: _parse_iso(r["created_at"])
        for r in repos
        if not r.get("archived")
        and r["name"] not in EXCLUDE_REPOS
        and _parse_iso(r["created_at"]) < anchor_utc
    }

    weekly: dict[str, list[int]] = {n: [0] * TREND_WEEKS for n in created}
    week_dates: dict[str, set] = {n: set() for n in created}
    for name in created:
        commits = _gh_paged(f"/repos/{GITHUB_USER}/{name}/commits",
                            {"since": since, "until": until})
        for c in commits:
            cd = ((c.get("commit") or {}).get("committer") or {}).get("date")
            if not cd:
                continue
            k = int((anchor_utc - _parse_iso(cd)).total_seconds() // (7 * 86400))
            if 0 <= k < TREND_WEEKS:
                weekly[name][TREND_WEEKS - 1 - k] += 1
                if k == 0:
                    week_dates[name].add(cd[:10])

    metrics, portfolio = _compute(weekly, created, anchor_utc, week_dates)
    return weekly, metrics, portfolio


def demo_trends(anchor: dt.datetime) -> tuple[dict, dict, dict]:
    """A fabricated 13-week, 5-repo dataset for judging layout. Never written live."""
    anchor_utc = anchor.astimezone(dt.timezone.utc)
    rng = random.Random(DEMO_SEED)
    W = TREND_WEEKS
    specs = {  # name: (shape, age in weeks; 0 => ~10 days old)
        "portfolio-site": ("steady", 20),
        "data-pipeline": ("cooling", 18),
        "ml-experiments": ("spiky", 16),
        "client-dashboard": ("dormant", 15),
        "dotfiles": ("new", 0),
    }
    weekly: dict[str, list[int]] = {}
    created: dict[str, dt.datetime] = {}
    for name, (shape, age_weeks) in specs.items():
        s = [0] * W
        if shape == "steady":
            s = [max(0, round(rng.gauss(11 + i * 0.35, 2))) for i in range(W)]
        elif shape == "cooling":
            s = [max(0, round(rng.gauss(19 if i < W - 3 else 3, 2))) for i in range(W)]
        elif shape == "spiky":
            s = [rng.randint(9, 15) if (W - 1 - i) % 3 == 0 else rng.choice([0, 0, 1])
                 for i in range(W)]
        elif shape == "dormant":
            s = [max(0, round(rng.gauss(9, 3))) if i < 6 else 0 for i in range(W)]
        elif shape == "new":
            s[-1] = rng.randint(3, 6)
        weekly[name] = s
        created[name] = anchor_utc - dt.timedelta(
            days=10 if age_weeks == 0 else 7 * age_weeks + 2
        )

    week_dates: dict[str, set] = {}
    wk_start = (anchor - dt.timedelta(days=7)).date()
    for name, series in weekly.items():
        # spread this week's commits over a realistic 2-6 distinct days
        n_days = min(series[-1], rng.choice([2, 3, 3, 4, 4, 5, 6]))
        picks = rng.sample(range(7), n_days) if n_days else []
        week_dates[name] = {(wk_start + dt.timedelta(days=d)).isoformat() for d in picks}

    metrics, portfolio = _compute(weekly, created, anchor_utc, week_dates)
    return weekly, metrics, portfolio


# --------------------------------------------------------------------- charts
def _repo_color(name: str, all_names: list[str]):
    return plt.cm.tab10(sorted(all_names).index(name) % 10)


def _chart1_title(portfolio: dict, metrics: dict) -> str:
    lead = (f"Portfolio {portfolio['arrow']} {portfolio['total_this_week']} "
            f"commits this week ({portfolio['wow_delta']:+d} WoW)")
    if metrics:
        mover, mv = max(((n, m["delta"]) for n, m in metrics.items()),
                        key=lambda kv: abs(kv[1]))
        if mv:
            lead += f" — {mover} {'up' if mv > 0 else 'down'} {abs(mv)}"
    return lead


def _chart2_title(metrics: dict) -> str:
    past = [n for n, m in metrics.items()
            if m["weeks_since"] is None or m["weeks_since"] >= DORMANT_WEEKS]
    if past:
        return (f"{len(past)} repo{'s' if len(past) != 1 else ''} past the "
                f"{DORMANT_WEEKS}-week dormancy line")
    worst = max((m["weeks_since"] or 0) for m in metrics.values())
    return f"Every repo committed within the last {worst} week{'s' if worst != 1 else ''}"


def make_charts(anchor: dt.datetime, anchor_date: str, weekly: dict,
                metrics: dict, all_names: list[str], portfolio: dict) -> tuple[str, str]:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    xs = [d.strftime("%m-%d") for d in _week_end_dates(anchor)]
    names = sorted(all_names)

    # --- trend chart: portfolio total (top) + per-repo, movers coloured ---
    fig, (axt, axb) = plt.subplots(
        2, 1, figsize=(9, 5.2), dpi=150, sharex=True,
        gridspec_kw={"height_ratios": [1, 2.4]},
    )
    ptot = [sum(weekly[n][i] for n in names) for i in range(TREND_WEEKS)]
    axt.fill_between(xs, ptot, color="#333333", alpha=0.15)
    axt.plot(xs, ptot, color="#222222", lw=2.2)
    axt.set_ylabel("total")
    axt.set_title(_chart1_title(portfolio, metrics), fontsize=11, loc="left", pad=8)
    axt.spines[["top", "right"]].set_visible(False)
    axt.tick_params(labelbottom=False)
    axt.grid(alpha=0.3)

    for name in names:
        mv = _is_mover(metrics[name])
        axb.plot(xs, weekly[name],
                 marker="o" if mv else None, ms=3,
                 lw=1.9 if mv else 1.0,
                 color=_repo_color(name, all_names) if mv else "#c7c7c7",
                 label=name if mv else f"{name} (quiet)",
                 zorder=3 if mv else 2)
    axb.set_ylabel("commits / week")
    axb.margins(x=0.02)
    axb.grid(alpha=0.3)
    axb.spines[["top", "right"]].set_visible(False)
    for lbl in axb.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_ha("right")
    axb.legend(fontsize=7.5, frameon=False, ncol=2)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.90, bottom=0.15, hspace=0.12)
    p1 = CHARTS_DIR / f"{anchor_date}_activity_13wk.png"
    fig.savefig(p1, facecolor="white")
    plt.close(fig)

    # --- snapshot chart: weeks since last commit -----------------------
    def wsl(n: str) -> int:
        v = metrics[n]["weeks_since"]
        return TREND_WEEKS if v is None else v

    order = sorted(all_names, key=lambda n: (wsl(n), n))
    y = np.arange(len(order))
    fig, ax = plt.subplots(figsize=(9, 0.62 * len(order) + 1.6), dpi=150)
    ax.barh(y, [wsl(n) for n in order],
            color=[_repo_color(n, all_names) for n in order], height=0.6)
    for yi, n in zip(y, order):
        v = metrics[n]["weeks_since"]
        txt = "0 (this week)" if v == 0 else (
            "none in 13 wks" if v is None else f"{v} wk{'s' if v != 1 else ''}")
        ax.text(wsl(n) + 0.15, yi, txt, va="center", fontsize=8)
    ax.axvline(DORMANT_WEEKS, ls="--", lw=1, color="#888",
               label=f"{DORMANT_WEEKS}-wk dormancy heuristic")
    ax.set_yticks(y)
    ax.set_yticklabels(order)
    ax.invert_yaxis()
    ax.set_xlabel("weeks since last commit")
    ax.set_xlim(0, TREND_WEEKS + 2)
    ax.set_title(_chart2_title(metrics), fontsize=11, loc="left", pad=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=8, frameon=False, loc="lower right")
    fig.tight_layout()
    p2 = CHARTS_DIR / f"{anchor_date}_weeks_since_commit.png"
    fig.savefig(p2, facecolor="white")
    plt.close(fig)

    return p1.name, p2.name


# --------------------------------------------------------------------- text
def build_kpi_header(p: dict) -> str:
    return (
        f"**{p['total_this_week']} commits** this week · 13-wk range "
        f"{p['range_lo']}–{p['range_hi']} · {p['arrow']} vs 4-week avg of "
        f"{p['avg4']:.0f}  \n"
        f"**{p['active_days']} / 7** active days · **{p['streak']}-week** active "
        f"streak · top repo = {p['top_share'] * 100:.0f}% of this week's commits"
    )


def build_table(metrics: dict) -> str:
    order = sorted(metrics,
                   key=lambda n: (metrics[n]["this_week"], metrics[n]["avg"]),
                   reverse=True)
    rows = ["| Repository | Commits | Days | Trend (13 wk) | Δ vs last wk | Status |",
            "|---|--:|--:|:--|--:|---|"]
    for n in order:
        m = metrics[n]
        d = m["delta"]
        rows.append(f"| **{n}** | {m['this_week']} | {m['active_days']} | "
                    f"`{m['spark']}` | {('+' + str(d)) if d > 0 else str(d)} | "
                    f"{m['label']} |")
    return "\n".join(rows)


def build_digest(anchor_date: str, metrics: dict, portfolio: dict,
                 week_ends: list[dt.date]) -> str:
    lines = [
        f"WEEK ENDING: {anchor_date}",
        (f"PORTFOLIO: {portfolio['total_this_week']} commits this week "
         f"({portfolio['wow_delta']:+d} vs last week); 4-week avg "
         f"{portfolio['avg4']:.1f}, 13-week range {portfolio['range_lo']}-"
         f"{portfolio['range_hi']}; {portfolio['active_days']}/7 active days; "
         f"{portfolio['streak']}-week active streak; {portfolio['active']} active, "
         f"{portfolio['dormant']} dormant, {portfolio['new']} new; top repo "
         f"{portfolio['top_repo']} = {portfolio['top_share'] * 100:.0f}% of this "
         f"week's commits; weekly-total coefficient of variation over 13 weeks = "
         f"{portfolio['cv']:.2f}."),
        "",
        "WEEK-ENDING DATES (oldest to newest): "
        + ", ".join(d.isoformat() for d in week_ends),
        "",
        "PER-REPO (weekly commit series oldest->newest, then metrics):",
    ]
    for n in sorted(metrics, key=lambda n: metrics[n]["this_week"], reverse=True):
        m = metrics[n]
        ws = "none" if m["weeks_since"] is None else m["weeks_since"]
        lines.append(
            f"- {n}: {m['series']} | this_week={m['this_week']} "
            f"last_week={m['last_week']} avg={m['avg']:.1f} delta={m['delta']:+d} "
            f"share={m['share'] * 100:.0f}% status={m['status']} new={m['is_new']} "
            f"active_days_this_week={m['active_days']}/7 streak={m['streak']}w "
            f"slope_4wk={m['slope4']:+.1f}/wk ({m['arrow']}) "
            f"weeks_since_last_commit={ws} age_days={m['age_days']}"
        )
    return "\n".join(lines)


def _split_sections(text: str) -> dict:
    out: dict[str, str] = {}
    cur, buf = None, []
    for line in text.splitlines():
        m = re.match(r"^###\s+(.*?)\s*$", line)
        if m and m.group(1) in _SECTIONS:
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    missing = [s for s in _SECTIONS if not out.get(s)]
    if missing:
        raise RuntimeError(f"claude output missing section(s) {missing}:\n{text[:400]}")
    return out


def link_repo_names(entry: str, repo_names: list[str]) -> str:
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


def assemble_entry(anchor_date: str, sections: dict, kpi_header: str,
                   table_md: str, chart1: str, chart2: str) -> str:
    return "\n".join([
        f"## Week ending {anchor_date}",
        "",
        "### Headline",
        sections["Headline"],
        "",
        "### This week at a glance",
        "",
        kpi_header,
        "",
        table_md,
        "",
        f"![Weekly commits by repository, 13 weeks to {anchor_date}]"
        f"(charts/{chart1})",
        "",
        "### Movements",
        sections["Movements"],
        "",
        f"![Weeks since last commit per repository](charts/{chart2})",
        "",
        "### Patterns & benchmarks",
        sections["Patterns & benchmarks"],
        "",
        "### Watchlist",
        sections["Watchlist"],
        "",
        "---",
    ]) + "\n"


def _to_prose_only(block: str) -> str:
    """Strip the table + chart images from a demoted (non-newest) entry."""
    out: list[str] = []
    skip = False
    for line in block.splitlines():
        m = re.match(r"^###\s+(.*?)\s*$", line)
        if m:
            skip = m.group(1) not in _PROSE_SECTIONS
        if skip or line.startswith("!["):
            continue
        out.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text + "\n"


def upsert_and_cap(entry: str, anchor_date: str) -> tuple[str, list[str]]:
    content = OUTPUT_FILE.read_text() if OUTPUT_FILE.exists() else INTRO
    marker = "\n---\n"
    idx = content.find(marker)
    head = content[: idx + len(marker)] if idx != -1 else INTRO
    rest = content[idx + len(marker):].lstrip("\n") if idx != -1 else ""

    old = [b.strip("\n") for b in
           re.split(r"(?=^## Week ending \d{4}-\d{2}-\d{2}\b)", rest, flags=re.M)
           if b.strip()]
    old = [b for b in old if not b.startswith(f"## Week ending {anchor_date}")]

    blocks = [entry.strip("\n")] + [_to_prose_only(b) for b in old]
    blocks = blocks[:MAX_ENTRIES]
    kept = [m.group(1) for b in blocks
            if (m := re.match(r"^## Week ending (\d{4}-\d{2}-\d{2})", b))]
    body = head.rstrip() + "\n\n" + "\n\n".join(blocks).rstrip() + "\n"
    return body, kept


def clean_orphan_charts(keep_date: str) -> int:
    if not CHARTS_DIR.exists():
        return 0
    removed = 0
    for p in CHARTS_DIR.glob("*.png"):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_", p.name)
        if m and m.group(1) != keep_date:
            p.unlink()
            removed += 1
    return removed


def generate_entry(digest: str, table: str, print_prompt: bool = False) -> str:
    prompt = PROMPT_TEMPLATE.format(
        digest=digest, table=table, word_target=PROSE_WORD_TARGET
    )
    if print_prompt:
        print("\n----- PROMPT SENT TO CLAUDE -----")
        print(prompt)
        print("----- END PROMPT -----\n")
    proc = subprocess.run(
        [CLAUDE_BIN, "-p", prompt, "--output-format", "text",
         "--permission-mode", "dontAsk", "--model", CLAUDE_MODEL],
        capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S, cwd=str(HERE),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {proc.returncode}\n{proc.stderr.strip()}"
        )
    text = proc.stdout.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rstrip()
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return text


# ---------------------------------------------------------------------- main
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the weekly GitHub trend report.",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help=f"anchor the week to this date @ {RUN_TIME_LOCAL} local instead of "
             f"the most recent scheduled slot (for testing)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="write output/_preview.md + charts only; touch no real log files",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="build the report from FABRICATED data (implies --dry-run) - for "
             "judging layout while the real GitHub history is thin",
    )
    parser.add_argument(
        "--print-prompt", action="store_true",
        help="also print the exact prompt sent to the claude CLI",
    )
    args = parser.parse_args(argv)
    if args.demo:
        args.dry_run = True
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
    anchor_date = anchor.date().isoformat()
    week_ends = _week_end_dates(anchor)

    mode = "DEMO" if args.demo else ("DRY RUN" if args.dry_run else "run")
    log(f"{mode} start - week ending {anchor_date} "
        f"(13-week window to {anchor:%Y-%m-%d %H:%M} {RUN_TIMEZONE})")

    try:
        weekly, metrics, portfolio = (
            demo_trends(anchor) if args.demo else gather_trends(anchor)
        )
    except GatherError as exc:
        log(f"ERROR gathering GitHub activity: {exc}")
        return 1
    if not metrics:
        log("ERROR: no owned repositories found")
        return 1

    log(f"gathered - {portfolio['total_this_week']} commits this week across "
        f"{len(metrics)} repos ({portfolio['wow_delta']:+d} WoW); "
        f"{portfolio['dormant']} dormant")

    chart1, chart2 = make_charts(anchor, anchor_date, weekly, metrics,
                                 list(metrics), portfolio)
    kpi_header = build_kpi_header(portfolio)
    table_md = build_table(metrics)
    digest = build_digest(anchor_date, metrics, portfolio, week_ends)

    try:
        sections = _split_sections(
            generate_entry(digest, table_md, print_prompt=args.print_prompt)
        )
    except Exception as exc:  # noqa: BLE001 - keep the existing file intact
        log(f"ERROR: {exc}")
        return 1

    words = sum(len(sections[s].split()) for s in _SECTIONS)
    if words > PROSE_WORD_TARGET + 100:
        log(f"WARNING: prose is {words} words (target {PROSE_WORD_TARGET})")

    entry = link_repo_names(
        assemble_entry(anchor_date, sections, kpi_header, table_md, chart1, chart2),
        list(metrics),
    )
    body, kept = upsert_and_cap(entry, anchor_date)

    if args.dry_run:
        PREVIEW_FILE.write_text(body)
        log(f"{mode} - {OUTPUT_FILE.name} NOT touched; preview at "
            f"{PREVIEW_FILE.relative_to(PROJECTS_ROOT)} ({len(body)} bytes); "
            f"charts under {CHARTS_DIR.relative_to(PROJECTS_ROOT)}/ "
            f"- open the preview in Markdown view")
        return 0

    OUTPUT_FILE.write_text(body)
    removed = clean_orphan_charts(anchor_date)
    log(f"wrote {OUTPUT_FILE.relative_to(PROJECTS_ROOT)} ({len(body)} bytes); "
        f"{len(kept)} entr{'y' if len(kept) == 1 else 'ies'} kept; "
        f"{removed} old chart(s) removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

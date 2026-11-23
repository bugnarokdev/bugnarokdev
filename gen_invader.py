#!/usr/bin/env python3
"""Generate a SMIL-animated SVG of BUGNAROK shooting a GitHub contribution grid.

No JavaScript. The output is safe to embed in a GitHub profile README.
Contribution data is fetched from the public GitHub contributions API when
possible; otherwise a clearly labeled sample grid is used.

Usage:
  python3 gen_invader.py --user bugnarokdev --out output/contrib-invader.svg
  python3 gen_invader.py --from-json data/contributions.json --out output/contrib-invader.svg
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

# Official, token-free source: the public contribution calendar rendered by GitHub
# itself. The previous third-party mirror (github-contributions-api.jogruber.de)
# silently served halved per-day counts and dropped the most recent days, which
# made the published SVG disagree with the profile.
GITHUB_CONTRIB_URL = "https://github.com/users/{user}/contributions?from={year}-01-01&to={year}-12-31"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GRAPHQL_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks {
          contributionDays { date contributionCount }
        }
      }
    }
  }
}
"""
HTTP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Brand
BG = "#0a0a0a"
BG_PANEL = "#080808"
FRAME = "#ff6a00"
FRAME_DIM = "#7a3200"
TEXT = "#c8c8c8"
TEXT_DIM = "#6a6a6a"
TEXT_BRIGHT = "#f0f0f0"
ORANGE = "#ff6a00"
ORANGE_HOT = "#ffb347"
ORANGE_CORE = "#ff3d00"
LEVEL_COLORS = {
    0: "#161616",
    1: "#3b1d00",
    2: "#7a3b00",
    3: "#c65600",
    4: "#ff6a00",
}
FUTURE = "#101010"

# Grid geometry (GitHub-like)
CELL = 12
GAP = 3
STEP = CELL + GAP  # 15
COLS = 53
ROWS = 7
PAD_L = 42
PAD_R = 24
PAD_T = 58
PAD_B = 86
FRAME_PAD = 12

# Pixel art: hooded BUGNAROK with orange rim-light, glowing eyes,
# and space-invader badges on the headphone cups. 20 x 24.
PALETTE: dict[str, str | None] = {
    ".": None,
    "D": "#0e0a08",
    "B": "#24160e",
    "M": "#482810",
    "U": "#6e3a12",
    "F": "#965c30",
    "O": "#ff6a00",
    "Q": "#ff5000",
    "Y": "#ffdc78",
    "C": "#ffaa32",
    "N": "#ff2800",
    "S": "#d2a882",
    "H": "#9a684c",
    "G": "#60402e",
    "P": "#18181e",
    "L": "#d2d2da",
    "I": "#ff840a",
    "T": "#ff7e14",
    "X": "#140c08",
    "A": "#3a2010",
    "Z": "#0c0806",
}

SPRITE_IDLE = [
    ".........QQ.........",
    "........QUUQ........",
    ".......QMMMMQ.......",
    "......QMBBBBMQ......",
    ".....QMBXXXXBMQ.....",
    "...QMBYNX..XNYBMQ...",
    "...QMBCYY..YYCBMQ...",
    "...QMBHSS..SSHBMQ...",
    "IPIQMBSSHHHHSSBMQIPI",
    "IIIQMBSSHGGHSSBMQIII",
    "IPIQMBSSHGGHSSBMQIPI",
    "...QMBHHGGGGHHBMQ...",
    "...QMBBB.TT.BBBMQ...",
    "....QQFBBTTBBFQQ....",
    "....QQFBBBBBBFQQ....",
    "....FFBBBBBBBBFF....",
    "...FFBBBBBBBBBBFF...",
    "...FBBBBAAAABBBBF...",
    "...BBBBBAAAABBBBB...",
    "....BBB......BBB....",
    "....BBB......BBB....",
    ".....BB......BB.....",
    ".....MM......MM.....",
    ".....MM......MM.....",
]

SPRITE_WALK = [
    ".........QQ.........",
    "........QUUQ........",
    ".......QMMMMQ.......",
    "......QMBBBBMQ......",
    ".....QMBXXXXBMQ.....",
    "...QMBYNX..XNYBMQ...",
    "...QMBCYY..YYCBMQ...",
    "...QMBHSS..SSHBMQ...",
    "IPIQMBSSHHHHSSBMQIPI",
    "IIIQMBSSHGGHSSBMQIII",
    "IPIQMBSSHGGHSSBMQIPI",
    "...QMBHHGGGGHHBMQ...",
    "...QMBBB.TT.BBBMQ...",
    "....QQFBBTTBBFQQ....",
    "....QQFBBBBBBFQQ....",
    "....FFBBBBBBBBFF....",
    "...FFBBBBBBBBBBFF...",
    "...FBBBBAAAABBBBF...",
    "...BBBBBAAAABBBBB...",
    "....BB........BBB...",
    "....BB........BB....",
    "....BB.......BB.....",
    "....MM.......MM.....",
    ".....M.......MM.....",
]

SPRITE_W = 20
SPRITE_H = 24
SPRITE_SCALE = 2  # display size 40 x 48


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def sunday_on_or_before(d: date) -> date:
    # Monday=0 ... Sunday=6 → days since Sunday
    return d - timedelta(days=(d.weekday() + 1) % 7)


def build_grid(
    contribs_by_date: dict[str, dict[str, int]],
    end: date,
) -> tuple[list[list[dict[str, Any] | None]], date, date]:
    """53x7 grid, rows Sunday→Saturday, last column is the week of `end`."""
    week_start = sunday_on_or_before(end)
    first = week_start - timedelta(weeks=COLS - 1)
    last = week_start + timedelta(days=6)
    grid: list[list[dict[str, Any] | None]] = [
        [None for _ in range(COLS)] for _ in range(ROWS)
    ]
    d = first
    while d <= last:
        col = (d - first).days // 7
        row = (d.weekday() + 1) % 7  # Sunday = 0
        key = d.isoformat()
        rec = contribs_by_date.get(key, {"count": 0, "level": 0})
        future = d > end
        grid[row][col] = {
            "date": key,
            "count": 0 if future else int(rec.get("count") or 0),
            "level": 0 if future else int(rec.get("level") or 0),
            "future": future,
        }
        d += timedelta(days=1)
    return grid, first, last


def _count_from_tooltip(text: str) -> int:
    """Extract the contribution count from a GitHub calendar tooltip."""
    if not text or text.startswith("No contributions"):
        return 0
    m = re.search(r"(\d+)\s+contributions?", text)
    return int(m.group(1)) if m else 0


def _parse_calendar(html_text: str) -> list[dict]:
    """Parse GitHub's rendered contribution calendar into dated rows.

    GitHub embeds one <td data-date=... data-level=...> per day plus a matching
    <tool-tip> carrying the exact count. Levels are recalculated locally so the
    palette stays stable across pages instead of trusting the per-page scale.
    """
    tooltips = {
        tid: " ".join(html.unescape(re.sub(r"<[^>]+>", "", body)).split())
        for tid, body in re.findall(
            r'<tool-tip[^>]*for="([^"]+)"[^>]*>(.*?)</tool-tip>', html_text, re.S
        )
    }
    rows: list[dict] = []
    for match in re.finditer(r"<td\b[^>]*>", html_text):
        tag = match.group(0)
        dated = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', tag)
        if not dated:
            continue
        tip_id = re.search(r'id="([^"]+)"', tag)
        count = _count_from_tooltip(tooltips.get(tip_id.group(1), "") if tip_id else "")
        rows.append({"date": dated.group(1), "count": count, "level": 0})
    rows.sort(key=lambda r: r["date"])
    return rows


def _assign_levels(rows: list[dict]) -> None:
    """Map counts to the 0..4 palette using GitHub-like quartiles of active days."""
    counts = sorted(r["count"] for r in rows if r["count"] > 0)
    if not counts:
        return

    def quantile(p: float) -> int:
        idx = min(len(counts) - 1, max(0, int(round(p * (len(counts) - 1)))))
        return counts[idx]

    q25, q50, q75 = quantile(0.25), quantile(0.50), quantile(0.75)
    for row in rows:
        c = row["count"]
        if c <= 0:
            row["level"] = 0
        elif c <= q25:
            row["level"] = 1
        elif c <= q50:
            row["level"] = 2
        elif c <= q75:
            row["level"] = 3
        else:
            row["level"] = 4


def _fetch_graphql_year(user: str, year: int, token: str) -> list[dict]:
    """Fetch one year of contributions from GitHub's official GraphQL API.

    Preferred in CI: authenticated, stable markup, and it includes private
    contributions the caller is allowed to see. Window is capped at one year
    because contributionsCollection rejects longer ranges.
    """
    payload = json.dumps(
        {
            "query": GRAPHQL_QUERY,
            "variables": {
                "login": user,
                "from": f"{year}-01-01T00:00:00Z",
                "to": f"{year}-12-31T23:59:59Z",
            },
        }
    ).encode()
    req = urllib.request.Request(
        GITHUB_GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "bugnarok-contrib-invader/2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("errors"):
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    node = (body.get("data") or {}).get("user")
    if not node:
        raise RuntimeError(f"GraphQL returned no user for {user}")
    weeks = node["contributionsCollection"]["contributionCalendar"]["weeks"]
    rows = [
        {"date": day["date"], "count": int(day["contributionCount"]), "level": 0}
        for week in weeks
        for day in week["contributionDays"]
    ]
    if not rows:
        raise ValueError(f"GraphQL returned an empty calendar for {year}")
    return rows


def fetch_contributions(user: str, years: list[int]) -> tuple[list[dict], dict, str]:
    """Fetch the official GitHub contribution calendar for the given years.

    Uses the authenticated GraphQL API when a token is present (CI), otherwise
    the public calendar page. Retries transient failures, merges every year into
    one date-sorted series, and raises if nothing usable comes back so the caller
    can fall back to the labeled sample instead of publishing wrong numbers.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    merged: dict[str, dict] = {}
    source = ""
    for year in years:
        last_error: Exception | None = None
        rows: list[dict] = []
        for attempt in range(3):
            try:
                if token:
                    rows = _fetch_graphql_year(user, year, token)
                    source = "GitHub GraphQL API"
                else:
                    url = GITHUB_CONTRIB_URL.format(user=user, year=year)
                    req = urllib.request.Request(
                        url,
                        headers={
                            "User-Agent": HTTP_UA,
                            "Accept": "text/html,application/xhtml+xml",
                            "Cache-Control": "no-cache",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        body = resp.read().decode("utf-8", "replace")
                    rows = _parse_calendar(body)
                    source = "github.com/users/{user}/contributions".format(user=user)
                if not rows:
                    raise ValueError(f"no contribution rows for {year}")
                break
            except Exception as exc:  # network, auth, or markup drift
                last_error = exc
                if attempt < 2:
                    time.sleep(2 + attempt * 3)
        else:
            raise RuntimeError(f"GitHub contributions fetch failed for {year}: {last_error}")
        for row in rows:
            merged[row["date"]] = row

    unique = [merged[k] for k in sorted(merged)]
    _assign_levels(unique)
    totals = {str(y): 0 for y in years}
    for row in unique:
        year_key = row["date"][:4]
        if year_key in totals:
            totals[year_key] += row["count"]
    return unique, totals, source or "GitHub"



def load_json(path: Path) -> tuple[list[dict], dict, str]:
    data = json.loads(path.read_text())
    rows = data.get("contributions") or data
    if not isinstance(rows, list):
        raise ValueError(f"Unexpected JSON in {path}")
    totals = data.get("total") if isinstance(data, dict) else {}
    source = "file:" + str(path)
    return rows, totals or {}, source


def sample_contributions(end: date) -> tuple[list[dict], dict, str]:
    """Deterministic sample year, clearly labeled in the SVG."""
    first = date(end.year - 1, 1, 1)
    last = date(end.year, 12, 31)
    rows = []
    total = 0
    d = first
    while d <= last:
        # A readable wavy pattern, denser recently, zero on some weeks
        age = (end - d).days
        if d > end:
            count = 0
        else:
            wave = (d.toordinal() * 7 + d.weekday() * 3) % 11
            if d.weekday() == 6 or wave == 0:
                count = 0
            elif age < 30:
                count = 4 + (wave % 5)
            elif age < 90:
                count = 2 + (wave % 4)
            else:
                count = wave % 4
        if count == 0:
            level = 0
        elif count <= 2:
            level = 1
        elif count <= 4:
            level = 2
        elif count <= 6:
            level = 3
        else:
            level = 4
        rows.append({"date": d.isoformat(), "count": count, "level": level})
        if d.year == end.year:
            total += count
        d += timedelta(days=1)
    return rows, {str(end.year): total}, "sample"


# ---------------------------------------------------------------------------
# Animation plan
# ---------------------------------------------------------------------------

def plan_shots(
    grid: list[list[dict[str, Any] | None]],
    cell_origin: tuple[float, float],
    sprite_y: float,
    muzzle_offset_y: float,
) -> tuple[list[dict[str, Any]], float, int]:
    """Left-to-right, bottom-to-top (nearest cell first). Returns events + loop dur."""
    ox, oy = cell_origin
    intro = 1.15
    outro = 1.40
    rebuild = 0.90
    t = intro
    events: list[dict[str, Any]] = []
    prev_col: int | None = None
    char_x_idle = None

    for col in range(COLS):
        targets = []
        for row in range(ROWS - 1, -1, -1):  # bottom (Sat) → top (Sun)
            cell = grid[row][col]
            if cell and not cell["future"] and cell["level"] > 0:
                targets.append((row, cell))
        if not targets:
            continue
        cx = ox + col * STEP + CELL / 2
        if prev_col is None:
            # start already under the first column
            t_arrive = t
            char_x_idle = cx
        else:
            dist = abs(col - prev_col)
            move = min(0.42, 0.10 + 0.035 * dist)
            t += move
            t_arrive = t
        for row, cell in targets:
            cy = oy + row * STEP + CELL / 2
            travel = 0.10 + 0.026 * row  # top rows take longer
            t_fire = t
            t_hit = t_fire + travel
            events.append(
                {
                    "col": col,
                    "row": row,
                    "cx": cx,
                    "cy": cy,
                    "count": cell["count"],
                    "level": cell["level"],
                    "t_arrive": t_arrive,
                    "t_fire": t_fire,
                    "t_hit": t_hit,
                    "char_x": cx,
                }
            )
            t = t_hit + 0.05
        prev_col = col

    if not events:
        loop = intro + 4.0 + outro
        return events, loop, 0

    last_t = events[-1]["t_hit"]
    loop = last_t + outro + rebuild
    return events, loop, len(events)


def character_translate_anim(events: list[dict[str, Any]], loop: float, sprite_y: float) -> str:
    """Build a single looping animateTransform for the player X position."""
    if not events:
        return ""
    # keyTimes / values in 0..1 of the full loop
    # Sequence: sit at first col until first fire, then follow each event's char_x
    # Hold at a column until leaving for the next one.
    points: list[tuple[float, float]] = []
    first = events[0]
    points.append((0.0, first["char_x"]))
    for i, ev in enumerate(events):
        # arrive / stay
        points.append((ev["t_arrive"], ev["char_x"]))
        points.append((ev["t_fire"], ev["char_x"]))
        # stay through hit
        points.append((ev["t_hit"], ev["char_x"]))
        nxt = events[i + 1] if i + 1 < len(events) else None
        if nxt and nxt["char_x"] != ev["char_x"]:
            # hold until next arrive starts (already at t_hit + recover, arrive is later)
            hold = max(ev["t_hit"], nxt["t_arrive"] - 0.001)
            points.append((hold, ev["char_x"]))
    # hold at last column through outro, then snap back at loop end
    last = events[-1]
    points.append((loop - 0.05, last["char_x"]))
    points.append((loop, first["char_x"]))

    # sanitize: strictly increasing keyTimes, merge equals
    cleaned: list[tuple[float, float]] = []
    for t, x in points:
        kt = max(0.0, min(1.0, t / loop))
        if cleaned and abs(kt - cleaned[-1][0]) < 1e-5:
            cleaned[-1] = (cleaned[-1][0], x)
        else:
            cleaned.append((kt, x))
    if cleaned[0][0] != 0.0:
        cleaned.insert(0, (0.0, cleaned[0][1]))
    if cleaned[-1][0] != 1.0:
        cleaned.append((1.0, cleaned[0][1]))

    # SMIL keyTimes must be strictly increasing; bump tiny collisions
    fixed: list[tuple[float, float]] = [cleaned[0]]
    for t, x in cleaned[1:]:
        if t <= fixed[-1][0]:
            t = min(1.0, fixed[-1][0] + 1e-4)
        fixed.append((t, x))
    if fixed[-1][0] < 1.0:
        fixed.append((1.0, fixed[0][1]))
    # final may have exceeded 1 due to bumps; rescale not needed if careful
    if fixed[-1][0] > 1.0:
        fixed[-1] = (1.0, fixed[-1][1])

    values = ";".join(f"{x:.2f},{sprite_y:.2f}" for _, x in fixed)
    key_times = ";".join(f"{t:.5f}" for t, _ in fixed)
    return (
        f'<animateTransform attributeName="transform" type="translate" '
        f'values="{values}" keyTimes="{key_times}" dur="{loop:.3f}s" '
        f'repeatCount="indefinite" calcMode="linear"/>'
    )


# ---------------------------------------------------------------------------
# SVG pieces
# ---------------------------------------------------------------------------

def sprite_group(sprite: list[str], opacity_anim: str | None = None) -> str:
    parts = [f'<g class="sprite"{opacity_anim or ""}>']
    for y, row in enumerate(sprite):
        for x, ch in enumerate(row):
            color = PALETTE.get(ch)
            if not color:
                continue
            parts.append(
                f'<rect x="{x * SPRITE_SCALE}" y="{y * SPRITE_SCALE}" '
                f'width="{SPRITE_SCALE}" height="{SPRITE_SCALE}" fill="{color}"/>'
            )
    parts.append("</g>")
    return "\n".join(parts)


def month_labels(first: date, ox: float, oy: float) -> str:
    labels = []
    last_col_used = -99
    last_month = None
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for col in range(COLS):
        d = first + timedelta(weeks=col)
        week_days = [d + timedelta(days=i) for i in range(7)]
        # Label the first column of a newly visible month (including a partial first month).
        month_here = None
        for day in week_days:
            if last_month is None or day.month != last_month:
                month_here = day.month
                break
        if month_here is None:
            continue
        if last_col_used >= 0 and col - last_col_used < 2:
            last_month = month_here
            continue
        last_col_used = col
        last_month = month_here
        x = ox + col * STEP
        labels.append(
            f'<text x="{x:.1f}" y="{oy - 8:.1f}" class="lbl">{names[month_here - 1]}</text>'
        )
    return "\n".join(labels)


def day_labels(ox: float, oy: float) -> str:
    # GitHub shows Mon, Wed, Fri
    out = []
    names = {1: "Mon", 3: "Wed", 5: "Fri"}
    for row, name in names.items():
        y = oy + row * STEP + CELL - 1
        out.append(
            f'<text x="{ox - 8:.1f}" y="{y:.1f}" class="lbl day" text-anchor="end">{name}</text>'
        )
    return "\n".join(out)


def hud_frame(width: float, height: float) -> str:
    m = 6
    L = 22
    return f"""
  <g class="hud-frame" fill="none" stroke="{FRAME}" stroke-width="1.4" stroke-linecap="square">
    <path d="M {m + L} {m} H {m} V {m + L}"/>
    <path d="M {width - m - L} {m} H {width - m} V {m + L}"/>
    <path d="M {m + L} {height - m} H {m} V {height - m - L}"/>
    <path d="M {width - m - L} {height - m} H {width - m} V {height - m - L}"/>
    <animate attributeName="opacity" values="0.65;1;0.65" dur="3.2s" repeatCount="indefinite"/>
  </g>
  <g fill="none" stroke="{FRAME}" stroke-width="0.6" opacity="0.35">
    <rect x="10" y="10" width="{width - 20}" height="{height - 20}"/>
  </g>
"""


def fmt_dur(seconds: float) -> str:
    return f"{seconds:.3f}s"


# ---------------------------------------------------------------------------
# Main SVG build
# ---------------------------------------------------------------------------

def build_svg(
    grid: list[list[dict[str, Any] | None]],
    first: date,
    end: date,
    total: int,
    source_label: str,
    is_sample: bool,
    user: str,
) -> str:
    grid_w = COLS * STEP - GAP
    grid_h = ROWS * STEP - GAP
    width = FRAME_PAD * 2 + PAD_L + grid_w + PAD_R
    height = FRAME_PAD * 2 + PAD_T + grid_h + PAD_B
    ox = FRAME_PAD + PAD_L
    oy = FRAME_PAD + PAD_T
    sprite_w = SPRITE_W * SPRITE_SCALE
    sprite_h = SPRITE_H * SPRITE_SCALE
    # Character is positioned by the center-top of the sprite (muzzle / hood tip)
    # We translate the player group so (0,0) is the sprite's top-left, but animation
    # uses the muzzle X (column center) minus half width.
    sprite_y = oy + grid_h + 10
    muzzle_local_x = sprite_w / 2
    muzzle_local_y = 2  # near hood tip

    events, loop, n_targets = plan_shots(grid, (ox, oy), sprite_y, muzzle_local_y)

    # Character animateTransform uses the translation of the sprite's top-left,
    # so we subtract half the sprite width to center under the column.
    for ev in events:
        ev["char_x_draw"] = ev["char_x"]  # keep center for projectiles
        ev["char_x"] = ev["char_x"] - muzzle_local_x

    char_anim = character_translate_anim(events, loop, sprite_y)

    year_span = first.year if first.year == end.year else f"{first.year}-{end.year}"
    title_main = "CONTRIBUTION_MAP"
    total_lbl = f"{total:,} CONTRIBUTIONS"
    if is_sample:
        title_main = "CONTRIBUTION_MAP  ·  SAMPLE DATA"
        source_label = "sample data (not live GitHub contributions)"

    # Cells
    cell_svg = ["<g id='cells'>"]
    for row in range(ROWS):
        for col in range(COLS):
            cell = grid[row][col]
            if cell is None:
                continue
            x = ox + col * STEP
            y = oy + row * STEP
            if cell["future"]:
                fill = FUTURE
            else:
                fill = LEVEL_COLORS.get(cell["level"], LEVEL_COLORS[0])
            rx = 2
            cid = f"c{col}_{row}"
            extra = ""
            if cell["level"] >= 4:
                extra = f' stroke="{ORANGE_HOT}" stroke-width="0.6"'
            # Destruction anim if this cell is a target
            ev = next((e for e in events if e["col"] == col and e["row"] == row), None)
            anim = ""
            if ev:
                t0 = ev["t_hit"] / loop
                t1 = min(1.0, (ev["t_hit"] + 0.16) / loop)
                # visible until hit, then fade, stay gone, restore in last 0.9s (rebuild)
                tr = (loop - 0.90) / loop
                anim = (
                    f'<animate attributeName="opacity" '
                    f'values="1;1;0;0;1" '
                    f'keyTimes="0;{t0:.5f};{t1:.5f};{tr:.5f};1" '
                    f'dur="{loop:.3f}s" repeatCount="indefinite"/>'
                )
                # slight scale burst via a sibling flash, keep the rect simple
            title = ""
            if cell["count"]:
                title = f'<title>{cell["count"]} contribution{"s" if cell["count"] != 1 else ""} on {cell["date"]}</title>'
            cell_svg.append(
                f'<rect id="{cid}" x="{x:.1f}" y="{y:.1f}" width="{CELL}" height="{CELL}" '
                f'rx="{rx}" fill="{fill}"{extra}>{title}{anim}</rect>'
            )
    cell_svg.append("</g>")

    # Flashes + particles at impact
    fx_svg = ['<g id="impacts" fill="#ffb347">']
    for i, ev in enumerate(events):
        t0 = ev["t_hit"] / loop
        t1 = min(1.0, (ev["t_hit"] + 0.12) / loop)
        t2 = min(1.0, (ev["t_hit"] + 0.22) / loop)
        fx_svg.append(
            f'<rect x="{ev["cx"] - 3:.1f}" y="{ev["cy"] - 3:.1f}" width="6" height="6" rx="1" opacity="0">'
            f'<animate attributeName="opacity" values="0;0;1;0;0" '
            f'keyTimes="0;{t0:.5f};{((t0+t1)/2):.5f};{t1:.5f};1" '
            f'dur="{loop:.3f}s" repeatCount="indefinite"/>'
            f'</rect>'
        )
        # 4 tiny shards
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            x0, y0 = ev["cx"] - 1, ev["cy"] - 1
            x1, y1 = ev["cx"] + dx * 7 - 1, ev["cy"] + dy * 7 - 1
            fx_svg.append(
                f'<rect x="{x0:.1f}" y="{y0:.1f}" width="2" height="2" opacity="0">'
                f'<animate attributeName="opacity" values="0;0;1;0;0" '
                f'keyTimes="0;{t0:.5f};{t1:.5f};{t2:.5f};1" '
                f'dur="{loop:.3f}s" repeatCount="indefinite"/>'
                f'<animate attributeName="x" values="{x0:.1f};{x0:.1f};{x1:.1f};{x1:.1f}" '
                f'keyTimes="0;{t0:.5f};{t2:.5f};1" '
                f'dur="{loop:.3f}s" repeatCount="indefinite"/>'
                f'<animate attributeName="y" values="{y0:.1f};{y0:.1f};{y1:.1f};{y1:.1f}" '
                f'keyTimes="0;{t0:.5f};{t2:.5f};1" '
                f'dur="{loop:.3f}s" repeatCount="indefinite"/>'
                f'</rect>'
            )
    fx_svg.append("</g>")

    # Projectiles
    proj = ['<g id="projectiles">']
    for ev in events:
        tf = ev["t_fire"] / loop
        th = ev["t_hit"] / loop
        # start at muzzle (character center x, sprite_y + muzzle_local_y)
        x = ev["char_x_draw"] - 1.5
        y0 = sprite_y + muzzle_local_y
        y1 = ev["cy"]
        proj.append(
            f'<g opacity="0">'
            f'<rect x="{x:.1f}" y="{y0:.1f}" width="3" height="8" rx="1" fill="{ORANGE}"/>'
            f'<rect x="{x + 0.5:.1f}" y="{y0:.1f}" width="2" height="4" rx="0.5" fill="{ORANGE_HOT}"/>'
            f'<animate attributeName="opacity" values="0;0;1;1;0;0" '
            f'keyTimes="0;{tf:.5f};{tf:.5f};{th:.5f};{th:.5f};1" '
            f'dur="{loop:.3f}s" repeatCount="indefinite"/>'
            f'<animateTransform attributeName="transform" type="translate" '
            f'values="0,0; 0,0; 0,{y1 - y0:.1f}; 0,{y1 - y0:.1f}" '
            f'keyTimes="0;{tf:.5f};{th:.5f};1" '
            f'dur="{loop:.3f}s" repeatCount="indefinite"/>'
            f'</g>'
        )
    proj.append("</g>")

    # Progress bar: shrinks as targets fall
    bar_x = ox
    bar_y = height - FRAME_PAD - 18
    bar_w = grid_w
    bar_vals = " ".join(
        # width goes from full to 0 across the shooting window
        []
    )
    if events:
        # keyframes: full until intro, then linear-ish by shot index, empty after last hit, refill in rebuild
        n = len(events)
        widths = []
        times = []
        widths.append(bar_w)
        times.append(0.0)
        widths.append(bar_w)
        times.append(events[0]["t_fire"] / loop)
        for i, ev in enumerate(events):
            remaining = n - (i + 1)
            widths.append(bar_w * remaining / n)
            times.append(ev["t_hit"] / loop)
        widths.append(0)
        times.append((events[-1]["t_hit"] + 0.2) / loop)
        widths.append(0)
        times.append((loop - 0.90) / loop)
        widths.append(bar_w)
        times.append(1.0)
        # fix keyTimes
        kt = [times[0]]
        vw = [widths[0]]
        for t, w in zip(times[1:], widths[1:]):
            if t <= kt[-1]:
                t = min(1.0, kt[-1] + 1e-4)
            kt.append(t)
            vw.append(w)
        if kt[-1] < 1.0:
            kt.append(1.0)
            vw.append(vw[0])
        bar_anim = (
            f'<animate attributeName="width" values="{" ; ".join(f"{w:.1f}" for w in vw)}" '
            f'keyTimes="{";".join(f"{t:.5f}" for t in kt)}" dur="{loop:.3f}s" repeatCount="indefinite"/>'
        )
    else:
        bar_anim = ""

    # Muzzle flashes at fire time (drawn in player-local coords, so they follow the character)
    muzzle = ['<g id="muzzle">']
    if events:
        # A single flash rect at the hood tip whose opacity pulses at each fire.
        # Build a values/keyTimes list over the loop.
        # 0 most of the time, 1 at each t_fire for ~0.08s
        samples: list[tuple[float, float]] = [(0.0, 0.0)]
        for ev in events:
            t0 = ev["t_fire"] / loop
            t1 = (ev["t_fire"] + 0.08) / loop
            if t0 <= samples[-1][0]:
                t0 = samples[-1][0] + 1e-4
            samples.append((t0, 1.0))
            if t1 <= samples[-1][0]:
                t1 = samples[-1][0] + 1e-4
            samples.append((min(1.0, t1), 0.0))
        if samples[-1][0] < 1.0:
            samples.append((1.0, 0.0))
        mz_values = ";".join(str(v) for _, v in samples)
        mz_times = ";".join(f"{t:.5f}" for t, _ in samples)
        mx = muzzle_local_x
        my = muzzle_local_y
        muzzle.append(
            f'<ellipse cx="{mx:.1f}" cy="{my:.1f}" rx="5" ry="4" fill="{ORANGE_HOT}" opacity="0">'
            f'<animate attributeName="opacity" values="{mz_values}" keyTimes="{mz_times}" '
            f'dur="{loop:.3f}s" repeatCount="indefinite"/>'
            f'</ellipse>'
        )
    muzzle.append("</g>")

    # Walk cycle: idle visible by default, walk frame half the time.
    # Both frames sit at (0,0) in the player group.
    walk_anim_idle = (
        f'<animate attributeName="opacity" values="1;0;1;0;1" keyTimes="0;0.25;0.5;0.75;1" '
        f'dur="0.40s" repeatCount="indefinite"/>'
    )
    walk_anim_walk = (
        f'<animate attributeName="opacity" values="0;1;0;1;0" keyTimes="0;0.25;0.5;0.75;1" '
        f'dur="0.40s" repeatCount="indefinite"/>'
    )

    # Subtle vertical bob
    bob = (
        '<animateTransform attributeName="transform" type="translate" '
        'values="0,0; 0,-1.5; 0,0" dur="0.4s" repeatCount="indefinite"/>'
    )

    # Default starting translation if no events
    if not events:
        start_x = ox + (COLS // 2) * STEP + CELL / 2 - muzzle_local_x
        char_anim = (
            f'<animateTransform attributeName="transform" type="translate" '
            f'values="{start_x:.2f},{sprite_y:.2f}; {start_x:.2f},{sprite_y:.2f}" '
            f'dur="{loop:.3f}s" repeatCount="indefinite"/>'
        )

    shadow = (
        f'<ellipse cx="{muzzle_local_x:.1f}" cy="{sprite_h - 1}" rx="10" ry="2.4" '
        f'fill="#000" opacity="0.45"/>'
    )

    idle_svg = sprite_group(SPRITE_IDLE)
    walk_svg = sprite_group(SPRITE_WALK)
    # inject walk-cycle opacity into the root <g class="sprite">
    idle_svg = idle_svg.replace(
        '<g class="sprite">',
        '<g class="sprite"><animate attributeName="opacity" values="1;0;1;0;1" '
        'keyTimes="0;0.25;0.5;0.75;1" dur="0.40s" repeatCount="indefinite"/>',
        1,
    )
    walk_svg = walk_svg.replace(
        '<g class="sprite">',
        '<g class="sprite"><animate attributeName="opacity" values="0;1;0;1;0" '
        'keyTimes="0;0.25;0.5;0.75;1" dur="0.40s" repeatCount="indefinite"/>',
        1,
    )
    player = f"""
  <g id="player">
    {char_anim}
    <g id="player-bob">
      {bob}
      {shadow}
      {idle_svg}
      {walk_svg}
      {''.join(muzzle)}
    </g>
  </g>
"""

    sample_banner = ""
    if is_sample:
        sample_banner = (
            f'<text x="{width/2:.1f}" y="{height - FRAME_PAD - 32:.1f}" text-anchor="middle" '
            f'class="sample">SAMPLE DATA: not live contributions</text>'
        )

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}"
     role="img" aria-label="BUGNAROK shooting the GitHub contribution grid of {escape(user)}">
  <title>BUGNAROK vs. the contribution grid: {escape(user)}</title>
  <desc>{n_targets} contribution days from {first.isoformat()} to {end.isoformat()}. Source: {escape(source_label)}. Loop {loop:.1f}s. Pure SMIL, no JavaScript.</desc>
  <defs>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="1.6" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <linearGradient id="bg-grad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#0c0c0c"/>
      <stop offset="100%" stop-color="#070707"/>
    </linearGradient>
  </defs>
  <style>
    .lbl {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
            font-size: 10px; fill: {TEXT_DIM}; }}
    .day {{ font-size: 9px; }}
    .title {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
              font-size: 11px; fill: {ORANGE}; letter-spacing: 1.6px; }}
    .meta {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
             font-size: 10px; fill: {TEXT}; letter-spacing: 0.8px; }}
    .foot {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
             font-size: 10px; fill: {TEXT_DIM}; letter-spacing: 1.4px; }}
    .sample {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
               font-size: 9px; fill: {ORANGE}; letter-spacing: 1.2px; }}
  </style>

  <rect width="100%" height="100%" fill="url(#bg-grad)"/>
  <!-- faint HUD grid in the margins -->
  <g stroke="#151515" stroke-width="0.6" opacity="0.8">
    <path d="M 28 0 V {height}"/>
    <path d="M {width - 28} 0 V {height}"/>
    <path d="M 0 28 H {width}"/>
    <path d="M 0 {height - 28} H {width}"/>
  </g>

  {hud_frame(width, height)}

  <text x="{FRAME_PAD + 18}" y="{FRAME_PAD + 28}" class="title">// {title_main}</text>
  <text x="{width - FRAME_PAD - 18}" y="{FRAME_PAD + 28}" class="meta" text-anchor="end">{total_lbl}  ·  {year_span}</text>

  {month_labels(first, ox, oy)}
  {day_labels(ox, oy)}

  {''.join(cell_svg)}
  {''.join(proj)}
  {''.join(fx_svg)}
  {player}

  <!-- progress rail -->
  <g id="progress">
    <rect x="{bar_x:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" height="3" fill="#1a1a1a" rx="1"/>
    <rect x="{bar_x:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" height="3" fill="{ORANGE}" rx="1" filter="url(#glow)">
      {bar_anim}
    </rect>
  </g>

  {sample_banner}
  <text x="{FRAME_PAD + 18}" y="{height - FRAME_PAD - 10}" class="foot">BNK // SYSTEM 001 // BZH</text>
  <text x="{width - FRAME_PAD - 18}" y="{height - FRAME_PAD - 10}" class="foot" text-anchor="end">{n_targets} TARGETS</text>
</svg>
'''
    return svg


def rows_to_map(rows: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out[r["date"]] = {"count": int(r.get("count") or 0), "level": int(r.get("level") or 0)}
    return out


def infer_total(rows: list[dict], totals: dict, end: date, first: date | None = None) -> int:
    """Total of contributions inside the visible grid window.

    The header figure must always equal the sum of the cells actually drawn;
    falling back to a year-wide total is what let the published SVG disagree
    with the grid (and with the GitHub profile) in the first place.
    """
    lower = first.isoformat() if first else f"{end.year - 1}-01-01"
    upper = end.isoformat()
    window = sum(
        int(r.get("count") or 0) for r in rows if lower <= r.get("date", "") <= upper
    )
    if window:
        return window
    return sum(int(v) for v in totals.values()) if totals else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate the BUGNAROK contribution-invader SVG.")
    p.add_argument("--user", default="bugnarokdev", help="GitHub username")
    p.add_argument("--from-json", type=Path, help="Load contributions from a local JSON file")
    p.add_argument("--out", type=Path, default=Path("output/contrib-invader.svg"))
    p.add_argument("--end-date", help="Last visible day (YYYY-MM-DD). Default: today UTC")
    p.add_argument("--force-sample", action="store_true", help="Ignore live data and use labeled sample contributions")
    args = p.parse_args(argv)

    if args.end_date:
        end = date.fromisoformat(args.end_date)
    else:
        end = datetime.now(timezone.utc).date()

    is_sample = False
    source = ""
    rows: list[dict] = []
    totals: dict = {}

    if args.force_sample:
        rows, totals, source = sample_contributions(end)
        is_sample = True
    elif args.from_json:
        rows, totals, source = load_json(args.from_json)
    else:
        years = sorted({end.year - 1, end.year})
        try:
            rows, totals, source = fetch_contributions(args.user, years)
        except Exception as exc:
            print(f"warning: live fetch failed ({exc}); using sample data", file=sys.stderr)
            rows, totals, source = sample_contributions(end)
            is_sample = True

    cmap = rows_to_map(rows)
    grid, first, _last = build_grid(cmap, end)
    total = infer_total(rows, totals, end, first)

    svg = build_svg(grid, first, end, total, source, is_sample, args.user)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg, encoding="utf-8")
    filled = sum(
        1
        for row in grid
        for c in row
        if c and c["level"] > 0
    )
    print(f"wrote {args.out}  ({args.out.stat().st_size} bytes)")
    print(f"user={args.user}  end={end.isoformat()}  first={first.isoformat()}  "
          f"filled={filled}  total={total}  source={source}  sample={is_sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

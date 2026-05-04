#!/usr/bin/env python3
"""
scrape_pll.py — Premier Lacrosse League stats backfill
Source: stats.premierlacrosseleague.com (Next.js RSC app, Wayback fallback)

PLL seasons: 2019 – present (single-calendar-year seasons, May–September)

The live stats site renders data client-side via React Server Components;
this scraper tries a Wayback Machine snapshot that captured __NEXT_DATA__.
If Wayback is unavailable the scraper exits cleanly with empty output files
so the pipeline can continue.

Outputs (all in pipeline/raw/):
  pll_players.json   — synthetic player records (IDs starting at 500000)
  pll_stats.json     — synthetic stat rows (season totals)
  pll_raw.json       — cache of parsed raw data

Injection: build_data.py reads these and extends raw_players + raw_stats.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW  = BASE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "pll_players.json"
STATS_FILE   = RAW / "pll_stats.json"
CACHE_FILE   = RAW / "pll_raw.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

SYNTHETIC_ID_START = 500_000

# Known Wayback Machine snapshot URLs to try (ordered by preference).
# These are snapshots from before the PLL stats site moved to pure RSC.
WAYBACK_CANDIDATES = [
    # Try a range of snapshot timestamps; Wayback serves the nearest one.
    "https://web.archive.org/web/20231001000000*/https://stats.premierlacrosseleague.com/players/scoring",
    "https://web.archive.org/web/20220901000000/https://stats.premierlacrosseleague.com/players/scoring",
    "https://web.archive.org/web/20210901000000/https://stats.premierlacrosseleague.com/players/scoring",
    "https://web.archive.org/web/20200901000000/https://stats.premierlacrosseleague.com/players/scoring",
]

# Stat categories on the PLL stats site
STAT_CATEGORIES = ["scoring", "shooting", "faceoff", "groundballs"]

TARGET_YEARS = list(range(2019, 2025))


def safe_float(v) -> float:
    if v is None or str(v).strip() in ("", "-", "—"):
        return 0.0
    try:
        f = float(str(v).replace(",", ""))
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


# ── Wayback CDX discovery ─────────────────────────────────────────────────────

def find_wayback_snapshots(url: str, limit: int = 5) -> list[str]:
    """Use CDX API to find snapshot timestamps for a URL."""
    cdx = (
        f"https://web.archive.org/cdx/search/cdx"
        f"?url={url}&output=json&limit={limit}"
        f"&fl=timestamp,original&filter=statuscode:200"
    )
    try:
        r = requests.get(cdx, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        rows = r.json()
        # rows[0] is the header ["timestamp","original"]
        return [row[0] for row in rows[1:] if row]
    except Exception:
        return []


def fetch_wayback(timestamp: str, target_url: str) -> str | None:
    """Fetch a specific Wayback Machine snapshot; return HTML text or None."""
    wb_url = f"https://web.archive.org/web/{timestamp}/{target_url}"
    try:
        r = requests.get(wb_url, headers=HEADERS, timeout=60)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


# ── Parsing helpers ───────────────────────────────────────────────────────────

def parse_next_data(html: str) -> dict | None:
    """Extract __NEXT_DATA__ JSON from an HTML page."""
    m = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(\{.*?\})\s*</script>',
        html, re.DOTALL
    )
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


def parse_html_table(html: str) -> list[dict]:
    """Parse the first HTML table into a list of row dicts."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
            rows.append(dict(zip(headers, cells)))
    return rows


def extract_year_from_url(url: str) -> int | None:
    """Try to extract a 4-digit season year from a URL."""
    m = re.search(r"[?&]season=(\d{4})", url)
    if m:
        return int(m.group(1))
    return None


# ── Main fetch + parse ────────────────────────────────────────────────────────

def fetch_all_data(cache: dict, reset: bool) -> dict:
    """
    Attempt to collect PLL player stats.
    Returns: {year: {player_slug: {name, team, position, stats_dict}}}
    """
    if "pll_data" in cache and not reset:
        print("Using cached PLL data")
        return cache["pll_data"]

    pll_data: dict = {}

    # Strategy 1: Try Wayback CDX to find snapshots
    print("Searching Wayback Machine for PLL stats snapshots …")
    target = "https://stats.premierlacrosseleague.com/players/scoring"
    timestamps = find_wayback_snapshots(target, limit=8)

    if not timestamps:
        print("  Wayback CDX unavailable — trying hardcoded snapshot timestamps")
        timestamps = [
            "20231005120000",
            "20221005120000",
            "20210905120000",
            "20200905120000",
        ]

    found_data = False
    for ts in timestamps:
        html = fetch_wayback(ts, target)
        if not html:
            continue

        # Try __NEXT_DATA__ (older Next.js Pages Router)
        nd = parse_next_data(html)
        if nd:
            print(f"  Found __NEXT_DATA__ in snapshot {ts}")
            # Attempt to extract player stats from the next data structure
            raw_str = json.dumps(nd)
            if "goals" in raw_str or "assists" in raw_str:
                cache["pll_data_raw_nd"] = nd
                found_data = True
                break

        # Try HTML table (some older versions had server-rendered tables)
        rows = parse_html_table(html)
        if rows and len(rows) > 5:
            print(f"  Found HTML table ({len(rows)} rows) in snapshot {ts}")
            # Guess year from snapshot timestamp
            year = int(ts[:4])
            if year not in pll_data:
                pll_data[year] = {}
            for row in rows:
                name = row.get("Player") or row.get("Name") or row.get("PLAYER") or ""
                if not name:
                    continue
                slug = re.sub(r"[^a-z0-9]", "-", name.lower()).strip("-")
                pll_data[year][slug] = {
                    "name": name,
                    "team": row.get("Team", ""),
                    "position": row.get("Pos") or row.get("Position") or "",
                    "goals":   safe_float(row.get("G") or row.get("Goals")),
                    "assists": safe_float(row.get("A") or row.get("Assists")),
                    "points":  safe_float(row.get("Pts") or row.get("Points")),
                    "shots":   safe_float(row.get("SOG") or row.get("Shots")),
                    "gb":      safe_float(row.get("GB") or row.get("Groundballs")),
                    "to":      safe_float(row.get("TO") or row.get("Turnovers")),
                    "cto":     safe_float(row.get("CTO") or row.get("Caused Turnovers")),
                }
            found_data = bool(pll_data)
            if found_data:
                break
        time.sleep(1.0)

    if not found_data:
        print("  NOTE: PLL stats site uses client-side rendering (RSC).")
        print("  No archived accessible data found. Writing empty output files.")
        print("  Re-run after Wayback Machine is accessible or a static snapshot exists.")

    cache["pll_data"] = pll_data
    return pll_data


# ── Output builders ───────────────────────────────────────────────────────────

def build_outputs(pll_data: dict) -> tuple[list[dict], list[dict]]:
    out_players: list[dict] = []
    out_stats:   list[dict] = []
    synthetic_id = SYNTHETIC_ID_START

    # pll_data: {year: {slug: {name, team, position, goals, assists, ...}}}
    # De-duplicate players across years by slug
    slug_to_id: dict[str, int] = {}

    for year in sorted(pll_data.keys()):
        for slug, pdata in pll_data[year].items():
            name = pdata.get("name", "")
            if not name:
                continue

            if slug not in slug_to_id:
                parts = name.split()
                first = parts[0] if parts else ""
                last  = parts[-1] if len(parts) > 1 else ""

                out_players.append({
                    "id":         synthetic_id,
                    "full_name":  name,
                    "short_name": name,
                    "first_name": first,
                    "last_name":  last,
                    "sport_id":   None,
                    "league":     "PLL",
                    "team":       pdata.get("team", ""),
                    "position":   pdata.get("position", ""),
                    "_pll":       True,
                    "_norm_name": name.lower(),
                    "sportradar_id": None,
                    "college":    None,
                    "jersey":     None,
                    "height":     None,
                    "weight":     None,
                })
                slug_to_id[slug] = synthetic_id
                synthetic_id += 1

            pid     = slug_to_id[slug]
            game_id = f"PLL_{year}_SEASON_TOTAL"

            stat_pairs = [
                ("goals",             pdata.get("goals",   0)),
                ("assists",           pdata.get("assists",  0)),
                ("points",            pdata.get("points",   0)),
                ("shots",             pdata.get("shots",    0)),
                ("groundballs",       pdata.get("gb",       0)),
                ("turnovers",         pdata.get("to",       0)),
                ("caused_turnovers",  pdata.get("cto",      0)),
            ]
            for stat_name, value in stat_pairs:
                v = safe_float(value)
                if v:
                    out_stats.append({
                        "player_id": pid,
                        "week":      1,
                        "stat":      stat_name,
                        "value":     v,
                        "game_id":   game_id,
                        "_year":     year,
                    })

    return out_players, out_stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Scrape PLL player stats")
    ap.add_argument("--reset", action="store_true",
                    help="Ignore cache and re-fetch everything")
    args = ap.parse_args()

    cache: dict = {}
    if CACHE_FILE.exists() and not args.reset:
        cache = json.loads(CACHE_FILE.read_text())

    pll_data = fetch_all_data(cache, reset=args.reset)

    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")

    players, stats = build_outputs(pll_data)
    years = sorted({s["_year"] for s in stats})
    print(f"\nBuilt {len(players)} PLL players,  {len(stats)} stat rows  (years: {years})")

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats,   indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name} and {STATS_FILE.name}")


if __name__ == "__main__":
    main()

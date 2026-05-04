#!/usr/bin/env python3
"""
scrape_pul.py — Premier Ultimate League stats backfill
Source: Wayback Machine archives of premierultimate.org

PUL seasons: 2019 – 2023 (women's professional ultimate frisbee)
The PUL website (premierultimate.org) is currently offline; this scraper
uses Wayback Machine snapshots when available.

Outputs (all in pipeline/raw/):
  pul_players.json   — synthetic player records (IDs starting at 600000)
  pul_stats.json     — synthetic stat rows (season totals)
  pul_raw.json       — cache of parsed data

Injection: build_data.py reads these and extends raw_players + raw_stats.
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW  = BASE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "pul_players.json"
STATS_FILE   = RAW / "pul_stats.json"
CACHE_FILE   = RAW / "pul_raw.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

SYNTHETIC_ID_START = 600_000

# Candidate Wayback snapshot URLs for PUL stats pages
WAYBACK_TARGETS = [
    ("https://premierultimate.org/stats/",          [2019, 2020, 2021, 2022, 2023]),
    ("https://www.premierultimate.org/stats/",      [2019, 2020, 2021, 2022, 2023]),
    ("https://premierultimate.org/statistics/",     [2019, 2020, 2021, 2022, 2023]),
    ("https://www.premierultimate.org/statistics/", [2019, 2020, 2021, 2022, 2023]),
    ("https://premierultimate.org/players/stats/",  [2019, 2020, 2021, 2022, 2023]),
]

# Snapshot timestamps to try per year (end-of-season, ~October)
YEAR_TIMESTAMPS = {
    2023: ["20231015", "20231101", "20231201"],
    2022: ["20221015", "20221101", "20221201"],
    2021: ["20211015", "20211101", "20211201"],
    2020: ["20201015", "20201101"],
    2019: ["20191015", "20191101"],
}


def safe_float(v) -> float:
    if v is None or str(v).strip() in ("", "-", "—"):
        return 0.0
    try:
        f = float(str(v).replace(",", ""))
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def fetch_wayback(timestamp: str, target_url: str) -> str | None:
    wb_url = f"https://web.archive.org/web/{timestamp}000000/{target_url}"
    try:
        r = requests.get(wb_url, headers=HEADERS, timeout=45)
        if r.status_code == 200 and len(r.text) > 2000:
            return r.text
    except Exception:
        pass
    return None


def find_cdx_snapshots(url: str) -> list[str]:
    cdx = (
        f"https://web.archive.org/cdx/search/cdx"
        f"?url={url}&output=json&limit=5&fl=timestamp"
        f"&filter=statuscode:200&from=20190101&to=20231231"
    )
    try:
        r = requests.get(cdx, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            rows = r.json()
            return [row[0] for row in rows[1:] if row]
    except Exception:
        pass
    return []


def parse_stats_table(html: str, year: int) -> list[dict]:
    """Try to parse player stats from an HTML table."""
    soup = BeautifulSoup(html, "html.parser")

    # Check for __NEXT_DATA__
    m = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(\{.*?\})\s*</script>',
        html, re.DOTALL
    )
    if m:
        try:
            nd = json.loads(m.group(1))
            raw = json.dumps(nd)
            # Look for player data in next data
            if any(k in raw for k in ["yardsThrown", "goals", "assists", "blocks"]):
                # Try to extract player rows from props
                props = nd.get("props", {}).get("pageProps", {})
                for key in props:
                    val = props[key]
                    if isinstance(val, list) and val and isinstance(val[0], dict):
                        if any(k in val[0] for k in ["name", "player", "fullName"]):
                            return _parse_nd_player_list(val, year)
        except (json.JSONDecodeError, KeyError):
            pass

    # Fall back to HTML table
    tables = soup.find_all("table")
    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            continue
        # Check if this looks like a stats table
        has_name = any(h.lower() in ("player", "name", "athlete") for h in headers)
        has_stat = any(h.lower() in ("g", "goals", "assists", "a", "blocks", "b") for h in headers)
        if not (has_name and has_stat):
            continue

        rows = []
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if cells:
                rows.append(dict(zip(headers, cells)))
        if rows:
            return _normalize_table_rows(rows, year)

    return []


def _parse_nd_player_list(player_list: list[dict], year: int) -> list[dict]:
    """Normalize player records from __NEXT_DATA__ props."""
    out = []
    for p in player_list:
        name = (
            p.get("fullName") or p.get("name") or
            f"{p.get('firstName','')} {p.get('lastName','')}".strip()
        )
        if not name:
            continue
        out.append({
            "name":    name.strip(),
            "team":    p.get("team") or p.get("teamName") or "",
            "position": p.get("position") or p.get("pos") or "",
            "_year":   year,
            "goals":   safe_float(p.get("goals") or p.get("g")),
            "assists": safe_float(p.get("assists") or p.get("a")),
            "blocks":  safe_float(p.get("blocks") or p.get("b")),
            "turnovers": safe_float(p.get("turnovers") or p.get("to")),
            "yards_thrown":   safe_float(p.get("yardsThrown") or p.get("completionYards")),
            "yards_received": safe_float(p.get("yardsReceived") or p.get("receptionYards")),
        })
    return out


def _normalize_table_rows(rows: list[dict], year: int) -> list[dict]:
    """Normalize HTML table rows into a standard format."""
    out = []
    for row in rows:
        # Find name field
        name = ""
        for k in ("Player", "Name", "Athlete", "PLAYER", "NAME"):
            if k in row:
                name = row[k]
                break
        if not name:
            continue
        out.append({
            "name":    name.strip(),
            "team":    row.get("Team") or row.get("TEAM") or "",
            "position": row.get("Pos") or row.get("Position") or "",
            "_year":   year,
            "goals":   safe_float(row.get("G") or row.get("Goals") or row.get("GOALS")),
            "assists": safe_float(row.get("A") or row.get("Assists") or row.get("ASSISTS")),
            "blocks":  safe_float(row.get("B") or row.get("Blocks") or row.get("BLOCKS")),
            "turnovers": safe_float(row.get("TO") or row.get("Turnovers")),
            "yards_thrown":   safe_float(row.get("YdsThrown") or row.get("Comp Yds")),
            "yards_received": safe_float(row.get("YdsRec") or row.get("Rec Yds")),
        })
    return out


def fetch_all_data(cache: dict, reset: bool) -> list[dict]:
    """Try to fetch PUL stats from Wayback Machine. Returns list of player-season dicts."""
    if "pul_rows" in cache and not reset:
        print(f"Using cached PUL data ({len(cache['pul_rows'])} rows)")
        return cache["pul_rows"]

    all_rows: list[dict] = []
    found_any = False

    for target_url, years in WAYBACK_TARGETS:
        print(f"\nTrying: {target_url}")

        for year in reversed(years):   # most recent first
            timestamps = YEAR_TIMESTAMPS.get(year, [])

            # First try CDX to find actual snapshots
            cdx_ts = find_cdx_snapshots(target_url)
            # Filter CDX timestamps to target year
            year_cdx = [t for t in cdx_ts if t.startswith(str(year))]
            all_timestamps = year_cdx + timestamps

            for ts in all_timestamps[:3]:
                html = fetch_wayback(ts, target_url)
                if not html:
                    continue

                rows = parse_stats_table(html, year)
                if rows:
                    print(f"  ✓ {year}: {len(rows)} players from snapshot {ts}")
                    all_rows.extend(rows)
                    found_any = True
                    break
                time.sleep(0.5)

        if found_any:
            break

    if not found_any:
        print("\nNOTE: PUL website (premierultimate.org) is currently offline.")
        print("No Wayback Machine snapshots with stats tables were found.")
        print("Re-run when the site is accessible or Wayback is less loaded.")

    cache["pul_rows"] = all_rows
    return all_rows


def build_outputs(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    out_players: list[dict] = []
    out_stats:   list[dict] = []
    synthetic_id = SYNTHETIC_ID_START

    # De-duplicate by name
    name_to_id: dict[str, int] = {}

    for row in rows:
        name = row.get("name", "").strip()
        if not name:
            continue

        if name not in name_to_id:
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
                "league":     "PUL",
                "team":       row.get("team", ""),
                "position":   row.get("position", ""),
                "_pul":       True,
                "_norm_name": name.lower(),
                "sportradar_id": None,
                "college":    None,
                "jersey":     None,
                "height":     None,
                "weight":     None,
            })
            name_to_id[name] = synthetic_id
            synthetic_id += 1

        pid     = name_to_id[name]
        year    = row.get("_year", 0)
        game_id = f"PUL_{year}_SEASON_TOTAL"

        stat_pairs = [
            ("goals",          row.get("goals",          0)),
            ("assists",        row.get("assists",         0)),
            ("blocks",         row.get("blocks",          0)),
            ("turnovers",      row.get("turnovers",       0)),
            ("yardsThrown",    row.get("yards_thrown",    0)),
            ("yardsReceived",  row.get("yards_received",  0)),
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


def main():
    ap = argparse.ArgumentParser(description="Scrape PUL player stats")
    ap.add_argument("--reset", action="store_true", help="Ignore cache and re-fetch")
    args = ap.parse_args()

    cache: dict = {}
    if CACHE_FILE.exists() and not args.reset:
        cache = json.loads(CACHE_FILE.read_text())

    rows = fetch_all_data(cache, reset=args.reset)
    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")

    players, stats = build_outputs(rows)
    years = sorted({s["_year"] for s in stats})
    print(f"\nBuilt {len(players)} PUL players,  {len(stats)} stat rows  (years: {years})")

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats,   indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name} and {STATS_FILE.name}")


if __name__ == "__main__":
    main()

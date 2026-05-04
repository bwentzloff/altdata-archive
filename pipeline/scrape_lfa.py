#!/usr/bin/env python3
"""
Scrape LFA (Liga de Fútbol Americano, Mexico) player data from embedded Google Sheets.

Source: lfa.mx/estadisticas-{year}/ pages (2016-2025) with embedded Google Sheets
Extracts: Player stats (passing, rushing, receiving, defense) via Google Sheets GViz CSV export

Stat types by GID: passing (ATT,COMP,YDS,TD,INT), rushing (ATT,YDS,AVG,TD),
receiving (REC,YDS,AVG,TD), defense (tackles, sacks, INT)

Synthetic IDs: 1,300,000+
"""

import argparse
import csv
import json
import re
import time
from io import StringIO
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Paths
REPO_ROOT = Path(__file__).parent.parent
CACHE_FILE = REPO_ROOT / ".cache" / "lfa_wiki.json"
RAW_DIR = REPO_ROOT / "pipeline" / "raw"
PLAYERS_FILE = RAW_DIR / "lfa_players.json"
STATS_FILE = RAW_DIR / "lfa_stats.json"
RAW_FILE = RAW_DIR / "lfa_raw.json"

# Config
LFA_STATS_BASE = "https://lfa.mx/estadisticas-{year}/"
GVIZ_CSV_URL = "https://docs.google.com/spreadsheets/d/{key}/gviz/tq?tqx=out:csv&gid={gid}"
SYNTHETIC_ID_START = 1_300_000
HEADERS = {
    "User-Agent": "AltSportsArchive/1.0 (https://archive.altfantasysports.com; altfantasysports@gmail.com) Python/requests"
}

# Years: 2016-2025
YEARS = list(range(2016, 2026))


def fetch_stats_page(year: int, cache: dict, reset: bool = False) -> Optional[str]:
    """Fetch and cache LFA stats page HTML."""
    cache_key = f"lfa:{year}"

    if cache_key in cache and not reset:
        return cache[cache_key]

    url = LFA_STATS_BASE.format(year=year)
    try:
        print(f"    Fetching {url} …")
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"    ERROR: {e}")
        return None

    html = r.text
    cache[cache_key] = html
    return html


def extract_google_sheet_ids(html: str) -> list[tuple[str, str]]:
    """
    Extract Google Sheets key and gid from [gdoc] shortcodes.
    Returns list of (sheet_key, gid) tuples.
    """
    results = []

    # Look for [gdoc key="..." data="{...}"] shortcodes
    # Pattern: [gdoc key="SHEET_ID" data="{...gid...:...}"]
    pattern = r'\[gdoc\s+key="([a-zA-Z0-9_-]+)"[^\]]*\]'
    for match in re.finditer(pattern, html):
        sheet_key = match.group(1)

        # Try to find data= attribute with gid
        data_pattern = rf'\[gdoc\s+key="{re.escape(sheet_key)}"[^>]*data="([^"]*)"'
        data_match = re.search(data_pattern, html)

        if data_match:
            data_str = data_match.group(1)
            # Parse gid from data string (usually JSON-like)
            gid_match = re.search(r'["\']?gid["\']?\s*[:=]\s*["\']?(\d+)', data_str)
            if gid_match:
                gid = gid_match.group(1)
                results.append((sheet_key, gid))

    # If we didn't find gid in data, try extracting all gids from the entire page
    if not results:
        # Look for patterns like gid=NNNNNNNN
        gid_pattern = r'gid["\']?\s*[:=]\s*["\']?(\d+)'
        gids = set(re.findall(gid_pattern, html))
        # Also look for sheet keys
        sheet_keys = set(re.findall(r'key="([a-zA-Z0-9_-]+)"', html))
        # If we found both, pair them
        if sheet_keys and gids:
            for sheet_key in sheet_keys:
                for gid in gids:
                    results.append((sheet_key, gid))

    return results


def fetch_sheet_csv(sheet_key: str, gid: str, cache: dict, reset: bool = False) -> Optional[str]:
    """Fetch Google Sheet as CSV via GViz endpoint."""
    cache_key = f"gsheet:{sheet_key}:{gid}"

    if cache_key in cache and not reset:
        return cache[cache_key]

    url = GVIZ_CSV_URL.format(key=sheet_key, gid=gid)
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        csv_data = r.text
    except requests.RequestException as e:
        print(f"    WARNING: Could not fetch {sheet_key}:{gid} - {e}")
        return None

    cache[cache_key] = csv_data
    return csv_data


def parse_stat_row(csv_row: dict, sheet_key: str, gid: str, year: int) -> Optional[dict]:
    """
    Parse a CSV row and extract player name, team, and stats.
    Detects stat type from column headers.
    """
    # Try to find player name column (JUGADOR, Player, Nombre, etc.)
    name = None
    team = None
    for key_var in ["JUGADOR", "Player", "Nombre", "NAME", "jugador", "player"]:
        if key_var in csv_row:
            name = csv_row[key_var].strip()
            break

    # Try to find team column (EQUIPO, Team, Equipo, etc.)
    for key_var in ["EQUIPO", "Team", "Equipo", "TEAM", "equipo"]:
        if key_var in csv_row:
            team = csv_row[key_var].strip()
            break

    if not name or len(name) < 3:
        return None

    # Detect stat type from column headers
    headers = set(csv_row.keys())
    stat_type = "other"

    if any(h in headers for h in ["ATT", "COMP", "YDS", "TD", "INT"]):
        if "COMP" in headers:  # Passing has COMP (completions)
            stat_type = "passing"
        elif "REC" in headers:  # Receiving has REC (receptions)
            stat_type = "receiving"
        else:
            stat_type = "passing"
    elif any(h in headers for h in ["REC", "REC.", "Receptions"]):
        stat_type = "receiving"
    elif any(h in headers for h in ["Tackles", "TACKLES", "TKL"]):
        stat_type = "defense"

    stats_dict = {"name": name, "team": team, "_year": year, "stat_type": stat_type}

    # Extract numeric stats (try common column names)
    stat_cols = {
        "passing": ["ATT", "COMP", "YDS", "TD", "INT"],
        "rushing": ["ATT", "YDS", "AVG", "TD"],
        "receiving": ["REC", "YDS", "AVG", "TD"],
        "defense": ["Tackles", "Sacks", "INT"],
    }

    for col in stat_cols.get(stat_type, []):
        if col in csv_row:
            try:
                val = float(csv_row[col])
                stats_dict[col] = val
            except (ValueError, TypeError):
                pass

    return stats_dict if len(stats_dict) > 3 else None  # Need actual stats


def fetch_all_data(cache: dict, reset: bool, batch: Optional[int] = None) -> dict:
    """
    Fetch LFA data from lfa.mx stats pages and embedded Google Sheets.
    If batch is set, fetch only that many seasons (for gradual backfill).
    """
    cache_key = "lfa_rows"

    if cache_key in cache and not reset:
        rows = cache[cache_key]
        if isinstance(rows, dict):
            total = len(rows.get("stats", []))
        else:
            total = len(rows)
        print(f"Using cached LFA data ({total} records)")
        return rows if isinstance(rows, dict) else {"stats": []}

    # If batch is set, only fetch that many years (from the end, most recent first)
    years_to_fetch = YEARS if not batch else YEARS[-batch:]

    all_stats = []

    for year in years_to_fetch:
        print(f"  Fetching LFA {year} stats page …")
        html = fetch_stats_page(year, cache, reset)
        if not html:
            print(f"  LFA {year}: no stats page")
            continue

        # Extract Google Sheet IDs from page
        sheet_ids = extract_google_sheet_ids(html)
        if not sheet_ids:
            print(f"  LFA {year}: no embedded Google Sheets found")
            continue

        print(f"  LFA {year}: found {len(sheet_ids)} sheet(s)")
        time.sleep(1.0)

        # Fetch each sheet's CSV data
        for sheet_key, gid in sheet_ids:
            csv_data = fetch_sheet_csv(sheet_key, gid, cache, reset)
            if not csv_data:
                continue

            # Parse CSV
            try:
                reader = csv.DictReader(StringIO(csv_data))
                row_count = 0
                for row in reader:
                    stat_row = parse_stat_row(row, sheet_key, gid, year)
                    if stat_row:
                        all_stats.append(stat_row)
                        row_count += 1
                if row_count:
                    print(f"    Sheet {gid}: {row_count} player stat rows")
            except Exception as e:
                print(f"    ERROR parsing CSV: {e}")

    result = {"stats": all_stats}
    cache[cache_key] = result
    return result


def build_outputs(data: dict) -> tuple[list[dict], list[dict], dict]:
    """
    Build player and stat records from scraped data.
    Returns (players, stats, raw_data)
    """
    out_players: list[dict] = []
    out_stats: list[dict] = []
    synthetic_id = SYNTHETIC_ID_START

    name_to_id: dict[str, int] = {}  # lower-case (name, team) → synthetic ID
    id_to_idx: dict[int, int] = {}   # synthetic ID → index in out_players

    # Collect all unique (name, team) pairs
    all_entries = set()
    for record in data.get("stats", []):
        name = record.get("name", "").strip()
        team = record.get("team", "").strip()
        if name and len(name) > 2:
            all_entries.add((name, team))

    # Build player records for all unique entries
    for name, team in sorted(all_entries):
        key = (name.lower(), team.lower() if team else "")
        if key not in name_to_id:
            parts = name.split()
            out_players.append(
                {
                    "id": synthetic_id,
                    "full_name": name,
                    "short_name": name,
                    "first_name": parts[0] if parts else "",
                    "last_name": parts[-1] if len(parts) > 1 else "",
                    "sport_id": None,
                    "league": "LFA",
                    "team": team,
                    "position": "",
                    "_lfa": True,
                    "_norm_name": name.lower(),
                    "sportradar_id": None,
                    "college": None,
                    "jersey": None,
                    "height": None,
                    "weight": None,
                }
            )
            name_to_id[key] = synthetic_id
            id_to_idx[synthetic_id] = len(out_players) - 1
            synthetic_id += 1

    # Build stat rows
    for record in data.get("stats", []):
        name = record.get("name", "").strip()
        team = record.get("team", "").strip()
        year = record.get("_year")
        stat_type = record.get("stat_type", "")

        if not name or not year:
            continue

        key = (name.lower(), team.lower() if team else "")
        pid = name_to_id.get(key)
        if pid is None:
            continue

        game_id = f"FOOTBALL_LFA_{year}_SEASON_TOTAL"

        # Extract stat values from record
        for stat_col in ["ATT", "COMP", "YDS", "TD", "INT", "AVG", "REC", "Tackles", "Sacks"]:
            if stat_col in record:
                try:
                    val = float(record[stat_col])
                    out_stats.append(
                        {
                            "player_id": pid,
                            "week": 1,
                            "stat": f"{stat_type}_{stat_col.lower()}",
                            "value": val,
                            "game_id": game_id,
                            "league": "LFA",
                            "_year": year,
                        }
                    )
                except (ValueError, TypeError):
                    pass

    return out_players, out_stats, {"stats": data.get("stats", [])}


def main():
    ap = argparse.ArgumentParser(description="Scrape LFA (Mexico) player data from Google Sheets")
    ap.add_argument("--reset", action="store_true", help="Ignore cache and re-fetch")
    ap.add_argument("--batch", type=int, help="Fetch only N most recent seasons (for gradual backfill)")
    args = ap.parse_args()

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    cache: dict = {}
    if CACHE_FILE.exists() and not args.reset:
        cache = json.loads(CACHE_FILE.read_text())

    data = fetch_all_data(cache, reset=args.reset, batch=args.batch)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    players, stats, raw_data = build_outputs(data)
    years = sorted({s["_year"] for s in stats})
    print(
        f"\nBuilt {len(players)} LFA players, {len(stats)} stat rows (years: {years})"
    )

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    RAW_FILE.write_text(json.dumps(raw_data, indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name}, {STATS_FILE.name}, {RAW_FILE.name}")


if __name__ == "__main__":
    main()

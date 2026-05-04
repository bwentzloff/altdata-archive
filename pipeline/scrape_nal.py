#!/usr/bin/env python3
"""
Scrape NAL (National Arena League) player data from Wikipedia season articles.

Source: Wikipedia pages for each NAL season (2017-2025, skip 2020 COVID)
Extracts: Players of the Week (Offensive/Defensive/Special Teams/Ironman)

Recognition stats (boolean 1.0 for weekly honors).
Synthetic IDs: 1,200,000+
"""

import argparse
import json
import time
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Paths
REPO_ROOT = Path(__file__).parent.parent
CACHE_FILE = REPO_ROOT / ".cache" / "nal_wiki.json"
RAW_DIR = REPO_ROOT / "pipeline" / "raw"
PLAYERS_FILE = RAW_DIR / "nal_players.json"
STATS_FILE = RAW_DIR / "nal_stats.json"
RAW_FILE = RAW_DIR / "nal_raw.json"

# Config
WIKI_API = "https://en.wikipedia.org/w/api.php"
SYNTHETIC_ID_START = 1_200_000
HEADERS = {
    "User-Agent": "AltSportsArchive/1.0 (https://archive.altfantasysports.com; altfantasysports@gmail.com) Python/requests"
}

# Years: 2017-2025, skip 2020 (COVID-cancelled)
YEARS = [2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025]

WIKI_PAGES = {year: f"{year}_National_Arena_League_season" for year in YEARS}


def fetch_wiki_html(page_title: str, cache: dict, reset: bool = False) -> Optional[str]:
    """Fetch and cache Wikipedia page HTML via API."""
    cache_key = f"wiki:{page_title}"

    if cache_key in cache and not reset:
        return cache[cache_key]

    params = {
        "action": "parse",
        "page": page_title,
        "format": "json",
        "prop": "text",
    }

    try:
        print(f"    Fetching {page_title} from Wikipedia API …")
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"    ERROR: {e}")
        return None

    data = r.json()
    if "error" in data:
        print(f"    API error: {data['error'].get('info', 'unknown')}")
        return None

    html = data.get("parse", {}).get("text", {}).get("*", "")
    cache[cache_key] = html
    return html


def parse_potw(soup: BeautifulSoup, year: int) -> list[dict]:
    """
    Extract Players of the Week (Offensive, Defensive, Special Teams, Ironman).
    Returns list of {"name": str, "potw_type": str, "team": str, "_year": int, "week": int}
    """
    results = []

    tables = soup.find_all("table", {"class": "wikitable"})

    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        table_text = table.get_text().lower()

        # Look for "player(s) of the week" tables
        if "player" not in table_text or "week" not in table_text:
            continue
        if "of the week" not in table_text:
            continue

        # Parse rows
        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue

            row_text = [td.get_text(strip=True) for td in tds]

            # First column is usually week number
            week_raw = row_text[0]
            try:
                week = int(week_raw.replace("Week", "").replace("Wk", "").strip())
            except (ValueError, IndexError):
                week = 1

            # Rest of row contains player names and types
            for i in range(1, min(len(row_text), len(headers) + 1)):
                cell = row_text[i]
                if not cell or len(cell) < 3:
                    continue

                # Infer type from header or position in row
                potw_type = "potw_offense"
                if i < len(headers):
                    header = headers[i].lower()
                    if "defensive" in header or "defense" in header:
                        potw_type = "potw_defense"
                    elif "special" in header or "st" in header:
                        potw_type = "potw_special_teams"
                    elif "ironman" in header:
                        potw_type = "potw_ironman"

                # Parse player name (possibly with team in parens)
                name = cell.split("(")[0].strip()
                team = ""
                if "(" in cell:
                    team = cell.split("(")[1].split(")")[0].strip()

                if name and len(name) > 2:
                    results.append(
                        {"name": name, "potw_type": potw_type, "team": team, "_year": year, "week": week}
                    )

    return results


def fetch_all_data(cache: dict, reset: bool, batch: Optional[int] = None) -> dict:
    """
    Fetch NAL data from Wikipedia season pages.
    If batch is set, fetch only that many seasons (for gradual backfill).
    """
    cache_key = "nal_rows"

    if cache_key in cache and not reset:
        rows = cache[cache_key]
        if isinstance(rows, dict):
            total = len(rows.get("potw", []))
        else:
            total = len(rows)
        print(f"Using cached NAL data ({total} records)")
        return rows if isinstance(rows, dict) else {"potw": []}

    # If batch is set, only fetch that many years (from the end, most recent first)
    years_to_fetch = YEARS if not batch else YEARS[-batch:]

    all_potw = []

    for year in years_to_fetch:
        page_title = WIKI_PAGES[year]
        print(f"  Fetching Wikipedia: {page_title} …")
        html = fetch_wiki_html(page_title, cache, reset)
        if not html:
            print(f"  NAL {year}: no Wikipedia data")
            continue
        time.sleep(1.0)

        soup = BeautifulSoup(html, "html.parser")

        potw = parse_potw(soup, year)
        if potw:
            print(f"  NAL {year}: {len(potw)} players of week")
            all_potw.extend(potw)

    result = {"potw": all_potw}
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

    name_to_id: dict[str, int] = {}  # lower-case name → synthetic ID
    id_to_idx: dict[int, int] = {}   # synthetic ID → index in out_players

    # Collect all unique names first
    all_names = set()
    for record in data.get("potw", []):
        name = record.get("name", "").strip()
        if name:
            all_names.add(name)

    # Build player records for all unique names
    for name in sorted(all_names):
        if not name or len(name) < 3:
            continue
        key = name.lower()
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
                    "league": "NAL",
                    "team": "",
                    "position": "",
                    "_nal": True,
                    "_norm_name": key,
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
    player_year_stats: dict[tuple, dict] = {}

    for record in data.get("potw", []):
        name = record.get("name", "").strip()
        year = record.get("_year")
        team = record.get("team", "")
        potw_type = record.get("potw_type", "")
        week = record.get("week", 1)

        if not name or not potw_type:
            continue

        key = name.lower()
        pid = name_to_id.get(key)
        if pid is None:
            continue

        # Update player team if available
        idx = id_to_idx[pid]
        if team and not out_players[idx]["team"]:
            out_players[idx]["team"] = team

        stat_key = (pid, year)
        if stat_key not in player_year_stats:
            player_year_stats[stat_key] = {}

        # POTW can occur multiple times per season (different weeks)
        if potw_type not in player_year_stats[stat_key]:
            player_year_stats[stat_key][potw_type] = 0
        player_year_stats[stat_key][potw_type] += 1.0

    # Convert stat_key dict to stat rows
    for (pid, year), stats in player_year_stats.items():
        game_id = f"FOOTBALL_NAL_{year}_SEASON_TOTAL"
        for stat_name, value in stats.items():
            if value:
                out_stats.append(
                    {
                        "player_id": pid,
                        "week": 1,
                        "stat": stat_name,
                        "value": float(value),
                        "game_id": game_id,
                        "league": "NAL",
                        "_year": year,
                    }
                )

    return out_players, out_stats, {"potw": data.get("potw", [])}


def main():
    ap = argparse.ArgumentParser(description="Scrape NAL player data from Wikipedia")
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
        f"\nBuilt {len(players)} NAL players, {len(stats)} stat rows (years: {years})"
    )

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    RAW_FILE.write_text(json.dumps(raw_data, indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name}, {STATS_FILE.name}, {RAW_FILE.name}")


if __name__ == "__main__":
    main()

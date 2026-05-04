#!/usr/bin/env python3
"""
Scrape X-League (Japan) player data from Wikipedia.

Source: Single Wikipedia page X-League_(Japan) with comprehensive historical data
Extracts: MVP awards (2012-2025), ROY awards (2012-2025), divisional standings (1997-2025)

Awards include player names, positions, teams, years.
Synthetic IDs: 1,500,000+
"""

import argparse
import json
import re
import time
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Paths
REPO_ROOT = Path(__file__).parent.parent
CACHE_FILE = REPO_ROOT / ".cache" / "xleague_wiki.json"
RAW_DIR = REPO_ROOT / "pipeline" / "raw"
PLAYERS_FILE = RAW_DIR / "xleague_players.json"
STATS_FILE = RAW_DIR / "xleague_stats.json"
RAW_FILE = RAW_DIR / "xleague_raw.json"

# Config
WIKI_API = "https://en.wikipedia.org/w/api.php"
SYNTHETIC_ID_START = 1_500_000
HEADERS = {
    "User-Agent": "AltSportsArchive/1.0 (https://archive.altfantasysports.com; altfantasysports@gmail.com) Python/requests"
}

PAGE_TITLE = "X-League_(Japan)"


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


def parse_mvp_table(soup: BeautifulSoup) -> list[dict]:
    """
    Extract MVP awards from X-League MVP table.
    Returns list of {"name": str, "position": str, "team": str, "_year": int, "award": str}
    """
    results = []

    tables = soup.find_all("table", {"class": "wikitable"})

    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        table_text = table.get_text().lower()

        # Look for MVP table (has "year", "division" or "winner", "position")
        if "mvp" not in table_text:
            continue

        # Parse rows
        for tr in table.find_all("tr")[1:]:  # skip header row
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue

            row_text = [td.get_text(strip=True) for td in tds]

            # Try to extract year from first column
            year = None
            name = ""
            position = ""
            team = ""

            # Expected column order: Year | [Division] | Winner | Position | Team
            col_idx = 0

            # First try: column 0 is year
            if col_idx < len(row_text):
                try:
                    year = int(row_text[col_idx])
                    col_idx += 1
                except (ValueError, IndexError):
                    pass

            # Skip division if present (look for x1, east, central, west)
            if col_idx < len(row_text):
                test_val = row_text[col_idx].lower()
                if "x1" in test_val or test_val in ["east", "central", "west"]:
                    col_idx += 1

            # Next should be winner name
            if col_idx < len(row_text):
                name = row_text[col_idx].strip()
                col_idx += 1

            # Next is position
            if col_idx < len(row_text):
                position = row_text[col_idx].strip()
                col_idx += 1

            # Next is team
            if col_idx < len(row_text):
                team = row_text[col_idx].strip()

            if year and name and len(name) > 2:
                results.append(
                    {
                        "name": name,
                        "position": position,
                        "team": team,
                        "_year": year,
                        "award": "award_mvp",
                    }
                )

    return results


def parse_roy_table(soup: BeautifulSoup) -> list[dict]:
    """
    Extract ROY (Rookie of the Year) awards from X-League ROY table.
    Returns list of {"name": str, "position": str, "team": str, "_year": int, "award": str}
    """
    results = []

    tables = soup.find_all("table", {"class": "wikitable"})

    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        table_text = table.get_text().lower()

        # Look for ROY table
        if "rookie" not in table_text and "roy" not in table_text:
            continue

        # Parse rows
        for tr in table.find_all("tr")[1:]:  # skip header row
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue

            row_text = [td.get_text(strip=True) for td in tds]

            # Extract fields from columns (similar to MVP table)
            year = None
            name = ""
            position = ""
            team = ""

            col_idx = 0

            # Try to extract year from first column
            if col_idx < len(row_text):
                try:
                    year = int(row_text[col_idx])
                    col_idx += 1
                except (ValueError, IndexError):
                    pass

            # Skip division if present
            if col_idx < len(row_text):
                test_val = row_text[col_idx].lower()
                if "x1" in test_val or test_val in ["east", "central", "west"]:
                    col_idx += 1

            # Winner (player name)
            if col_idx < len(row_text):
                name = row_text[col_idx].strip()
                col_idx += 1

            # Position
            if col_idx < len(row_text):
                position = row_text[col_idx].strip()
                col_idx += 1

            # Team
            if col_idx < len(row_text):
                team = row_text[col_idx].strip()

            if year and name and len(name) > 2:
                results.append(
                    {
                        "name": name,
                        "position": position,
                        "team": team,
                        "_year": year,
                        "award": "award_roty",
                    }
                )

    return results


def fetch_all_data(cache: dict, reset: bool) -> dict:
    """
    Fetch X-League data from Wikipedia (single page, fast scraper).
    """
    cache_key = "xleague_rows"

    if cache_key in cache and not reset:
        rows = cache[cache_key]
        if isinstance(rows, dict):
            total = len(rows.get("awards", []))
        else:
            total = len(rows)
        print(f"Using cached X-League data ({total} records)")
        return rows if isinstance(rows, dict) else {"awards": []}

    print(f"  Fetching Wikipedia: {PAGE_TITLE} …")
    html = fetch_wiki_html(PAGE_TITLE, cache, reset)
    if not html:
        print(f"  X-League: no Wikipedia data")
        return {"awards": []}

    time.sleep(1.0)

    soup = BeautifulSoup(html, "html.parser")

    all_awards = []

    # Parse MVP table
    mvp_awards = parse_mvp_table(soup)
    if mvp_awards:
        print(f"  X-League: {len(mvp_awards)} MVP award rows")
        all_awards.extend(mvp_awards)

    # Parse ROY table
    roy_awards = parse_roy_table(soup)
    if roy_awards:
        print(f"  X-League: {len(roy_awards)} ROY award rows")
        all_awards.extend(roy_awards)

    result = {"awards": all_awards}
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
    for record in data.get("awards", []):
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
                    "league": "X-League",
                    "team": "",
                    "position": "",
                    "_xleague": True,
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

    for record in data.get("awards", []):
        name = record.get("name", "").strip()
        year = record.get("_year")
        team = record.get("team", "")
        position = record.get("position", "")
        award = record.get("award", "")

        if not name or not award:
            continue

        key = name.lower()
        pid = name_to_id.get(key)
        if pid is None:
            continue

        # Update player team and position if available
        idx = id_to_idx[pid]
        if team and not out_players[idx]["team"]:
            out_players[idx]["team"] = team
        if position and not out_players[idx]["position"]:
            out_players[idx]["position"] = position

        stat_key = (pid, year)
        if stat_key not in player_year_stats:
            player_year_stats[stat_key] = {}
        player_year_stats[stat_key][award] = 1.0

    # Convert stat_key dict to stat rows
    for (pid, year), stats in player_year_stats.items():
        game_id = f"FOOTBALL_XLEAGUE_{year}_SEASON_TOTAL"
        for stat_name, value in stats.items():
            if value:
                out_stats.append(
                    {
                        "player_id": pid,
                        "week": 1,
                        "stat": stat_name,
                        "value": float(value),
                        "game_id": game_id,
                        "_year": year,
                    }
                )

    return out_players, out_stats, {"awards": data.get("awards", [])}


def main():
    ap = argparse.ArgumentParser(description="Scrape X-League (Japan) player data from Wikipedia")
    ap.add_argument("--reset", action="store_true", help="Ignore cache and re-fetch")
    args = ap.parse_args()

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    cache: dict = {}
    if CACHE_FILE.exists() and not args.reset:
        cache = json.loads(CACHE_FILE.read_text())

    data = fetch_all_data(cache, reset=args.reset)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    players, stats, raw_data = build_outputs(data)
    years = sorted({s["_year"] for s in stats})
    print(
        f"\nBuilt {len(players)} X-League players, {len(stats)} stat rows (years: {years})"
    )

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    RAW_FILE.write_text(json.dumps(raw_data, indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name}, {STATS_FILE.name}, {RAW_FILE.name}")


if __name__ == "__main__":
    main()

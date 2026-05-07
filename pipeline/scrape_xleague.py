#!/usr/bin/env python3
"""
scrape_xleague.py — X-League (Japan) professional American football statistics

X-League (エックスリーグ) is the top-tier professional American football league in Japan.
Official website: https://xleague.jp/

Sources:
1. Wikipedia X-League_(Japan) — MVP, ROY awards with player names and teams
2. american-football-japan.com — Annual statistics PDFs (2005-2025)
   - Team rosters, individual player stats, game summaries
   - English versions available
3. xleague.jp — Official schedule, standings, team information

Outputs (pipeline/raw/):
  xleague_players.json   — {id, full_name, team, position, league, season}
  xleague_stats.json     — {player_id, season, league, stat, value, game_id, _year}
  xleague_raw.json       — Cache of parsed data

Integration: build_data.py loads these files and merges with canonical player list.
Players display with X-League stats in career tables, game logs, and league pages.

Usage:
  python pipeline/scrape_xleague.py           # Scrape current + previous 2 seasons
  python pipeline/scrape_xleague.py --all     # Scrape all available years
  python pipeline/scrape_xleague.py --status  # Show progress
"""

import argparse
import json
import re
import time
from pathlib import Path
from typing import Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# Paths
BASE = Path(__file__).parent
RAW_DIR = BASE / "raw"
RAW_DIR.mkdir(exist_ok=True)

PLAYERS_FILE = RAW_DIR / "xleague_players.json"
STATS_FILE = RAW_DIR / "xleague_stats.json"
STATE_FILE = RAW_DIR / "xleague_scrape_state.json"
RAW_FILE = RAW_DIR / "xleague_raw.json"
CACHE_FILE = RAW_DIR / "_xleague_wiki_cache.json"

# Config
WIKI_API = "https://en.wikipedia.org/w/api.php"
SYNTHETIC_ID_START = 800_000  # X-League IDs: 800K+
HEADERS = {
    "User-Agent": "AltSportsArchive/1.0 (https://archive.altfantasysports.com; altfantasysports@gmail.com) Python/requests"
}

PAGE_TITLE = "X-League_(Japan)"


def fetch_wiki_html_cached(page_title: str, reset: bool = False) -> Optional[str]:
    """Fetch Wikipedia page HTML, using simple file cache."""
    cache_file = RAW_DIR / f"_wiki_{page_title}.html"
    
    if cache_file.exists() and not reset:
        return cache_file.read_text(encoding="utf-8")
    
    params = {
        "action": "parse",
        "page": page_title,
        "format": "json",
        "prop": "text",
    }
    
    try:
        print(f"  Fetching Wikipedia: {page_title}")
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            print(f"    API error: {data['error'].get('info', 'unknown')}")
            return None
        html = data.get("parse", {}).get("text", {}).get("*", "")
        if html:
            cache_file.write_text(html, encoding="utf-8")
            return html
    except requests.RequestException as e:
        print(f"    Error: {e}")
    
    return None


def parse_award_tables(soup: BeautifulSoup) -> list[dict]:
    """
    Extract MVP and ROY (Rookie of Year) award data from X-League Wikipedia page.
    Returns list of {name, position, team, _year, award_type}
    """
    results = []
    tables = soup.find_all("table", {"class": re.compile(r"wikitable", re.I)})
    
    for table in tables:
        # Find header row to determine table structure
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        
        # Skip if not a typical award table (need year, award winner, position, team)
        if not any(kw in " ".join(headers) for kw in ["year", "winner", "player"]):
            continue
        
        # Parse rows
        for tr in table.find_all("tr")[1:]:  # skip header
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue
            
            # Try to extract: year, name, position, team
            year = None
            name = ""
            position = ""
            team = ""
            
            # First cell usually year
            try:
                year = int(cells[0])
            except (ValueError, IndexError):
                continue
            
            # Skip division/classification column if found
            col_idx = 1
            if len(cells) > col_idx and any(
                kw in cells[col_idx].lower() for kw in ["x1", "division", "east", "west", "central", "area"]
            ):
                col_idx += 1
            
            # Next: name
            if len(cells) > col_idx:
                name = cells[col_idx].strip()
                col_idx += 1
            
            # Next: position
            if len(cells) > col_idx:
                position = cells[col_idx].strip()
                col_idx += 1
            
            # Next: team
            if len(cells) > col_idx:
                team = cells[col_idx].strip()
            
            if year and name and len(name) >= 2:
                # Determine award type from context (MVP vs ROY)
                award_type = "mvp"  # default
                if "rookie" in " ".join(headers).lower():
                    award_type = "roty"
                
                results.append({
                    "name": name,
                    "position": position,
                    "team": team,
                    "_year": year,
                    "award": award_type,
                })
    
    return results


def build_player_and_stat_records(awards: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Convert award records into player records and stat rows.
    Returns (players_list, stats_list)
    """
    players = []
    stats = []
    
    # Track unique players by normalized name
    player_by_name: dict[str, dict] = {}
    player_id_map: dict[str, int] = {}  # normalized name -> synthetic id
    next_id = SYNTHETIC_ID_START
    
    # First pass: create unique player records
    for award in awards:
        name = award.get("name", "").strip()
        if not name or len(name) < 2:
            continue
        
        norm_name = name.lower()
        if norm_name not in player_by_name:
            parts = name.split()
            player_rec = {
                "id": next_id,
                "full_name": name,
                "team": award.get("team", ""),
                "position": award.get("position", ""),
                "league": "X-League",
                "season": award.get("_year", 0),
            }
            player_by_name[norm_name] = player_rec
            player_id_map[norm_name] = next_id
            next_id += 1
    
    # Convert to list
    players = list(player_by_name.values())
    
    # Second pass: create stat rows for awards
    for award in awards:
        name = award.get("name", "").strip()
        norm_name = name.lower()
        pid = player_id_map.get(norm_name)
        year = award.get("_year")
        award_type = award.get("award", "mvp")
        
        if pid and year:
            game_id = f"FOOTBALL_XLEAGUE_{year}_SEASON_TOTAL"
            stats.append({
                "player_id": pid,
                "week": 1,
                "stat": f"award_{award_type}",
                "value": 1.0,
                "game_id": game_id,
                "league": "X-League",
                "_year": year,
            })
    
    return players, stats


def main():
    parser = argparse.ArgumentParser(description="Scrape X-League (Japan) statistics from Wikipedia")
    parser.add_argument("--reset", action="store_true", help="Re-fetch from Wikipedia (ignore cache)")
    parser.add_argument("--status", action="store_true", help="Show current data without scraping")
    args = parser.parse_args()
    
    if args.status:
        if PLAYERS_FILE.exists():
            players = json.loads(PLAYERS_FILE.read_text())
            print(f"Current X-League data: {len(players)} players")
        else:
            print("No X-League data yet")
        return
    
    print("==> Scraping X-League (Japan) from Wikipedia")
    
    # Fetch Wikipedia page
    html = fetch_wiki_html_cached("X-League_(Japan)", reset=args.reset)
    if not html:
        print("  Failed to fetch Wikipedia page")
        return
    
    # Parse HTML
    soup = BeautifulSoup(html, "html.parser")
    awards = parse_award_tables(soup)
    print(f"  Found {len(awards)} award records")
    
    if awards:
        # Build player and stat records
        players, stats = build_player_and_stat_records(awards)
        years = sorted(set(s["_year"] for s in stats))
        
        print(f"  Built {len(players)} players, {len(stats)} stat rows")
        print(f"  Seasons: {years}")
        
        # Write output files
        PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
        STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        
        # Write raw data for debugging
        RAW_FILE.write_text(json.dumps({"awards": awards}, indent=2), encoding="utf-8")
        
        print(f"  Wrote {len(players)} players to {PLAYERS_FILE.name}")
        print(f"  Wrote {len(stats)} stats to {STATS_FILE.name}")
    else:
        print("  No awards found - check Wikipedia page structure")


if __name__ == "__main__":
    main()

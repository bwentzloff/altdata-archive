#!/usr/bin/env python3
"""
scrape_aaf_gamelogs.py
Scrapes per-game player stats from footballdb.com AAF player gamelogs.

Flow:
  1. Load AAF player list (from aaf_players.json)
  2. For each player, fetch their /gamelogs page
  3. Extract game-by-game stats
  4. Aggregate into stat rows with game_date, league, season

Usage:
  python pipeline/scrape_aaf_gamelogs.py          # all players
  python pipeline/scrape_aaf_gamelogs.py --batch 5  # 5 per run

Rate limit: 1.5 sec between requests
"""

import argparse
import json
import re
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.footballdb.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}
DELAY = 1.5

RAW_DIR = Path(__file__).parent / "raw"
RAW_DIR.mkdir(exist_ok=True)

STATE_FILE = RAW_DIR / "aaf_gamelogs_state.json"
OUTPUT_FILE = RAW_DIR / "aaf_2019_gamelogs.json"

SPORT_ID = 8
LEAGUE = "AAF"
SEASON = "2019"


def load_state() -> dict:
    """Load progress state."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"processed": {}, "total_rows": 0}


def save_state(state: dict):
    """Save progress state."""
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_gamelog(player_url: str, player_name: str) -> Optional[list[dict]]:
    """Fetch per-game stats from a player's 2019 gamelog page."""
    # Specifically fetch 2019 gamelogs, not the default (which is current year)
    gamelog_url = f"{BASE_URL}{player_url}/gamelogs/2019"
    
    try:
        r = requests.get(gamelog_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        time.sleep(DELAY)
    except Exception as e:
        print(f"    ✗ Failed to fetch: {e}")
        return None
    
    try:
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Look for the game logs table - try multiple selectors
        table = soup.find("table", {"class": "tr-table"})
        if not table:
            table = soup.find("table", {"id": re.compile(r".*gamelogs.*", re.I)})
        if not table:
            table = soup.find("table")  # Fallback to first table
        
        if not table:
            return []
        
        rows = []
        tbody = table.find("tbody")
        if not tbody:
            tbody = table
        
        for tr in tbody.find_all("tr"):
            # Skip header rows (they have "header" class)
            if "header" in tr.get("class", []):
                continue
            
            cells = tr.find_all(["th", "td"])
            if len(cells) < 3:
                continue
            
            # Parse: Date, Team, Opp, Stats...
            date_cell = cells[0].get_text(strip=True)
            if not date_cell:
                continue
            
            try:
                # Try to parse date (format: MM/DD/YY)
                date_match = re.search(r'(\d+)/(\d+)/(\d{2,4})', date_cell)
                if not date_match:
                    continue
                
                m, d, y = date_match.groups()
                # Convert 2-digit year to 4-digit
                if len(y) == 2:
                    y = f"20{y}"
                game_date = f"{y}-{int(m):02d}-{int(d):02d}"
                
                # Extract team and opponent
                team_cell = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                opp_cell = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                
                # Skip "Inactive" entries
                if "Inactive" in " ".join([c.get_text(strip=True) for c in cells]):
                    continue
                
                # Build basic stat row
                row = {
                    "game_date": game_date,
                    "team": team_cell,
                    "opponent": opp_cell,
                    "league": LEAGUE,
                    "season": SEASON,
                    "sport_id": SPORT_ID,
                    "_raw": " ".join([c.get_text(strip=True) for c in cells[:10]]),
                }
                rows.append(row)
            except Exception:
                continue
        
        return rows if rows else []
    
    except Exception as e:
        print(f"    ✗ Parse error: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Scrape AAF player gamelogs")
    parser.add_argument("--batch", type=int, default=10, help="Players per run")
    args = parser.parse_args()
    
    print("=== AAF Gamelog Scraper ===\n")
    
    # Load AAF players
    players_file = RAW_DIR / "aaf_players.json"
    if not players_file.exists():
        print("✗ aaf_players.json not found")
        return
    
    aaf_players = json.loads(players_file.read_text())
    print(f"Loaded {len(aaf_players)} AAF players")
    
    # Load state
    state = load_state()
    
    # Find players to process
    to_process = [
        p for p in aaf_players
        if p.get("_aaf_url") and p["_aaf_url"] not in state.get("processed", {})
    ]
    
    print(f"Players with gamelogs: {len(to_process)}")
    print(f"Already processed: {len(state.get('processed', {}))}\n")
    
    if not to_process:
        print("All players processed!")
        return
    
    # Process up to --batch players
    batch = to_process[:args.batch]
    all_rows = []
    
    for i, player in enumerate(batch, 1):
        name = player.get("full_name", player.get("name", "Unknown"))
        url = player.get("_aaf_url", "")
        
        print(f"  [{i}/{len(batch)}] {name}...", end=" ")
        rows = fetch_gamelog(url, name)
        
        if rows is None:
            print("⚠")
            continue
        
        if not rows:
            print("(no data)")
            state["processed"][url] = "no_gamelogs"
            state["total_rows"] += 0
        else:
            print(f"({len(rows)} games)")
            all_rows.extend(rows)
            state["processed"][url] = len(rows)
            state["total_rows"] += len(rows)
    
    # Save accumulated rows
    if OUTPUT_FILE.exists():
        existing = json.loads(OUTPUT_FILE.read_text())
        all_rows = existing + all_rows
    
    OUTPUT_FILE.write_text(json.dumps(all_rows, indent=2))
    save_state(state)
    
    print(f"\nWrote {len(all_rows)} total gamelogs to {OUTPUT_FILE.name}")
    print(f"State saved. Run again to continue ({len(to_process) - len(batch)} players remaining)")


if __name__ == "__main__":
    main()

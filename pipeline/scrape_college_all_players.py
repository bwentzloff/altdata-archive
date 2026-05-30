# https://www.footballdb.com/players/nolan-henderson-hendeno01?src=search
# iterate through JSON files in docs/data/players/* and check if they were in one of the FOOTBALL_LEAGUES.
import json
import os
import time
from typing import Dict, Tuple
from bs4 import BeautifulSoup
import requests
from pathlib import Path
import re

FOOTBALL_LEAGUES = ['UFL', 'AF1', 'IFL', 'USFL', 'CFL']

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

RAW_DIR   = Path(__file__).parent / "raw"
RAW_DIR.mkdir(exist_ok=True)
CACHE_DIR = RAW_DIR / "_fdb_cache"
DELAY = 1.5   # seconds between real (non-cached) requests

def get_page(url: str) -> BeautifulSoup:
    """Fetch with disk cache.  Only sleeps DELAY on a real network request."""
    safe = re.sub(r"[^a-z0-9]", "_", url.lower())[:200]
    cache_file = CACHE_DIR / (safe + ".html")
    if cache_file.exists():
        return BeautifulSoup(cache_file.read_text(encoding="utf-8"), "lxml")
    time.sleep(DELAY)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    cache_file.write_text(resp.text, encoding="utf-8")
    return BeautifulSoup(resp.text, "lxml")

def _load_json(path: str, default=None):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def _save_json(path: str, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    players_dir = 'docs/data/players'
    all_players = []
    for filename in os.listdir(players_dir):
        if filename.endswith('.json'):
            player_data = _load_json(os.path.join(players_dir, filename))
            if player_data and 'leagues' in player_data:
                leagues = player_data['leagues']
                if any(league in FOOTBALL_LEAGUES for league in leagues):
                    all_players.append(player_data)

    for player in all_players:
        football_db_url = f"https://www.footballdb.com/players/{player['canonical_name'].replace(' ', '-').lower()}-{player['canonical_name'].split(' ')[1].lower()[:5]}{player['canonical_name'].split(' ')[0].lower()[:2]}01?src=search"
        player['football_db_url'] = football_db_url
        # Scrape football_db_url. The necessary data is in a table with id "tbl_P_reg". We only want rows where columns 2 = "FCS"
        print(f"Checking {player['canonical_name']} at {football_db_url}...")
        soup = None
        try:
            soup = get_page(football_db_url)
        except Exception as e:
            print(f"Error fetching {football_db_url}: {e}")
            continue
        if soup:
            print(f"Successfully fetched page for {player['canonical_name']}. Parsing...")
            table = soup.find('table', id='tbl_P_reg')
            if table:
                rows = table.find_all('tr')[1:]  # Skip header row
                college_json = None

                for row in rows:
                    print(f"Checking row: {row.text.strip()}")
                    cols = row.find_all('td')
                    print(f"Columns: {[col.text.strip() for col in cols]}")
                    if len(cols) > 1 and (cols[2].text.strip() == 'FCS' or cols[2].text.strip() == 'FBS'):
                        season_year = cols[1].text.strip()
                        
                        print(player)
                        exit()
                        break
                college_json = {
                    "school": cols[3].text.strip(),
                    "fdb_url": football_db_url,
                    "seasons": {},  # This would require more parsing to fill out
                    "career": {},   # This would require more parsing to fill out
                }
                if college_json:
                    player['college'] = college_json
                    print(f"Updated {player['canonical_name']} with college data: {college_json}")

if __name__ == "__main__":
    main()
        



"college": {
    "school": "CINN",
    "fdb_url": "/players/zach-collaros-collaza01",
    "seasons": {
      "2011": {
        "pass_att": 272.0,
        "pass_cmp": 166.0,
        "pass_yds": 1934.0,
        "pass_td": 15.0,
        "pass_int": 10.0
      },
      "2010": {
        "pass_att": 383.0,
        "pass_cmp": 225.0,
        "pass_yds": 2902.0,
        "pass_td": 26.0,
        "pass_int": 14.0
      }
    },
    "career": {
      "pass_att": 655.0,
      "pass_cmp": 391.0,
      "pass_yds": 4836.0,
      "pass_td": 41.0,
      "pass_int": 24.0
    }
  },
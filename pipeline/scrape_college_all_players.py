# https://www.footballdb.com/players/nolan-henderson-hendeno01?src=search

FOOTBALL_LEAGUES = ['UFL', 'AF1', 'IFL', 'USFL', 'CFL']

# iterate through JSON files in docs/data/players/* and check if they were in one of the FOOTBALL_LEAGUES.
import json
import os
from typing import Dict, Tuple

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
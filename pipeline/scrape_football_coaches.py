#!/usr/bin/env python3
"""
scrape_football_coaches.py — Extract coaching staff from Wikipedia football season articles.

Sources: Wikipedia season articles for various football leagues
  - XFL (2020, 2023-present)
  - USFL (2022-present) 
  - AAF (2019)
  - IFL, NAL, LFA (various years on Wikipedia)

Outputs (all in pipeline/raw/):
  football_coaches.json   — synthetic coach records (IDs starting at 2000000)
  football_coaches_raw.json  — cache of parsed data

Each coach record has:
  {
    "id": int,
    "full_name": str,
    "team": str (team abbreviation),
    "role": str (Head Coach, OC, DC, etc.),
    "league": str,
    "_year": int,
    "is_coach": True,
    "sport_id": None,  (will be filled in by build_data.py)
    "_source_url": str,
  }

Usage:
  python pipeline/scrape_football_coaches.py              # fetch/update all coaches
  python pipeline/scrape_football_coaches.py --cache-only # use cached data only
"""

import argparse
import json
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from coach_utils import (
    extract_coaches_from_wikipedia_roster,
    normalize_coach_role,
    dedup_coaches,
)

# Paths
BASE = Path(__file__).parent
RAW = BASE / "raw"
RAW.mkdir(exist_ok=True)

COACHES_FILE = RAW / "football_coaches.json"
COACHES_RAW_FILE = RAW / "football_coaches_raw.json"
STATE_FILE = RAW / "football_coaches_state.json"

HEADERS = {
    "User-Agent": (
        "AltSportsArchive/1.0 (https://archive.altfantasysports.com; "
        "altfantasysports@gmail.com) Python/requests"
    )
}

SYNTHETIC_ID_START = 2_000_000

WIKI_API = "https://en.wikipedia.org/w/api.php"

# League configurations: Wikipedia page titles and target years
FOOTBALL_LEAGUES = {
    "XFL": [
        (2020, "2020_XFL_season"),
        (2023, "2023_XFL_season"),
        (2024, "2024_XFL_season"),
        (2025, "2025_XFL_season"),
    ],
    "USFL": [
        (2022, "2022_USFL_season"),
        (2023, "2023_USFL_season"),
        (2024, "2024_USFL_season"),
    ],
    "AAF": [
        (2019, "2019_Alliance_of_American_Football_season"),
    ],
    "IFL": [
        (2023, "2023_Indoor_Football_League_season"),
        (2024, "2024_Indoor_Football_League_season"),
    ],
    "NAL": [
        (2022, "2022_North_American_Football_League_season"),
        (2023, "2023_North_American_Football_League_season"),
    ],
}


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
        print(f"  Fetching {page_title} from Wikipedia …")
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR: {e}")
        return None

    data = r.json()
    if "error" in data:
        print(f"  API error: {data['error'].get('info', 'unknown')}")
        return None

    html = data.get("parse", {}).get("text", {}).get("*", "")
    cache[cache_key] = html
    return html


def extract_team_coaches_from_html(
    html: str,
    year: int,
    league: str,
) -> list[dict]:
    """
    Extract coaches for all teams in a season from Wikipedia season article.
    Returns list of {name, role, team, league, _year, _source}
    """
    coaches = []
    soup = BeautifulSoup(html, "html.parser")

    # Strategy: Look for coaching staff tables with Team | Head Coach columns
    # Usually structured as: Team | Head Coach | or similar variations
    for table in soup.find_all("table", {"class": "wikitable"}):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        
        # Check if this looks like a coaching table
        if not any(kw in " ".join(headers) for kw in ["team", "coach", "head"]):
            continue
        
        # Find indices of relevant columns
        team_col = None
        coach_col = None
        
        for i, header in enumerate(headers):
            if any(kw in header for kw in ["team", "franchise"]):
                team_col = i
            if any(kw in header for kw in ["coach", "head"]):
                coach_col = i
        
        # Only process if we found both columns
        if team_col is None or coach_col is None:
            continue
        
        # Extract rows
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= max(team_col or 0, coach_col or 0):
                continue
            
            try:
                team_text = cells[team_col].get_text(strip=True) if team_col is not None else ""
                coach_text = cells[coach_col].get_text(strip=True) if coach_col is not None else ""
                
                # Clean up text (remove citations, links, etc.)
                team_text = team_text.split("(")[0].split("[")[0].strip()
                coach_text = coach_text.split("(")[0].split("[")[0].strip()
                
                # Try to extract team abbreviation from text
                # Common formats: "Team Name (XX)" or just "Team Name"
                abbr_match = re.search(r"\(([A-Z]{2,3})\)", cells[team_col].get_text(strip=True))
                team_abbr = abbr_match.group(1) if abbr_match else team_text[:3].upper()
                
                if coach_text and len(coach_text) > 2:
                    coaches.append({
                        "name": coach_text,
                        "role": "Head Coach",
                        "team": team_abbr,
                        "league": league,
                        "_year": year,
                        "_source": "wikipedia_coaching_table",
                    })
            except (IndexError, AttributeError):
                continue
    
    return coaches


def scrape_league_season(
    league: str,
    year: int,
    wiki_page: str,
    cache: dict,
    reset: bool = False,
) -> list[dict]:
    """Scrape coaches for one league season."""
    print(f"Scraping {league} {year} coaches from Wikipedia …")

    html = fetch_wiki_html(wiki_page, cache, reset=reset)
    if not html:
        print(f"  Could not fetch {wiki_page}")
        return []

    coaches = extract_team_coaches_from_html(html, year, league)

    # Normalize roles and deduplicate
    for coach in coaches:
        coach["role"] = normalize_coach_role(coach.get("role", ""))

    coaches = dedup_coaches(coaches)

    print(f"  Found {len(coaches)} coaches")
    return coaches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Ignore cache, fetch fresh data",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Use cached data only, don't fetch new",
    )
    args = parser.parse_args()

    # Load cache
    cache = json.loads(COACHES_RAW_FILE.read_text()) if COACHES_RAW_FILE.exists() else {}
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"processed": []}

    all_coaches = []
    synthetic_id = SYNTHETIC_ID_START

    # Process each league
    for league, seasons in FOOTBALL_LEAGUES.items():
        for year, wiki_page in seasons:
            season_key = f"{league}_{year}"
            
            if args.cache_only and season_key not in state.get("processed", []):
                print(f"Skipping {season_key} (not cached, cache-only mode)")
                continue

            season_coaches = scrape_league_season(
                league, year, wiki_page, cache, reset=args.reset
            )

            # Build synthetic coach records
            for coach in season_coaches:
                coach_record = {
                    "id": synthetic_id,
                    "full_name": coach["name"],
                    "short_name": coach["name"],
                    "first_name": coach["name"].split()[0] if coach["name"] else "",
                    "last_name": " ".join(coach["name"].split()[1:]) if coach["name"] else "",
                    "position": coach.get("role", "Head Coach"),
                    "team": coach.get("team", ""),
                    "sport_id": None,  # Will be filled in by build_data.py
                    "league": coach.get("league", ""),
                    "jersey": None,
                    "college": None,
                    "height": None,
                    "weight": None,
                    "is_coach": True,
                    "_year": coach.get("_year"),
                    "_source": coach.get("_source", "wikipedia"),
                }
                all_coaches.append(coach_record)
                synthetic_id += 1

            state.setdefault("processed", []).append(season_key)
            time.sleep(0.5)  # Be respectful to Wikipedia

    # Write outputs
    COACHES_FILE.write_text(json.dumps(all_coaches, indent=2), encoding="utf-8")
    COACHES_RAW_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"\nWrote {len(all_coaches)} coaches to {COACHES_FILE}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scrape_xleague.py — X-League (Japan) professional American football statistics

X-League (エックスリーグ) is the top-tier professional American football league in Japan.
Official website: https://xleague.jp/ (currently under redesign)

Sources:
1. Wikipedia X-League_(Japan) — MVP, ROY awards, team divisions, historical rosters
2. american-football-japan.com — Annual statistics PDFs (2005-2025)
   - Team rosters, individual player stats (passing/rushing/receiving yards, TDs)
   - Game summaries with per-player performance
   - English versions available for recent years
3. xleague.jp — Official schedule, standings (accessible via Wayback Machine or current redesign)
4. Individual team official websites — Rosters and player profiles

Outputs (pipeline/raw/):
  xleague_players.json   — {id, full_name, team, position, league, season, _data_source}
  xleague_stats.json     — {player_id, season, league, stat, value, game_id, _year, _source}
  xleague_teams.json     — {team_name, season_years, players_count}
  xleague_raw.json       — Cached parsed data (awards, rosters, game stats)

Integration: build_data.py loads these files and merges with canonical player list.
Players display with X-League stats in career tables, game logs, and league pages.
Team pages show rosters and seasonal performance.

Usage:
  python pipeline/scrape_xleague.py              # Scrape MVP/ROTY + team rosters
  python pipeline/scrape_xleague.py --pdf 2023  # Attempt PDF parsing for 2023 season
  python pipeline/scrape_xleague.py --all       # Scrape all available years + PDFs
  python pipeline/scrape_xleague.py --status    # Show progress
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

# Try to import PDF library
try:
    import PyPDF2
    HAS_PDF_SUPPORT = True
except ImportError:
    HAS_PDF_SUPPORT = False

# Paths
BASE = Path(__file__).parent
RAW_DIR = BASE / "raw"
RAW_DIR.mkdir(exist_ok=True)

PLAYERS_FILE = RAW_DIR / "xleague_players.json"
STATS_FILE = RAW_DIR / "xleague_stats.json"
TEAMS_FILE = RAW_DIR / "xleague_teams.json"
STATE_FILE = RAW_DIR / "xleague_scrape_state.json"
RAW_FILE = RAW_DIR / "xleague_raw.json"
CACHE_FILE = RAW_DIR / "_xleague_wiki_cache.json"
PDF_CACHE_DIR = RAW_DIR / "_xleague_pdf_cache"
PDF_CACHE_DIR.mkdir(exist_ok=True)

# Config
WIKI_API = "https://en.wikipedia.org/w/api.php"
PDF_BASE_URL = "http://www.american-football-japan.com"
SYNTHETIC_ID_START = 800_000  # X-League IDs: 800K+
HEADERS = {
    "User-Agent": "AltSportsArchive/1.0 (https://archive.altfantasysports.com; altfantasysports@gmail.com) Python/requests"
}

# X-League team names (standardized)
XLEAGUE_TEAMS_INFO = {
    "Panasonic Impulse": {"abbr": "IMP", "active_since": 1997},
    "Obic Seagulls": {"abbr": "SEA", "active_since": 1997},
    "Fujitsu Frontiers": {"abbr": "FRO", "active_since": 1997},
    "Nojima Sagamihara Rise": {"abbr": "RISE", "active_since": 2005},
    "Tokyo Gas Creators": {"abbr": "CRE", "active_since": 2005},
    "SEKISUI Challengers": {"abbr": "CHA", "active_since": 2007},
    "Elecom Kobe Finies": {"abbr": "FIN", "active_since": 2008},
    "IBM Big Blue": {"abbr": "BLUE", "active_since": 2005},
    "All Mitsubishi Lions": {"abbr": "LION", "active_since": 2005},
    "LIXIL Deers": {"abbr": "DEER", "active_since": 2005},
    "Asahi Beer Silver Star": {"abbr": "STAR", "active_since": 2005},
    "Dentsu Club Caterpillars": {"abbr": "CAT", "active_since": 2010},
    "Fujitsu Ebina Minerva AFC": {"abbr": "MIN", "active_since": 2015},
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


def fetch_pdf_statistics(season: int) -> dict:
    """
    Attempt to fetch and parse X-League statistics PDF for a given season.
    Returns {player_name: {stat_key: value}}
    """
    if not HAS_PDF_SUPPORT:
        print(f"    PyPDF2 not installed, skipping PDF parsing for {season}")
        return {}
    
    # PDF URL pattern from american-football-japan.com
    pdf_url = f"{PDF_BASE_URL}/footballjapan-xleague{season}-statistics-eng.pdf"
    cache_file = PDF_CACHE_DIR / f"xleague_{season}.pdf"
    
    # Try to download PDF
    try:
        if not cache_file.exists():
            print(f"    Fetching PDF: {pdf_url}")
            r = requests.get(pdf_url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            cache_file.write_bytes(r.content)
            print(f"    Downloaded {season} PDF ({len(r.content)} bytes)")
        
        # Parse PDF
        print(f"    Parsing PDF for {season}...")
        with open(cache_file, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for page_num, page in enumerate(reader.pages[:15]):
                text += page.extract_text()
                if page_num > 0 and len(text) > 200000:
                    break
        
        stats_data = {}
        
        # Improved pattern: "PlayerName (Team) stat1 stat2 stat3..."
        # Handles Western names, Japanese romanization (ū ō ā ē ī), hyphens, apostrophes
        player_line_pattern = r"^([A-Z][a-zA-Z\s\-\.\'ūōāēīéōĀĒĪŌŪ]+)\s+\(([A-Za-z\s]+)\)\s+(.+)$"
        
        for line in text.split('\n'):
            match = re.match(player_line_pattern, line.strip())
            if not match:
                continue
            
            name = match.group(1).strip()
            team = match.group(2).strip()
            stats_str = match.group(3).strip()
            
            # Skip if name is too short or contains only numbers
            if len(name) < 2 or re.match(r"^\d+", name):
                continue
            
            # Parse stat values (handle both integers and floats)
            try:
                stat_vals = []
                for val in stats_str.split():
                    try:
                        if '.' in val:
                            stat_vals.append(float(val))
                        elif val != '-':  # Skip dashes (missing values)
                            stat_vals.append(int(val))
                    except ValueError:
                        # Skip non-numeric values (section headers, etc)
                        pass
            except Exception:
                continue
            
            if not stat_vals:
                continue
            
            # Normalize name for deduplication
            norm_name = name.lower().strip()
            
            if norm_name not in stats_data:
                stats_data[norm_name] = {
                    "full_name": name,
                    "team": team,
                    "raw_values": stat_vals,
                }
            else:
                # Update if this entry has more stats
                if len(stat_vals) > len(stats_data[norm_name].get("raw_values", [])):
                    stats_data[norm_name]["full_name"] = name
                    stats_data[norm_name]["team"] = team
                    stats_data[norm_name]["raw_values"] = stat_vals
        
        # Post-process: assign stat meanings based on value heuristics
        result = {}
        for norm_name, entry in stats_data.items():
            if not entry.get("raw_values"):
                continue
            
            stats_dict = {
                "full_name": entry["full_name"],
                "team": entry["team"],
            }
            
            # Categorize stats based on typical ranges
            raw = entry["raw_values"][:10]  # Limit to first 10 values
            
            # Heuristics for stat categorization:
            # - Attempts: 3-100
            # - Yards: 50-3000
            # - Average: 0-20
            # - TD/Points: 0-50
            # - Percentage: 0-100
            
            if len(raw) >= 5:
                # Pattern: Attempts Yards Avg Long TD (rushing/receiving)
                if 3 < raw[0] < 100 and 50 < raw[1] < 3000:
                    stats_dict["attempts"] = raw[0]
                    stats_dict["yards"] = raw[1]
                    stats_dict["avg"] = raw[2]
                    stats_dict["long"] = raw[3]
                    stats_dict["td"] = raw[4]
            
            if stats_dict.get("yards") or stats_dict.get("points"):
                result[entry["full_name"]] = stats_dict
        
        if result:
            print(f"    Extracted {len(result)} player entries from {season} PDF")
        
        return result
    
    except requests.RequestException as e:
        print(f"    Could not fetch PDF: {e}")
    except Exception as e:
        print(f"    Error parsing PDF: {e}")
    
    return {}


def scrape_team_rosters(season: int) -> dict[str, list[str]]:
    """
    Attempt to scrape team rosters from xleague.jp or team websites.
    Returns {team_name: [player_names]}
    """
    rosters = {}
    
    for team_name in XLEAGUE_TEAMS_INFO.keys():
        # Check if team was active in this season
        if season < XLEAGUE_TEAMS_INFO[team_name]["active_since"]:
            continue
        
        rosters[team_name] = []
        
        # Try to fetch team roster from official site (placeholder for now)
        # In reality, this would need team-specific URLs once xleague.jp is fully accessible
        # For now, we'll rely on Wikipedia and PDF data
    
    return rosters


def merge_player_records(awards: list[dict], pdf_stats: dict[int, dict], rosters: dict[int, dict]) -> tuple[list[dict], list[dict], dict]:
    """
    Merge player data from multiple sources (awards, PDFs, rosters).
    Returns (players_list, stats_list, teams_info)
    """
    players = []
    stats = []
    teams_info = {}
    
    player_by_name: dict[str, dict] = {}
    player_id_map: dict[str, int] = {}
    next_id = SYNTHETIC_ID_START
    
    # First pass: awards (MVP/ROTY)
    for award in awards:
        name = award.get("name", "").strip()
        team = award.get("team", "")
        position = award.get("position", "")
        year = award.get("_year")
        
        if not name or len(name) < 2:
            continue
        
        norm_name = name.lower()
        if norm_name not in player_by_name:
            player_rec = {
                "id": next_id,
                "full_name": name,
                "team": team,
                "position": position,
                "league": "X-League",
                "seasons": {year} if year else set(),
                "_sources": ["wikipedia_awards"],
            }
            player_by_name[norm_name] = player_rec
            player_id_map[norm_name] = next_id
            next_id += 1
        else:
            # Update existing player record
            if year and not player_by_name[norm_name].get("seasons"):
                player_by_name[norm_name]["seasons"] = set()
            if year:
                player_by_name[norm_name]["seasons"].add(year)
            if team and not player_by_name[norm_name].get("team"):
                player_by_name[norm_name]["team"] = team
            if position and not player_by_name[norm_name].get("position"):
                player_by_name[norm_name]["position"] = position
    
    # Second pass: PDF statistics
    for year, player_stats in pdf_stats.items():
        for name, stats_dict in player_stats.items():
            norm_name = name.lower()
            if norm_name not in player_by_name:
                player_rec = {
                    "id": next_id,
                    "full_name": name,
                    "team": "",
                    "position": "Unknown",
                    "league": "X-League",
                    "seasons": {year},
                    "_sources": ["pdf_statistics"],
                }
                player_by_name[norm_name] = player_rec
                player_id_map[norm_name] = next_id
                next_id += 1
            else:
                if year and not player_by_name[norm_name].get("seasons"):
                    player_by_name[norm_name]["seasons"] = set()
                if year:
                    player_by_name[norm_name]["seasons"].add(year)
    
    # Convert players to list (convert sets to lists for JSON serialization)
    for player in player_by_name.values():
        player["seasons"] = sorted(list(player.get("seasons", set())))
        players.append(player)
    
    # Create stat rows
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
                "_source": "wikipedia_awards",
            })
    
    # Add stats from PDFs
    for year, player_stats in pdf_stats.items():
        for name, stats_dict in player_stats.items():
            norm_name = name.lower()
            pid = player_id_map.get(norm_name)
            if pid:
                game_id = f"FOOTBALL_XLEAGUE_{year}_SEASON_TOTAL"
                for stat_key, value in stats_dict.items():
                    # Skip metadata fields
                    if stat_key in ("full_name", "team", "raw_values"):
                        continue
                    # Skip non-numeric values
                    try:
                        val_float = float(value)
                        if val_float > 0:
                            stats.append({
                                "player_id": pid,
                                "week": 1,
                                "stat": stat_key,
                                "value": val_float,
                                "game_id": game_id,
                                "league": "X-League",
                                "_year": year,
                                "_source": "pdf_statistics",
                            })
                    except (TypeError, ValueError):
                        # Skip non-numeric values
                        pass
    
    # Build teams info
    for team_name, team_data in XLEAGUE_TEAMS_INFO.items():
        active_years = []
        for player in players:
            if player.get("team") == team_name:
                active_years.extend(player.get("seasons", []))
        
        if active_years:
            teams_info[team_name] = {
                "name": team_name,
                "abbr": team_data.get("abbr"),
                "seasons": sorted(list(set(active_years))),
                "player_count": len([p for p in players if p.get("team") == team_name]),
            }
    
    return players, stats, teams_info


def main():
    parser = argparse.ArgumentParser(description="Scrape X-League (Japan) statistics from multiple sources")
    parser.add_argument("--reset", action="store_true", help="Re-fetch from Wikipedia (ignore cache)")
    parser.add_argument("--pdf", nargs="+", type=int, help="Attempt to parse PDFs for specific seasons")
    parser.add_argument("--all-pdf", action="store_true", help="Attempt to parse PDFs for all seasons")
    parser.add_argument("--status", action="store_true", help="Show current data without scraping")
    args = parser.parse_args()
    
    if args.status:
        if PLAYERS_FILE.exists():
            players = json.loads(PLAYERS_FILE.read_text())
            stats = json.loads(STATS_FILE.read_text()) if STATS_FILE.exists() else []
            print(f"Current X-League data: {len(players)} players, {len(stats)} stats")
            if TEAMS_FILE.exists():
                teams = json.loads(TEAMS_FILE.read_text())
                print(f"Teams: {len(teams)}")
        else:
            print("No X-League data yet")
        return
    
    print("==> Scraping X-League (Japan) from multiple sources")
    
    # Fetch Wikipedia page for awards
    html = fetch_wiki_html_cached("X-League_(Japan)", reset=args.reset)
    if not html:
        print("  Failed to fetch Wikipedia page")
        return
    
    # Parse HTML
    soup = BeautifulSoup(html, "html.parser")
    awards = parse_award_tables(soup)
    print(f"  Found {len(awards)} award records from Wikipedia")
    
    # Determine which PDF seasons to attempt
    pdf_seasons = []
    if args.all_pdf:
        pdf_seasons = list(range(2005, datetime.now().year + 1))
    elif args.pdf:
        pdf_seasons = args.pdf
    
    # Fetch PDF statistics if requested
    pdf_stats = {}
    if pdf_seasons:
        if not HAS_PDF_SUPPORT:
            print("  PyPDF2 not installed. Install with: pip install PyPDF2")
            print("  Continuing with Wikipedia awards only...")
        else:
            for season in pdf_seasons:
                season_stats = fetch_pdf_statistics(season)
                if season_stats:
                    pdf_stats[season] = season_stats
            if pdf_stats:
                total_pdf_entries = sum(len(v) for v in pdf_stats.values())
                print(f"  Extracted {total_pdf_entries} player entries from {len(pdf_stats)} PDF files")
    
    # Scrape team rosters (placeholder for now)
    team_rosters = {}
    
    # Merge all data sources
    if awards or pdf_stats:
        players, stats, teams_info = merge_player_records(awards, pdf_stats, team_rosters)
        years = sorted(set(s["_year"] for s in stats))
        
        print(f"  Merged data: {len(players)} players, {len(stats)} stat rows")
        print(f"  Seasons covered: {years}")
        print(f"  Teams with rosters: {len(teams_info)}")
        
        # Write output files
        PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
        STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        TEAMS_FILE.write_text(json.dumps(teams_info, indent=2), encoding="utf-8")
        
        # Write raw data for debugging
        RAW_FILE.write_text(
            json.dumps({
                "awards": awards,
                "pdf_entries": sum(len(v) for v in pdf_stats.values()),
                "teams": list(teams_info.keys())
            }, indent=2),
            encoding="utf-8"
        )
        
        print(f"✓ Wrote {len(players)} players to {PLAYERS_FILE.name}")
        print(f"✓ Wrote {len(stats)} stats to {STATS_FILE.name}")
        print(f"✓ Wrote {len(teams_info)} teams to {TEAMS_FILE.name}")
    else:
        print("  No data found - check sources")


if __name__ == "__main__":
    main()

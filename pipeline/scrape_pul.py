#!/usr/bin/env python3
"""
scrape_pul.py — Premier Ultimate League stats backfill
Source: pul-stats-hub.pages.dev  (seasons 2022–present)

Strategy:
  1. Discover player URLs by scraping each team page on the hub
     (14 all-time teams → deduplicated player list)
  2. Fetch each player's detail page (/players/{name}) for season stats
  3. Cache everything in pul_state.json; --batch limits new fetches per run

Outputs (all in pipeline/raw/):
  pul_players.json   — synthetic player records (IDs starting at 600000)
  pul_stats.json     — synthetic stat rows (season totals per player per year)
  pul_state.json     — cache: player_urls + per-player season data

Injection: build_data.py reads these and extends raw_players + raw_stats.
"""

import argparse
import json
import re
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW  = BASE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "pul_players.json"
STATS_FILE   = RAW / "pul_stats.json"
STATE_FILE   = RAW / "pul_state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

BASE_URL           = "https://pul-stats-hub.pages.dev"
TEAMS_URL          = f"{BASE_URL}/teams"
SYNTHETIC_ID_START = 600_000

# Only backfill completed or current PUL seasons
TARGET_YEARS = {2022, 2023, 2024, 2025, 2026}


def safe_float(v) -> float:
    if v is None or str(v).strip() in ("", "-", "—"):
        return 0.0
    try:
        f = float(str(v).replace(",", ""))
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def strip_arrows(s: str) -> str:
    """Remove sort-arrow glyphs from column headers (e.g. 'Goals↕' → 'Goals')."""
    return re.sub(r"[↑↓↕]", "", s).strip()


# ── Player discovery ──────────────────────────────────────────────────────────

def discover_players(state: dict, reset: bool) -> list[str]:
    """
    Scrape each team page to build a deduplicated list of /players/{name} URLs.
    Returns list of player URL paths (e.g. '/players/Julia%20Hoffmann').
    """
    if "player_urls" in state and not reset:
        print(f"Using cached player list ({len(state['player_urls'])} players)")
        return state["player_urls"]

    print("Discovering PUL players from team pages …")

    # Step 1: get all unique team base URLs from /teams
    r = requests.get(TEAMS_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    team_paths: list[str] = []
    seen_teams: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/teams/") and "?" not in href and href not in seen_teams:
            seen_teams.add(href)
            team_paths.append(href)

    print(f"  Found {len(team_paths)} unique team pages")

    # Step 2: fetch each team page and collect player links
    all_player_paths: list[str] = []
    seen_players: set[str] = set()

    for team_path in sorted(team_paths):
        team_url = BASE_URL + team_path
        try:
            tr = requests.get(team_url, headers=HEADERS, timeout=20)
            if tr.status_code != 200:
                print(f"  WARN: {tr.status_code} for {team_path}")
                continue
            tsoup = BeautifulSoup(tr.text, "html.parser")
            team_name = urllib.parse.unquote(team_path.split("/teams/")[1])
            count = 0
            for a in tsoup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/players/") and href not in seen_players:
                    seen_players.add(href)
                    all_player_paths.append(href)
                    count += 1
            print(f"  {team_name}: {count} new players")
        except requests.RequestException as exc:
            print(f"  WARN: {exc} for {team_path}")
        time.sleep(0.3)

    print(f"  Total unique players discovered: {len(all_player_paths)}")
    state["player_urls"] = all_player_paths
    return all_player_paths


# ── Per-player page fetch ─────────────────────────────────────────────────────

def fetch_player_page(player_path: str, state: dict) -> list[dict]:
    """
    Fetch /players/{name} and return list of per-season stat dicts.
    Cached in state under "player:{path}".
    """
    cache_key = f"player:{player_path}"
    if cache_key in state and state[cache_key] is not None:
        return state[cache_key]

    url = BASE_URL + player_path

    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20 + attempt * 10)
            if r.status_code == 200:
                break
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  HTTP 429 — waiting {wait}s …")
                time.sleep(wait)
                continue
            # Non-retryable error
            state[cache_key] = []
            return []
        except requests.RequestException as exc:
            if attempt < 2:
                print(f"  WARN: {exc} — retrying ({attempt+1}/3)")
                time.sleep(5 * (attempt + 1))
            else:
                print(f"  WARN: {exc} — giving up on {url}")
                return []   # do NOT cache failures
    else:
        return []   # do NOT cache

    soup = BeautifulSoup(r.text, "html.parser")

    # Player name from H1
    h1 = soup.find("h1")
    player_name = h1.get_text(" ", strip=True) if h1 else ""
    if not player_name:
        player_name = urllib.parse.unquote(
            player_path.split("/players/")[1]
        ).replace("+", " ").strip()

    # Team: find most recent from the team/year pairs in the profile area
    team = ""
    flex_div = soup.find("div", class_=lambda c: c and "flex-wrap" in c and "gap-4" in c)
    if flex_div:
        bold = flex_div.find("div", class_=lambda c: c and "font-bold" in c)
        if bold:
            team = bold.get_text(strip=True)

    # First table = season stats: Season | Team | Goals | Assists | Blocks | Turnovers | Touches | O-Pts | D-Pts | +/-
    tables = soup.find_all("table")
    seasons: list[dict] = []

    if tables:
        table = tables[0]
        raw_headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not raw_headers:
            first_tr = table.find("tr")
            if first_tr:
                raw_headers = [td.get_text(strip=True) for td in first_tr.find_all("td")]
        col_headers = [strip_arrows(h) for h in raw_headers]

        if "Season" not in col_headers and "Goals" not in col_headers:
            state[cache_key] = []
            return []

        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            row = dict(zip(col_headers, cells))

            season_val = row.get("Season", "").strip()
            if not season_val or not season_val.isdigit():
                continue
            year = int(season_val)
            if year not in TARGET_YEARS:
                continue

            row_team = row.get("Team", team).strip()

            seasons.append({
                "_year":      year,
                "_name":      player_name,
                "_team":      row_team or team,
                "goals":      row.get("Goals",     "0"),
                "assists":    row.get("Assists",    "0"),
                "blocks":     row.get("Blocks",     "0"),
                "turnovers":  row.get("Turnovers",  "0"),
                "touches":    row.get("Touches",    "0"),
                "o_points":   row.get("O-Pts",      "0"),
                "d_points":   row.get("D-Pts",      "0"),
                "plus_minus": row.get("+/-",        "0"),
            })

    state[cache_key] = seasons
    return seasons


# ── Output builders ───────────────────────────────────────────────────────────

def build_outputs(
    all_seasons: dict[str, list[dict]],
) -> tuple[list[dict], list[dict]]:
    """
    all_seasons: {player_path: [season_dicts]}
    Returns (players_list, stats_list).
    """
    out_players: list[dict] = []
    out_stats:   list[dict] = []
    synthetic_id = SYNTHETIC_ID_START

    for player_path, seasons in sorted(all_seasons.items()):
        if not seasons:
            continue

        player_name = ""
        for s in seasons:
            if s.get("_name"):
                player_name = s["_name"]
                break
        if not player_name:
            player_name = urllib.parse.unquote(
                player_path.split("/players/")[1]
            ).replace("+", " ").strip()
        if not player_name:
            continue

        team = ""
        for s in reversed(seasons):
            if s.get("_team"):
                team = s["_team"]
                break

        parts = player_name.split()
        first = parts[0] if parts else ""
        last  = parts[-1] if len(parts) > 1 else ""

        out_players.append({
            "id":              synthetic_id,
            "full_name":       player_name,
            "short_name":      player_name,
            "first_name":      first,
            "last_name":       last,
            "sport_id":        None,
            "league":          "PUL",
            "team":            team,
            "position":        "",
            "_pul":            True,
            "_pul_player_url": player_path,
            "_norm_name":      player_name.lower(),
            "sportradar_id":   None,
            "college":         None,
            "jersey":          None,
            "height":          None,
            "weight":          None,
        })

        for s in seasons:
            year    = s["_year"]
            game_id = f"PUL_{year}_SEASON_TOTAL"

            stat_pairs = [
                ("goals",      safe_float(s.get("goals"))),
                ("assists",    safe_float(s.get("assists"))),
                ("blocks",     safe_float(s.get("blocks"))),
                ("turnovers",  safe_float(s.get("turnovers"))),
                ("touches",    safe_float(s.get("touches"))),
                ("o_points",   safe_float(s.get("o_points"))),
                ("d_points",   safe_float(s.get("d_points"))),
                ("plus_minus", safe_float(s.get("plus_minus"))),
            ]
            for stat_name, value in stat_pairs:
                if value:
                    out_stats.append({
                        "player_id": synthetic_id,
                        "week":      1,
                        "stat":      stat_name,
                        "value":     value,
                        "game_id":   game_id,
                        "_year":     year,
                    })

        synthetic_id += 1

    return out_players, out_stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Scrape PUL stats from pul-stats-hub.pages.dev"
    )
    ap.add_argument(
        "--reset", action="store_true",
        help="Ignore all caches and re-fetch from network"
    )
    ap.add_argument(
        "--batch", type=int, default=0,
        help="Max new player pages to fetch per run (0 = all pending)"
    )
    args = ap.parse_args()

    state: dict = {}
    if STATE_FILE.exists() and not args.reset:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    # Step 1: discover all player URLs from team pages
    player_urls = discover_players(state, reset=args.reset)
    if not player_urls:
        print("No player URLs found — nothing to do.")
        return

    # Step 2: determine which players still need fetching
    pending = [
        u for u in player_urls
        if args.reset or f"player:{u}" not in state
    ]
    if args.batch > 0:
        pending = pending[:args.batch]

    already_cached = len(player_urls) - len(pending)
    print(f"Players cached: {already_cached}  |  to fetch: {len(pending)}")

    # Step 3: fetch pending player pages
    for i, url in enumerate(pending):
        if i and i % 25 == 0:
            print(f"  … {i}/{len(pending)} fetched")
            STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        fetch_player_page(url, state)
        time.sleep(0.4)   # ~2.5 req/s, polite

    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

    # Step 4: collect all cached season data
    all_seasons: dict[str, list[dict]] = {}
    for url in player_urls:
        key = f"player:{url}"
        if key in state and state[key]:
            all_seasons[url] = state[key]

    # Step 5: build output files
    players, stats = build_outputs(all_seasons)
    years = sorted({s["_year"] for s in stats})
    print(f"\nBuilt {len(players)} PUL players")
    print(f"Built {len(stats)} PUL stat rows  (years: {years})")

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats,   indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name} and {STATS_FILE.name}")


if __name__ == "__main__":
    main()

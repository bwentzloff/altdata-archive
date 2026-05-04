#!/usr/bin/env python3
"""
scrape_nll_historical.py — NLL historical stats backfill
Source: nll.com/players/ + individual player stat pages

Scrapes NLL seasons not yet in the DB:
  2019-20 through 2023-24
  (2024-25 is already in the live DB as sport id=30)

Each player's page at nll.com/players/{id}/{slug}/ has a season-by-season
stats table: Year | Team | GP | G | A | PTS | PPG | PPA | SHG | PIM

Outputs (all in pipeline/raw/):
  nll_historical_players.json  — synthetic player records (IDs starting at 400000)
  nll_historical_stats.json    — synthetic stat rows (one season-total row per stat)
  nll_historical_state.json    — cache: {player_url: [season_dicts]}

Injection: build_data.py reads these and extends raw_players + raw_stats.
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW = BASE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "nll_historical_players.json"
STATS_FILE   = RAW / "nll_historical_stats.json"
STATE_FILE   = RAW / "nll_historical_state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

BASE_URL     = "https://www.nll.com"
PLAYERS_URL  = "https://www.nll.com/players/"

SYNTHETIC_ID_START = 400_000

# NLL season label format: "2024-25" = start_year 2024
# The live DB has 2024-25 season (exported as "2025" in our system), so skip it.
SKIP_START_YEARS = {2024, 2025}   # 2024-25 in live DB; 2025-26 is future/in-progress


def safe_float(v) -> float:
    if v is None or str(v).strip() in ("", "-", "—"):
        return 0.0
    try:
        f = float(str(v).replace(",", ""))
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def season_start_year(label: str) -> int | None:
    """Convert '2023-24' → 2023.  Returns None if unparseable."""
    m = re.match(r"(\d{4})-\d{2}$", label.strip())
    return int(m.group(1)) if m else None


def infer_position(page_pos: str, stats: dict) -> str:
    """Prefer the on-page position; fall back to stat-based guess."""
    if page_pos:
        return page_pos
    g  = safe_float(stats.get("G"))
    a  = safe_float(stats.get("A"))
    gp = safe_float(stats.get("GP"))
    if g + a > 0:
        return "F"
    if gp > 0:
        return ""
    return ""


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def fetch_player_urls(state: dict, reset: bool) -> list[str]:
    """Return deduplicated list of /players/{id}/{slug}/ URLs."""
    if "player_urls" in state and not reset:
        print(f"Using cached player list ({len(state['player_urls'])} players)")
        return state["player_urls"]

    print("Fetching NLL player roster …")
    r = requests.get(PLAYERS_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if not table:
        print("ERROR: No table found on NLL players page — roster may be off-season")
        return []

    urls: list[str] = []
    seen: set[str] = set()
    for a in table.find_all("a", href=True):
        href = a["href"]
        # Match /players/{numeric-id}/{name-slug}/
        if re.match(r"^/players/\d+/[a-z0-9-]+/?$", href) and href not in seen:
            seen.add(href)
            urls.append(href)

    print(f"Found {len(urls)} unique player URLs")
    state["player_urls"] = urls
    return urls


def fetch_player_page(player_url: str, state: dict, reset: bool) -> list[dict]:
    """
    Fetch a player's stat page and return a list of per-season dicts.
    Cached in state under "player:{url}".
    """
    cache_key = f"player:{player_url}"
    # Only use cache for successful results — never cache failures/timeouts
    if cache_key in state and state[cache_key] is not None and not reset:
        return state[cache_key]

    url = BASE_URL + player_url

    # Retry up to 3 times with increasing timeout on transient errors
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20 + attempt * 15)
            if r.status_code == 200:
                break
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  HTTP 429 — waiting {wait}s …")
                time.sleep(wait)
                continue
            # Other HTTP error — don't retry
            return []   # do NOT cache
        except requests.RequestException as exc:
            if attempt < 2:
                print(f"  WARN: {exc} — retrying ({attempt+1}/3) …")
                time.sleep(5 * (attempt + 1))
            else:
                print(f"  WARN: {exc} — giving up on {url}")
                return []   # do NOT cache
    else:
        return []   # do NOT cache

    soup = BeautifulSoup(r.text, "html.parser")

    # Player name from first <h1>
    h1 = soup.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else ""
    # Clean off jersey/team suffixes sometimes appended
    name = re.sub(r"\s*#\d+.*$", "", name).strip()

    # Position: look for a label/value pair in player-bio section
    position = ""
    for el in soup.find_all(string=re.compile(r"^Position$", re.I)):
        sib = el.find_parent()
        if sib:
            nxt = sib.find_next_sibling()
            if nxt:
                position = nxt.get_text(strip=True)[:10]
                break

    # Extract stats from the FIRST table (regular season career log)
    tables = soup.find_all("table")
    seasons: list[dict] = []
    if tables:
        table = tables[0]
        col_headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not col_headers:
            # Some tables use <td> in header row
            first_tr = table.find("tr")
            if first_tr:
                col_headers = [td.get_text(strip=True) for td in first_tr.find_all("td")]

        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            row = dict(zip(col_headers, cells))

            season_label = row.get("Year", "").strip()
            if not season_label:
                continue

            start_year = season_start_year(season_label)
            if start_year is None:
                continue
            if start_year in SKIP_START_YEARS:
                continue
            if start_year < 2019:
                continue

            seasons.append({
                "_year":     start_year,
                "_season":   season_label,
                "_name":     name,
                "_position": position,
                "team":      row.get("Team", ""),
                "GP":        row.get("GP",  "0"),
                "G":         row.get("G",   "0"),
                "A":         row.get("A",   "0"),
                "PTS":       row.get("PTS", "0"),
                "PPG":       row.get("PPG", "0"),
                "PPA":       row.get("PPA", "0"),
                "SHG":       row.get("SHG", "0"),
                "PIM":       row.get("PIM", "0"),
            })

    state[cache_key] = seasons
    return seasons


# ── Output builders ───────────────────────────────────────────────────────────

def build_outputs(
    all_seasons: dict[str, list[dict]],
) -> tuple[list[dict], list[dict]]:
    """
    all_seasons: {player_url: [season_dicts]}
    Returns (players_list, stats_list).
    """
    out_players: list[dict] = []
    out_stats:   list[dict] = []
    synthetic_id = SYNTHETIC_ID_START

    for player_url, seasons in sorted(all_seasons.items()):
        if not seasons:
            continue

        # Derive name: prefer on-page name, fall back to URL slug
        player_name = ""
        for s in seasons:
            if s.get("_name"):
                player_name = s["_name"]
                break
        if not player_name:
            m = re.search(r"/players/\d+/([^/]+)/?$", player_url)
            if m:
                player_name = m.group(1).replace("-", " ").title()
        if not player_name:
            continue

        position  = seasons[0].get("_position", "") if seasons else ""
        team      = seasons[-1].get("team", "") if seasons else ""

        parts = player_name.split()
        first = parts[0] if parts else ""
        last  = parts[-1] if len(parts) > 1 else ""

        out_players.append({
            "id":             synthetic_id,
            "full_name":      player_name,
            "short_name":     player_name,
            "first_name":     first,
            "last_name":      last,
            "sport_id":       None,
            "league":         "NLL",
            "team":           team,
            "position":       position,
            "_nll_historical":  True,
            "_nll_player_url":  player_url,
            "_norm_name":       player_name.lower(),
            "sportradar_id":  None,
            "college":        None,
            "jersey":         None,
            "height":         None,
            "weight":         None,
        })

        for s in seasons:
            year     = s["_year"]
            game_id  = f"NLL_{year}_SEASON_TOTAL"

            stat_pairs = [
                ("games_played",     safe_float(s.get("GP"))),
                ("goals",            safe_float(s.get("G"))),
                ("assists",          safe_float(s.get("A"))),
                ("points",           safe_float(s.get("PTS"))),
                ("pp_goals",         safe_float(s.get("PPG"))),
                ("pp_assists",       safe_float(s.get("PPA"))),
                ("sh_goals",         safe_float(s.get("SHG"))),
                ("penalty_minutes",  safe_float(s.get("PIM"))),
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
    ap = argparse.ArgumentParser(description="Scrape NLL historical player stats")
    ap.add_argument("--reset", action="store_true",
                    help="Ignore all caches and re-fetch from network")
    ap.add_argument("--batch", type=int, default=0,
                    help="Max new player pages to fetch per run (0 = all pending)")
    args = ap.parse_args()

    # Load / reset state cache
    state: dict = {}
    if STATE_FILE.exists() and not args.reset:
        state = json.loads(STATE_FILE.read_text())

    # Step 1: get player URLs
    player_urls = fetch_player_urls(state, reset=args.reset)
    if not player_urls:
        print("No player URLs — nothing to do.")
        return

    # Step 2: determine which still need fetching
    pending = [
        u for u in player_urls
        if args.reset or f"player:{u}" not in state
    ]
    if args.batch > 0:
        pending = pending[:args.batch]

    already_cached = len(player_urls) - len(pending)
    print(f"Players cached: {already_cached}  |  to fetch: {len(pending)}")

    # Step 3: fetch pages
    for i, url in enumerate(pending):
        if i and i % 20 == 0:
            print(f"  … {i}/{len(pending)} fetched")
            STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        fetch_player_page(url, state, reset=args.reset)
        time.sleep(0.4)   # polite delay (~2.5 req/s)

    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

    # Step 4: build all_seasons from state
    all_seasons: dict[str, list[dict]] = {}
    for url in player_urls:
        key = f"player:{url}"
        if key in state:
            all_seasons[url] = state[key]

    # Step 5: build output
    players, stats = build_outputs(all_seasons)
    years = sorted({s["_year"] for s in stats})
    print(f"\nBuilt {len(players)} NLL historical players")
    print(f"Built {len(stats)} NLL historical stat rows  (years: {years})")

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats,   indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name} and {STATS_FILE.name}")


if __name__ == "__main__":
    main()

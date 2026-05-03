"""
scrape_aaf.py — Scrape 2019 AAF season stats from www.footballdb.com
Outputs:  pipeline/raw/aaf_2019_season.json   (player season totals)
          pipeline/raw/aaf_2019_games.json     (game results from scores page)

Usage:
  python pipeline/scrape_aaf.py

The script respects rate limits (1 req/s) and saves intermediate results so it
can be re-run without re-fetching already-cached pages.
"""

import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ───────────────────────────────────────────────────────────────────

BASE_URL = "https://www.footballdb.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}
DELAY = 1.5  # seconds between requests

RAW_DIR = Path(__file__).parent / "raw"
RAW_DIR.mkdir(exist_ok=True)

CACHE_DIR = RAW_DIR / "_fdb_cache"
CACHE_DIR.mkdir(exist_ok=True)

SPORT_ID = 8   # AAF in our sports table
SEASON   = "2019"

# ── Stat-page definitions ─────────────────────────────────────────────────────
# Each entry: (slug, our_stat_key_prefix, column_map)
# column_map: list of (page_col_index_after_player, our_stat_key)
#   column 0 is always the player/team cell.

STAT_PAGES = [
    {
        "slug": "passing",
        "cols": [
            # GP  Att  Cmp  Pct  Yds  YPA  TD  TDpct  Int  IntPct  Long  Sack  SackYds  Rate
            (0, "games_played"),
            (1, "pass_attempts"),
            (2, "completions"),
            (3, "completion_pct"),
            (4, "passing_yards"),
            (5, "yards_per_attempt"),
            (6, "passing_tds"),
            (7, "pass_td_pct"),
            (8, "interceptions_thrown"),
            (9, "int_pct"),
            (10, "pass_long"),
            (11, "sacks"),
            (12, "sack_yards"),
            (13, "passer_rating"),
        ],
    },
    {
        "slug": "rushing",
        "cols": [
            # GP  Att  Yds  Avg  AvgG  Long  TD
            (0, "games_played"),
            (1, "rush_attempts"),
            (2, "rushing_yards"),
            (3, "rush_avg"),
            (4, "rush_avg_game"),
            (5, "rush_long"),
            (6, "rushing_tds"),
        ],
    },
    {
        "slug": "receiving",
        "cols": [
            # GP  Rec  Yds  Avg  AvgG  Long  TD  Tgt
            (0, "games_played"),
            (1, "receptions"),
            (2, "receiving_yards"),
            (3, "rec_avg"),
            (4, "rec_avg_game"),
            (5, "rec_long"),
            (6, "receiving_tds"),
            (7, "targets"),
        ],
    },
    {
        "slug": "defense",
        "cols": [
            # GP  Int  IntYds  Avg  Long  TD  Solo  Asst  Total  Sacks  SackYds
            (0, "games_played"),
            (1, "interceptions_made"),
            (2, "int_yards"),
            (3, "int_avg"),
            (4, "int_long"),
            (5, "int_tds"),
            (6, "solo_tackles"),
            (7, "assist_tackles"),
            (8, "tackles"),
            (9, "sacks"),
            (10, "sack_yards"),
        ],
    },
    {
        "slug": "kicking",
        "cols": [
            # GP  XPA  XPM  XPPct  FGA  FGM  FGPct  Long  Pts
            (0, "games_played"),
            (1, "xp_attempts"),
            (2, "extra_points"),
            (3, "xp_pct"),
            (4, "fg_attempts"),
            (5, "field_goals_made"),
            (6, "fg_pct"),
            (7, "fg_long"),
            (8, "kicking_points"),
        ],
    },
    {
        "slug": "scoring",
        "cols": [
            # GP  Rush_TD  Rec_TD  PR_TD  KR_TD  Fum_TD  Blk_TD  Other_TD  TotalTD  2PM  XPM  FGM  Saf  Pts
            (0, "games_played"),
            (1, "rush_tds_scoring"),
            (2, "rec_tds_scoring"),
            (3, "punt_return_tds"),
            (4, "kick_return_tds"),
            (5, "fumble_return_tds"),
            (6, "blocked_kick_tds"),
            (7, "other_tds"),
            (8, "total_tds"),
            (9, "two_point_conv"),
            (10, "xp_made_scoring"),
            (11, "fg_made_scoring"),
            (12, "safeties"),
            (13, "total_points"),
        ],
    },
    {
        "slug": "punting",
        "cols": [
            # GP  Punts  Yds  Avg  Net  TB  In20  Long  Blkd
            (0, "games_played"),
            (1, "punts"),
            (2, "punt_yards"),
            (3, "punt_avg"),
            (4, "punt_net"),
            (5, "punt_tb"),
            (6, "punts_in20"),
            (7, "punt_long"),
            (8, "punts_blocked"),
        ],
    },
    {
        "slug": "kickreturns",
        "cols": [
            # GP  Ret  Yds  Avg  Long  TD
            (0, "games_played"),
            (1, "kick_returns"),
            (2, "kr_yards"),
            (3, "kr_avg"),
            (4, "kr_long"),
            (5, "kr_tds"),
        ],
    },
    {
        "slug": "puntreturns",
        "cols": [
            # GP  Ret  Yds  Avg  Long  TD
            (0, "games_played"),
            (1, "punt_returns"),
            (2, "pr_yards"),
            (3, "pr_avg"),
            (4, "pr_long"),
            (5, "pr_tds"),
        ],
    },
]

# Team abbreviation → full name
TEAM_NAMES = {
    "ATL": "Atlanta Legends",
    "BIR": "Birmingham Iron",
    "MEM": "Memphis Express",
    "ORL": "Orlando Apollos",
    "ARI": "Arizona Hotshots",
    "SA":  "San Antonio Commanders",
    "SD":  "San Diego Fleet",
    "SL":  "Salt Lake Stallions",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_page(url: str) -> BeautifulSoup:
    """Fetch URL with simple disk cache."""
    cache_file = CACHE_DIR / (re.sub(r"[^a-z0-9]", "_", url.lower()) + ".html")
    if cache_file.exists():
        html = cache_file.read_text(encoding="utf-8")
    else:
        time.sleep(DELAY)
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text
        cache_file.write_text(html, encoding="utf-8")
    return BeautifulSoup(html, "lxml")


def clean_num(s: str):
    """Parse a stat value to float; return None if empty/dash/NA."""
    s = s.strip().replace(",", "").rstrip("t").rstrip("*")  # strip "t"=touchdown marker
    if s in ("", "—", "-", "NA", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return s  # keep as string if it's e.g. a long like "83t"


def parse_player_cell(td) -> tuple[str, str, str]:
    """
    Returns (full_name, player_url_path, team_abbr) from a .playertmcell td.
    """
    # Desktop span: d-none d-xl-inline
    desktop = td.find("span", class_=lambda c: c and "d-xl-inline" in c)
    if desktop:
        link = desktop.find("a")
        team_span = desktop.find("span", class_="statplayer-team")
        name = link.get_text(strip=True) if link else ""
        href = link["href"] if link and link.get("href") else ""
        team = team_span.get_text(strip=True) if team_span else ""
        return name, href, team
    # Fallback: any link
    link = td.find("a")
    if link:
        text = link.get_text(strip=True)
        href = link.get("href", "")
        team_span = td.find("span", class_="statplayer-team")
        team = team_span.get_text(strip=True) if team_span else ""
        return text, href, team
    return td.get_text(strip=True), "", ""


def scrape_stat_page(stat_slug: str, col_map: list) -> dict:
    """
    Scrape one stat category page.
    Returns dict keyed by (player_url_path or player_name, team) → {stat_key: value, ...}
    """
    url = f"{BASE_URL}/statistics/aaf/player-stats/{stat_slug}"
    print(f"  Fetching {url}")
    soup = get_page(url)
    table = soup.find("table")
    if not table:
        print(f"    WARNING: no table found for {stat_slug}")
        return {}

    rows = table.find_all("tr")
    results = {}
    for row in rows[1:]:  # skip header row(s)
        cells = row.find_all("td")
        if not cells:
            continue
        # Player cell is first
        name, href, team = parse_player_cell(cells[0])
        if not name:
            continue

        key = (href or name, team)
        if key not in results:
            results[key] = {
                "_name": name,
                "_team": team,
                "_url": href,
            }

        # Stat cells start at index 1
        stat_cells = cells[1:]
        for col_idx, stat_key in col_map:
            if col_idx < len(stat_cells):
                val = clean_num(stat_cells[col_idx].get_text(strip=True))
                if val is not None:
                    # Don't overwrite games_played if already set
                    if stat_key == "games_played" and stat_key in results[key]:
                        continue
                    results[key][stat_key] = val

    print(f"    → {len(results)} players")
    return results


def scrape_games() -> list:
    """
    Scrape game results from the scores pages (all 8 regular-season weeks).
    Returns list of game dicts.
    """
    games = []
    for week in range(1, 9):
        url = f"{BASE_URL}/scores/index.html?lg=AAF&yr=2019&type=reg&wk={week}"
        print(f"  Fetching scores week {week}: {url}")
        soup = get_page(url)

        # Find game blocks: each game has a score table + a box score link
        # Look for tables with exactly 2 team rows
        for table in soup.find_all("table"):
            rows = [r for r in table.find_all("tr") if r.find("td")]
            if len(rows) != 2:
                continue
            away_cells = [td.get_text(strip=True) for td in rows[0].find_all("td")]
            home_cells = [td.get_text(strip=True) for td in rows[1].find_all("td")]
            if len(away_cells) < 6 or len(home_cells) < 6:
                continue

            # Cells: Team(record) | Q1 | Q2 | Q3 | Q4 | Total
            def parse_team_row(cells):
                team_text = cells[0]  # e.g. "Atlanta (0-1)"
                m = re.match(r"^(.+?)\s*\((\d+-\d+)\)$", team_text)
                if m:
                    return m.group(1).strip(), m.group(2), cells[-1]
                return team_text, "", cells[-1]

            away_name, away_rec, away_score = parse_team_row(away_cells)
            home_name, home_rec, home_score = parse_team_row(home_cells)

            # Look for the box score link nearby
            box_link = None
            parent = table.find_parent()
            if parent:
                a = parent.find("a", href=re.compile(r"/games/boxscore/"))
                if a:
                    box_link = BASE_URL + a["href"] if a["href"].startswith("/") else a["href"]

            # Find date: look for a preceding heading with a date
            date_str = ""
            for prev in table.find_all_previous(["h2", "h3", "strong"]):
                txt = prev.get_text(strip=True)
                if re.search(r"\w+ \d+, \d{4}", txt):
                    date_str = txt
                    break

            games.append({
                "week": week,
                "date": date_str,
                "away_team": away_name,
                "away_record": away_rec,
                "away_score": clean_num(away_score),
                "home_team": home_name,
                "home_record": home_rec,
                "home_score": clean_num(home_score),
                "box_score_url": box_link,
                "sport_id": SPORT_ID,
                "season": SEASON,
                "league": "AAF",
            })

    return games


def scrape_box_score(url: str) -> dict:
    """
    Scrape player stats from a game box score page.
    Returns dict with game info and per-player stats.
    """
    print(f"    Box score: {url}")
    soup = get_page(url)
    result = {"url": url, "players": []}

    # Box score stat section heading → column mapping
    SECTION_COLS = {
        "passing":      ["pass_attempts", "completions", "passing_yards", "yards_per_attempt",
                         "passing_tds", "interceptions_thrown", "pass_long"],
        "rushing":      ["rush_attempts", "rushing_yards", "rush_avg", "rush_long", "rushing_tds"],
        "receiving":    ["receptions", "receiving_yards", "rec_avg", "rec_long", "receiving_tds", "targets"],
        "punt returns": ["punt_returns", "pr_yards", "pr_avg", "pr_fc", "pr_long", "pr_tds"],
        "punting":      ["punts", "punt_yards", "punt_avg", "punt_long", "punt_tb", "punts_in20", "punts_blocked"],
        "kicking":      ["xp_att_game", "fg_att_game", "fg_0_19", "fg_20_29", "fg_30_39", "fg_40_49", "fg_50plus"],
        "defense":      ["interceptions_made", "int_yards", "int_avg", "int_long", "int_tds",
                         "solo_tackles", "assist_tackles"],
        "fumbles":      ["fumbles_lost_game", "fumbles_forced", "fumbles_own_rec",
                         "fumbles_opp_rec", "fumble_rec_yards"],
    }

    players = {}

    def get_team_from_header(table):
        """Extract team name from table's first th text."""
        th = table.find("th")
        if not th:
            return ""
        raw = th.get_text(separator=" ", strip=True)
        # Header format: "Atlanta LegendsAtlanta" or "Orlando ApollosOrlando"
        # Find the desktop span
        desktop = th.find("span", class_=lambda c: c and "d-xl-inline" in c)
        if desktop:
            return desktop.get_text(strip=True)
        return raw.split("  ")[0].strip()

    def parse_name_cell(td):
        """Extract full player name and URL from player cell."""
        link = td.find("a")
        if link:
            # Link text may include both full and abbreviated versions concatenated
            # Use title or href to get clean name
            title = link.get("title", "")
            if title.endswith(" Stats"):
                name = title[:-6]
            else:
                # Get text from desktop span if present
                desktop = link.find("span", class_=lambda c: c and "d-xl-inline" in c)
                if desktop:
                    name = desktop.get_text(strip=True)
                else:
                    # Fall back: first link text may have concatenated names
                    # e.g. "Garrett GilbertG. Gilbert" — take the first word pair
                    raw = link.get_text(separator="|", strip=True)
                    name = raw.split("|")[0]
            href = link.get("href", "")
            return name, href
        return td.get_text(strip=True), ""

    current_section = None
    current_col_map = None

    for el in soup.find_all(["h2", "table"]):
        if el.name == "h2":
            heading_text = el.get_text(strip=True).lower()
            current_section = None
            current_col_map = None
            for section_key, cols in SECTION_COLS.items():
                if section_key in heading_text:
                    current_section = section_key
                    current_col_map = cols
                    break
            continue

        if el.name == "table" and current_col_map is not None:
            team_name = get_team_from_header(el)
            rows = el.find_all("tr")
            if not rows:
                continue

            for row in rows[1:]:  # skip header
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                name, href = parse_name_cell(cells[0])
                if not name:
                    continue

                pkey = href or name
                if pkey not in players:
                    players[pkey] = {"name": name, "url": href, "team": team_name, "stats": {}}

                stat_cells = cells[1:]
                for col_idx, stat_key in enumerate(current_col_map):
                    if col_idx < len(stat_cells):
                        raw = stat_cells[col_idx].get_text(strip=True)
                        # Handle kicking format "2/3"
                        if "/" in raw:
                            parts = raw.split("/")
                            try:
                                val = float(parts[0])
                            except ValueError:
                                val = None
                        else:
                            val = clean_num(raw)
                        if val is not None:
                            players[pkey]["stats"][stat_key] = val

    result["players"] = list(players.values())
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== AAF 2019 Scraper ===\n")

    # 1. Scrape all stat category season totals
    print("Step 1: Scraping season stat pages ...")
    all_players: dict[tuple, dict] = {}

    for page_def in STAT_PAGES:
        slug = page_def["slug"]
        col_map = page_def["cols"]
        page_data = scrape_stat_page(slug, col_map)
        for key, stats in page_data.items():
            if key not in all_players:
                all_players[key] = {
                    "name": stats["_name"],
                    "team_abbr": stats["_team"],
                    "team_name": TEAM_NAMES.get(stats["_team"], stats["_team"]),
                    "player_url": stats["_url"],
                    "sport_id": SPORT_ID,
                    "season": SEASON,
                    "league": "AAF",
                    "stats": {},
                }
            for k, v in stats.items():
                if not k.startswith("_"):
                    # games_played: take max across categories
                    if k == "games_played":
                        existing = all_players[key]["stats"].get("games_played", 0) or 0
                        all_players[key]["stats"]["games_played"] = max(existing, v or 0)
                    else:
                        all_players[key]["stats"][k] = v

    players_list = list(all_players.values())
    print(f"\nTotal unique players scraped: {len(players_list)}")

    season_out = RAW_DIR / "aaf_2019_season.json"
    season_out.write_text(json.dumps(players_list, indent=2), encoding="utf-8")
    print(f"Saved → {season_out}")

    # 2. Scrape game results
    print("\nStep 2: Scraping game scores ...")
    games = scrape_games()
    print(f"Total games found: {len(games)}")

    games_out = RAW_DIR / "aaf_2019_games.json"
    games_out.write_text(json.dumps(games, indent=2), encoding="utf-8")
    print(f"Saved → {games_out}")

    # 3. Scrape individual box scores for per-game player stats
    print("\nStep 3: Scraping box scores ...")
    box_scores = []
    seen_urls = set()
    for g in games:
        url = g.get("box_score_url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            try:
                bs = scrape_box_score(url)
                bs["week"] = g["week"]
                bs["date"] = g["date"]
                bs["away_team"] = g["away_team"]
                bs["home_team"] = g["home_team"]
                bs["away_score"] = g["away_score"]
                bs["home_score"] = g["home_score"]
                box_scores.append(bs)
            except Exception as e:
                print(f"    ERROR scraping {url}: {e}")

    box_out = RAW_DIR / "aaf_2019_boxscores.json"
    box_out.write_text(json.dumps(box_scores, indent=2), encoding="utf-8")
    print(f"Saved {len(box_scores)} box scores → {box_out}")

    print("\nDone.")


if __name__ == "__main__":
    main()

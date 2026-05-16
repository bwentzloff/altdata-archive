#!/usr/bin/env python3
"""
scrape_wnba.py — WNBA per-game box scores (2021–2025).

Source: basketball-reference.com (HTML, polite rate-limited scrape).

For each season:
  1. GET /wnba/years/{year}_games.html  → schedule (one row per game)
  2. GET /wnba/boxscores/{code}.html    → both teams' player tables

Outputs (pipeline/raw/):
  wnba_players.json  — one record per (player, season)
  wnba_stats.json    — per-game stat rows  (sparse: only non-zero values)
  wnba_games.json    — one record per game with final score / box-score URL
  wnba_state.json    — incremental progress (scraped game codes per season)

The scraper is RESUMABLE: re-running picks up where it left off using the
state file. Hit Ctrl-C or let it crash; state is checkpointed every 25 games.

Designed for human-paced scraping. Default delay 3.0s between HTTP requests;
basketball-reference rate-limits aggressively.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup, Comment

# ── paths ────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
RAW  = BASE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "wnba_players.json"
STATS_FILE   = RAW / "wnba_stats.json"
GAMES_FILE   = RAW / "wnba_games.json"
STATE_FILE   = RAW / "wnba_state.json"

# ── config ───────────────────────────────────────────────────────────────
SEASONS = [2021, 2022, 2023, 2024, 2025]
ROOT = "https://www.basketball-reference.com"
SYNTHETIC_ID_START = 2_800_000

# Polite delay between requests. basketball-reference has been observed to
# throttle bursts; 3 s is the commonly-cited safe value.
REQ_DELAY  = 3.0
RETRY_WAIT = 60.0     # on HTTP 429, wait this long before retrying
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.basketball-reference.com/wnba/",
}

# Map BR 3-letter team codes → full team display names.
# basketball-reference uses LAS for the LA Sparks; Las Vegas Aces are LVA.
WNBA_TEAMS = {
    "ATL": "Atlanta Dream",
    "CHI": "Chicago Sky",
    "CON": "Connecticut Sun",
    "DAL": "Dallas Wings",
    "GSV": "Golden State Valkyries",   # 2025 expansion
    "IND": "Indiana Fever",
    "LAS": "Los Angeles Sparks",
    "LVA": "Las Vegas Aces",
    "MIN": "Minnesota Lynx",
    "NYL": "New York Liberty",
    "PHO": "Phoenix Mercury",
    "SEA": "Seattle Storm",
    "WAS": "Washington Mystics",
}

# ── HTTP ────────────────────────────────────────────────────────────────
_last_req_time = 0.0


def _throttle():
    global _last_req_time
    dt = time.time() - _last_req_time
    if dt < REQ_DELAY:
        time.sleep(REQ_DELAY - dt)
    _last_req_time = time.time()


def fetch(url: str) -> str | None:
    """GET url with throttling + 429-aware retry. Returns text or None."""
    for attempt in range(1, MAX_RETRIES + 1):
        _throttle()
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except Exception as e:
            print(f"  ERR (attempt {attempt}) {url}: {e}")
            time.sleep(RETRY_WAIT)
            continue
        if r.status_code == 200:
            return r.text
        if r.status_code == 404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            wait = RETRY_WAIT * attempt
            print(f"  HTTP {r.status_code} on {url} — sleeping {wait:.0f}s")
            time.sleep(wait)
            continue
        print(f"  HTTP {r.status_code}: {url}")
        return None
    return None


def parse(html: str) -> BeautifulSoup:
    """Parse HTML; also stitch commented-out tables back into the tree.

    basketball-reference wraps many secondary tables in HTML comments to
    defeat naive scrapers. We pull them back into the live DOM so bs4 can
    walk them normally.
    """
    soup = BeautifulSoup(html, "html.parser")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        text = str(c)
        if "<table" in text:
            try:
                inner = BeautifulSoup(text, "html.parser")
                c.replace_with(inner)
            except Exception:
                pass
    return soup


# ── parsing ──────────────────────────────────────────────────────────────

def parse_season_schedule(html: str, season: int) -> list[dict]:
    """Return [{game_code, date, away_team_code, home_team_code, away_score, home_score, playoff_flag}]."""
    soup = parse(html)
    games: list[dict] = []
    for tr in soup.select("tr"):
        if "thead" in (tr.get("class") or []):
            continue
        date_cell = tr.find("th", {"data-stat": "date_game"})
        away_cell = tr.find("td", {"data-stat": "visitor_team_name"})
        home_cell = tr.find("td", {"data-stat": "home_team_name"})
        if not (date_cell and away_cell and home_cell):
            continue
        # csk attribute on date <th> is the box-score code (e.g. 202505160DAL)
        code = (date_cell.get("csk") or "").strip()
        if not code:
            # fall back to the box-score link
            bs_cell = tr.find("td", {"data-stat": "box_score_text"})
            bs_link = bs_cell.find("a") if bs_cell else None
            if bs_link and "/wnba/boxscores/" in (bs_link.get("href") or ""):
                code = bs_link["href"].rsplit("/", 1)[-1].replace(".html", "")
        if not code:
            continue
        date_text = date_cell.get_text(strip=True)
        def _code(cell):
            a = cell.find("a")
            if not a: return ""
            m = re.search(r"/wnba/teams/([A-Z]+)/", a.get("href") or "")
            return m.group(1) if m else ""
        away_code = _code(away_cell)
        home_code = _code(home_cell)
        def _score(cell_name):
            c = tr.find("td", {"data-stat": cell_name})
            txt = c.get_text(strip=True) if c else ""
            return int(txt) if txt.isdigit() else None
        away_score = _score("visitor_pts")
        home_score = _score("home_pts")
        games.append({
            "game_code":      code,
            "date":           date_text,
            "season":         season,
            "away_team_code": away_code,
            "home_team_code": home_code,
            "away_score":     away_score,
            "home_score":     home_score,
        })
    return games


# Map basketball-reference per-game box score data-stat keys → our stat names
BOXSCORE_STATS = {
    "mp":        "minutes",
    "fg":        "fgm",
    "fga":       "fga",
    "fg_pct":    "fg_pct",
    "fg3":       "fg3m",
    "fg3a":      "fg3a",
    "fg3_pct":   "fg3_pct",
    "ft":        "ftm",
    "fta":       "fta",
    "ft_pct":    "ft_pct",
    "orb":       "off_rebounds",
    "drb":       "def_rebounds",
    "trb":       "rebounds",
    "ast":       "assists",
    "stl":       "steals",
    "blk":       "blocks",
    "tov":       "turnovers",
    "pf":        "fouls",
    "pts":       "points",
    "plus_minus":"plus_minus",
}


def _safe_float(v) -> float | None:
    if v is None or str(v).strip() in ("", "-", "—", "Did Not Play",
                                       "Did Not Dress", "Not With Team",
                                       "Player Suspended", "Inactive"):
        return None
    try:
        s = str(v).strip().replace("%", "")
        if ":" in s:                      # minutes "MM:SS"
            mm, ss = s.split(":", 1)
            return float(mm) + float(ss) / 60.0
        f = float(s)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def parse_box_score(html: str, game_code: str, away_code: str,
                    home_code: str) -> tuple[list[dict], list[dict]]:
    """Return (player_rows, raw_team_rows).

    player_rows: [{name, slug, team_code, dnp, stats:{name:val}}]
    raw_team_rows: [] (placeholder — totals not currently used).
    """
    soup = parse(html)
    rows: list[dict] = []
    for tcode in (away_code, home_code):
        table = soup.find("table", {"id": f"box-{tcode}-game-basic"})
        if not table:
            # try lowercased fallback
            table = soup.find("table", {"id": f"box-{tcode.lower()}-game-basic"})
        if not table:
            continue
        tbody = table.find("tbody")
        if not tbody:
            continue
        for tr in tbody.find_all("tr"):
            if "thead" in (tr.get("class") or []):
                continue
            name_cell = tr.find("th", {"data-stat": "player"})
            if not name_cell:
                continue
            a = name_cell.find("a")
            if not a:
                continue
            name = a.get_text(strip=True)
            slug = (a.get("href") or "").rsplit("/", 1)[-1].replace(".html", "")
            # DNP rows have a single <td class="iz" ...> spanning all cols
            reason_cell = tr.find("td", {"data-stat": "reason"})
            dnp = reason_cell is not None
            stats: dict[str, float] = {}
            if not dnp:
                for br_key, our_key in BOXSCORE_STATS.items():
                    cell = tr.find("td", {"data-stat": br_key})
                    if not cell:
                        continue
                    v = _safe_float(cell.get_text(strip=True))
                    if v is None:
                        continue
                    stats[our_key] = v
            rows.append({
                "name":      name,
                "slug":      slug,
                "team_code": tcode,
                "dnp":       dnp,
                "stats":     stats,
            })
    return rows, []


# ── orchestrate ──────────────────────────────────────────────────────────

def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def save_outputs(players: dict, stats: list, games: list) -> None:
    # players dict (keyed by (slug, season)) → list
    out_players = list(players.values())
    PLAYERS_FILE.write_text(json.dumps(out_players, indent=2))
    STATS_FILE.write_text(json.dumps(stats, indent=2))
    GAMES_FILE.write_text(json.dumps(games, indent=2))


def build_player_record(pid: int, name: str, slug: str, season: int,
                        team_code: str) -> dict:
    parts = name.split()
    return {
        "id":             pid,
        "full_name":      name,
        "short_name":     name,
        "first_name":     parts[0] if parts else name,
        "last_name":      " ".join(parts[1:]) if len(parts) > 1 else "",
        "sport_id":       None,
        "league":         "WNBA",
        "team":           WNBA_TEAMS.get(team_code, team_code),
        "position":       "",
        "_wnba":          True,
        "_wnba_slug":     slug,
        "_year":          season,
        "_norm_name":     name.lower(),
        "sportradar_id":  None,
        "college":        None,
        "jersey":         None,
        "height":         None,
        "weight":         None,
    }


def build_game_record(g: dict) -> dict:
    """Convert schedule row → games.json record."""
    away_full = WNBA_TEAMS.get(g["away_team_code"], g["away_team_code"])
    home_full = WNBA_TEAMS.get(g["home_team_code"], g["home_team_code"])
    return {
        "game_id":        f"WNBA_{g['game_code']}",
        "week":           1,
        "date":           g["date"],
        "away_team":      away_full,
        "home_team":      home_full,
        "team_away":      g["away_team_code"],
        "team_home":      g["home_team_code"],
        "score_away":     g["away_score"],
        "score_home":     g["home_score"],
        "away_score":     g["away_score"],
        "home_score":     g["home_score"],
        "sport_id":       None,
        "season":         g["season"],
        "league":         "WNBA",
        "box_score_url":  f"{ROOT}/wnba/boxscores/{g['game_code']}.html",
    }


def main(seasons: Iterable[int] = SEASONS) -> None:
    state = load_json(STATE_FILE, {"scraped_games": [], "next_id": SYNTHETIC_ID_START})
    scraped_games: set[str] = set(state.get("scraped_games", []))
    next_id: int = state.get("next_id", SYNTHETIC_ID_START)

    # Load existing outputs to support resume
    existing_players_list = load_json(PLAYERS_FILE, [])
    existing_stats = load_json(STATS_FILE, [])
    existing_games = load_json(GAMES_FILE, [])

    # Re-key players by (slug, season) for in-memory updates
    players: dict[tuple[str, int], dict] = {}
    for p in existing_players_list:
        key = (p.get("_wnba_slug") or p["full_name"].lower(), p.get("_year"))
        players[key] = p
    stats: list[dict] = list(existing_stats)
    games_out: list[dict] = list(existing_games)
    games_already = {g["game_id"] for g in games_out}

    def lookup_or_create_player(name: str, slug: str, season: int,
                                team_code: str) -> int:
        nonlocal next_id
        key = (slug, season)
        if key in players:
            # Update team if a later game shows a different team (trade)
            return players[key]["id"]
        pid = next_id
        next_id += 1
        players[key] = build_player_record(pid, name, slug, season, team_code)
        return pid

    total_new_games = 0
    for season in seasons:
        print(f"\n=== Season {season} ===")
        sched_html = fetch(f"{ROOT}/wnba/years/{season}_games.html")
        if not sched_html:
            print(f"  ! schedule fetch failed for {season}; skipping")
            continue
        season_games = parse_season_schedule(sched_html, season)
        # only games that have already been played
        season_games = [g for g in season_games
                        if g["away_score"] is not None and g["home_score"] is not None]
        print(f"  schedule: {len(season_games)} completed games")

        for i, g in enumerate(season_games, 1):
            game_id = f"WNBA_{g['game_code']}"
            if g["game_code"] in scraped_games:
                continue
            box_url = f"{ROOT}/wnba/boxscores/{g['game_code']}.html"
            html = fetch(box_url)
            if not html:
                print(f"  [{i}/{len(season_games)}] {g['game_code']} — fetch failed")
                continue
            player_rows, _ = parse_box_score(html, g["game_code"],
                                              g["away_team_code"],
                                              g["home_team_code"])
            for row in player_rows:
                pid = lookup_or_create_player(row["name"], row["slug"],
                                              season, row["team_code"])
                for stat_name, value in row["stats"].items():
                    if value == 0.0:
                        continue
                    stats.append({
                        "player_id": pid,
                        "week":      1,
                        "stat":      stat_name,
                        "value":     value,
                        "game_id":   game_id,
                        "_year":     season,
                    })

            if game_id not in games_already:
                games_out.append(build_game_record(g))
                games_already.add(game_id)
            scraped_games.add(g["game_code"])
            total_new_games += 1
            if total_new_games % 25 == 0:
                state["scraped_games"] = sorted(scraped_games)
                state["next_id"] = next_id
                save_state(state)
                save_outputs(players, stats, games_out)
                print(f"  [{i}/{len(season_games)}] checkpoint — "
                      f"{total_new_games} new games, {len(players)} players, "
                      f"{len(stats):,} stat rows")

    # Final flush
    state["scraped_games"] = sorted(scraped_games)
    state["next_id"]       = next_id
    state["last_run"]      = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_state(state)
    save_outputs(players, stats, games_out)

    print()
    print(f"DONE. {total_new_games} new games this run; "
          f"{len(scraped_games)} games total in state.")
    print(f"  players:     {len(players):,}")
    print(f"  stat rows:   {len(stats):,}")
    print(f"  game files:  {len(games_out):,}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args:
        try:
            chosen = [int(a) for a in args]
        except ValueError:
            print("usage: scrape_wnba.py [season ...]")
            sys.exit(2)
        main(chosen)
    else:
        main()

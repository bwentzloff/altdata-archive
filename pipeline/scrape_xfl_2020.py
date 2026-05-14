#!/usr/bin/env python3
"""
scrape_xfl_2020.py
Scrapes the shortened 2020 XFL season from footballdb.com.

Outputs (pipeline/raw/):
  - xfl_2020_games.json         game metadata + scoring summary events
  - xfl_2020_boxscores.json     parsed per-game player stat tables
  - xfl_2020_scoring_plays.json flattened scoring-summary events
  - xfl_2020_players.json       synthetic player records for merge/build pipeline
  - xfl_2020_stats.json         stat rows ready for build_data aggregation

Usage:
  /Users/brian/Projects/altdata-archive/.venv/bin/python pipeline/scrape_xfl_2020.py
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

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

RAW_DIR = Path(__file__).parent / "raw"
RAW_DIR.mkdir(exist_ok=True)

CACHE_DIR = RAW_DIR / "_fdb_cache"
CACHE_DIR.mkdir(exist_ok=True)

LEAGUE = "XFL"
SEASON = 2020
SPORT_ID = 7

TEAM_ABBR = {
    "dc defenders": "DC",
    "defenders": "DC",
    "st. louis": "STL",
    "st louis": "STL",
    "st. louis battlehawks": "STL",
    "battlehawks": "STL",
    "new york": "NY",
    "new york guardians": "NY",
    "guardians": "NY",
    "tampa bay": "TB",
    "tampa bay vipers": "TB",
    "vipers": "TB",
    "houston": "HOU",
    "houston roughnecks": "HOU",
    "roughnecks": "HOU",
    "dallas": "DAL",
    "dallas renegades": "DAL",
    "renegades": "DAL",
    "los angeles": "LA",
    "los angeles wildcats": "LA",
    "wildcats": "LA",
    "seattle": "SEA",
    "seattle dragons": "SEA",
    "dragons": "SEA",
}

SECTION_COLS = {
    "passing": [
        "pass_attempts",
        "completions",
        "passing_yards",
        "yards_per_attempt",
        "passing_tds",
        "interceptions_thrown",
        "pass_long",
    ],
    "rushing": ["rush_attempts", "rushing_yards", "rush_avg", "rush_long", "rushing_tds"],
    "receiving": ["receptions", "receiving_yards", "rec_avg", "rec_long", "receiving_tds", "targets"],
    "punt returns": ["punt_returns", "pr_yards", "pr_avg", "pr_fc", "pr_long", "pr_tds"],
    "kickoff returns": ["kick_returns", "kr_yards", "kr_avg", "kr_long", "kr_tds"],
    "punting": ["punts", "punt_yards", "punt_avg", "punt_long", "punt_tb", "punts_in20", "punts_blocked"],
    "kicking": ["xp_att_game", "fg_att_game", "fg_0_19", "fg_20_29", "fg_30_39", "fg_40_49", "fg_50plus"],
    "defense": [
        "interceptions_made",
        "int_yards",
        "int_avg",
        "int_long",
        "int_tds",
        "solo_tackles",
        "assist_tackles",
        "tackles",
        "sacks",
    ],
    "fumbles": ["fumbles_lost_game", "fumbles_forced", "fumbles_own_rec", "fumbles_opp_rec", "fumble_rec_yards"],
}

SKIP_STATS = {
    "completion_pct",
    "yards_per_attempt",
    "pass_td_pct",
    "int_pct",
    "passer_rating",
    "rush_avg",
    "rush_avg_game",
    "rush_long",
    "rec_avg",
    "rec_avg_game",
    "rec_long",
    "pass_long",
    "games_played",
    "pr_avg",
    "kr_avg",
    "int_avg",
}

RENAME_STATS = {"interceptions_thrown": "interceptions_lost"}


def clean_num(text: str) -> float | None:
    s = text.strip().replace(",", "")
    if not s or s in {"-", "--", "—", "N/A", "NA"}:
        return None
    if "/" in s:
        s = s.split("/", 1)[0]
    s = s.rstrip("t").rstrip("*")
    try:
        return float(s)
    except ValueError:
        return None


def get_page(url: str, delay: float) -> BeautifulSoup:
    cache_file = CACHE_DIR / (re.sub(r"[^a-z0-9]", "_", url.lower()) + ".html")
    if cache_file.exists():
        html = cache_file.read_text(encoding="utf-8")
    else:
        time.sleep(delay)
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        html = resp.text
        cache_file.write_text(html, encoding="utf-8")
    return BeautifulSoup(html, "lxml")


def norm_team_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def team_abbr(name: str) -> str:
    n = norm_team_name(name)
    if n in TEAM_ABBR:
        return TEAM_ABBR[n]
    # Fallback: strip record and use first token(s)
    n = re.sub(r"\([^)]*\)", "", n).strip()
    if n in TEAM_ABBR:
        return TEAM_ABBR[n]
    token = re.sub(r"[^a-z]", "", (n.split(" ")[0] if n else ""))
    return (token[:3] or "UNK").upper()


def parse_team_row(cells: list[str]) -> tuple[str, str, int | None]:
    # Team(record) | Q1 | Q2 | Q3 | Q4 | Total
    team_text = cells[0]
    m = re.match(r"^(.+?)\s*\((\d+-\d+)\)$", team_text)
    if m:
        name = m.group(1).strip()
        rec = m.group(2)
    else:
        name = team_text.strip()
        rec = ""
    score = clean_num(cells[-1])
    return name, rec, int(score) if score is not None else None


def extract_yyyymmdd_from_box_url(url: str) -> tuple[int, int, int] | None:
    m = re.search(r"-(\d{4})(\d{2})(\d{2})\d{2}$", url)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def parse_scores_week(week: int, delay: float) -> list[dict[str, Any]]:
    url = f"{BASE_URL}/scores/index.html?lg={LEAGUE}&yr={SEASON}&type=reg&wk={week}"
    soup = get_page(url, delay)
    games: list[dict[str, Any]] = []

    for table in soup.find_all("table"):
        rows = [r for r in table.find_all("tr") if r.find("td")]
        if len(rows) != 2:
            continue

        away_cells = [td.get_text(strip=True) for td in rows[0].find_all("td")]
        home_cells = [td.get_text(strip=True) for td in rows[1].find_all("td")]
        if len(away_cells) < 6 or len(home_cells) < 6:
            continue

        away_name, away_rec, away_score = parse_team_row(away_cells)
        home_name, home_rec, home_score = parse_team_row(home_cells)

        box_link = None
        parent = table.find_parent()
        if parent:
            a = parent.find("a", href=re.compile(r"/games/boxscore/"))
            if a and a.get("href"):
                href = a["href"]
                box_link = BASE_URL + href if href.startswith("/") else href

        # Find nearest date heading before this table.
        date_str = ""
        for prev in table.find_all_previous(["h2", "h3", "strong"]):
            txt = prev.get_text(" ", strip=True)
            if re.search(r"\b\w+,\s+\w+\s+\d{1,2},\s+\d{4}\b", txt):
                date_str = txt
                break

        month = day = None
        parsed_date = extract_yyyymmdd_from_box_url(box_link or "")
        if parsed_date:
            _, month, day = parsed_date

        if month is None or day is None:
            dm = re.search(r"\b\w+,\s+(\w+)\s+(\d{1,2}),\s+(\d{4})\b", date_str)
            if dm:
                month_map = {
                    "january": 1,
                    "february": 2,
                    "march": 3,
                    "april": 4,
                    "may": 5,
                    "june": 6,
                    "july": 7,
                    "august": 8,
                    "september": 9,
                    "october": 10,
                    "november": 11,
                    "december": 12,
                }
                month = month_map.get(dm.group(1).lower(), 1)
                day = int(dm.group(2))
            else:
                month, day = 1, 1

        away_ab = team_abbr(away_name)
        home_ab = team_abbr(home_name)
        game_id = f"FOOTBALL_XFL_{SEASON}_{month}_{day}_{away_ab}@{home_ab}"

        games.append(
            {
                "game_id": game_id,
                "week": week,
                "date": date_str,
                "away_team": away_name,
                "away_record": away_rec,
                "away_score": away_score,
                "home_team": home_name,
                "home_record": home_rec,
                "home_score": home_score,
                "team_away": away_ab,
                "team_home": home_ab,
                "score_away": away_score,
                "score_home": home_score,
                "sport_id": SPORT_ID,
                "season": SEASON,
                "league": LEAGUE,
                "box_score_url": box_link,
            }
        )

    return games


def parse_scoring_summary(soup: BeautifulSoup) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    quarter = None

    for h2 in soup.find_all("h2"):
        if "scoring summary" not in h2.get_text(" ", strip=True).lower():
            continue

        table = h2.find_next("table")
        if not table:
            return events

        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
            if not cells:
                continue

            # Quarter row pattern: ["1st Quarter", "SEA", "DC"]
            if len(cells) >= 1 and "quarter" in cells[0].lower():
                quarter = cells[0]
                continue

            # Typical event row: Team | time | description | away | home
            if len(cells) >= 5:
                away_score = clean_num(cells[-2])
                home_score = clean_num(cells[-1])
                events.append(
                    {
                        "quarter": quarter,
                        "team": cells[0],
                        "clock": cells[1],
                        "description": cells[2],
                        "away_score": int(away_score) if away_score is not None else None,
                        "home_score": int(home_score) if home_score is not None else None,
                    }
                )

        return events

    return events


def parse_name_cell(td) -> tuple[str, str]:
    link = td.find("a")
    if link:
        title = (link.get("title") or "").strip()
        if title.endswith(" Stats"):
            name = title[:-6].strip()
        else:
            desktop = link.find("span", class_=lambda c: c and "d-xl-inline" in c)
            name = desktop.get_text(strip=True) if desktop else link.get_text(" ", strip=True)
        return name, link.get("href", "")
    return td.get_text(" ", strip=True), ""


def parse_box_score(box_url: str, delay: float) -> dict[str, Any]:
    soup = get_page(box_url, delay)
    players: dict[str, dict[str, Any]] = {}

    current_cols = None
    for el in soup.find_all(["h2", "table"]):
        if el.name == "h2":
            heading = el.get_text(" ", strip=True).lower()
            current_cols = None
            for key, cols in SECTION_COLS.items():
                if key in heading:
                    current_cols = cols
                    break
            continue

        if el.name != "table" or current_cols is None:
            continue

        rows = el.find_all("tr")
        if len(rows) <= 1:
            continue

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            name, href = parse_name_cell(cells[0])
            if not name:
                continue

            pkey = href or name
            if pkey not in players:
                players[pkey] = {"name": name, "url": href, "stats": {}}

            stat_cells = cells[1:]
            for col_idx, stat_key in enumerate(current_cols):
                if col_idx >= len(stat_cells):
                    continue
                val = clean_num(stat_cells[col_idx].get_text(strip=True))
                if val is not None:
                    players[pkey]["stats"][stat_key] = val

    return {
        "url": box_url,
        "players": list(players.values()),
        "scoring_summary": parse_scoring_summary(soup),
    }


def infer_position(stats: dict[str, float]) -> str:
    py = float(stats.get("passing_yards", 0) or 0)
    ry = float(stats.get("rushing_yards", 0) or 0)
    rec = float(stats.get("receiving_yards", 0) or 0)
    tackles = float(stats.get("tackles", 0) or 0)
    sacks = float(stats.get("sacks", 0) or 0)
    punts = float(stats.get("punts", 0) or 0)
    fga = float(stats.get("fg_att_game", 0) or 0)

    if py > max(ry, rec, 0):
        return "QB"
    if rec > max(ry, 0):
        return "WR"
    if ry > 0:
        return "RB"
    if punts > 0:
        return "P"
    if fga > 0:
        return "K"
    if tackles > 0 or sacks > 0:
        return "DB"
    return ""


def synth_player_record(pid: int, name: str, team: str, pos: str, url: str) -> dict[str, Any]:
    parts = name.split()
    return {
        "id": pid,
        "full_name": name,
        "short_name": name,
        "first_name": parts[0] if parts else "",
        "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
        "position": pos,
        "team": team,
        "sport_id": SPORT_ID,
        "league": LEAGUE,
        "_xfl_url": url,
        "sportradar_id": None,
        "college": None,
        "jersey": None,
        "height": None,
        "weight": None,
        "college_stats": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape XFL 2020 game/player stats from footballdb.com")
    parser.add_argument("--delay", type=float, default=1.2, help="seconds between uncached requests")
    parser.add_argument("--max-week", type=int, default=10, help="max regular-season week to probe")
    args = parser.parse_args()

    print("=== XFL 2020 Scraper ===")

    all_games: list[dict[str, Any]] = []
    for week in range(1, args.max_week + 1):
        week_games = parse_scores_week(week, args.delay)
        if week_games:
            print(f"Week {week}: {len(week_games)} games")
            all_games.extend(week_games)
        else:
            # The 2020 season ended early; missing weeks are expected.
            print(f"Week {week}: no games")

    # Dedup by game_id in case pages repeat blocks.
    dedup_games = {g["game_id"]: g for g in all_games}
    games = [dedup_games[k] for k in sorted(dedup_games.keys())]

    boxscores: list[dict[str, Any]] = []
    scoring_events: list[dict[str, Any]] = []

    for i, g in enumerate(games, 1):
        url = g.get("box_score_url")
        if not url:
            continue
        try:
            bs = parse_box_score(url, args.delay)
            bs.update(
                {
                    "game_id": g["game_id"],
                    "week": g["week"],
                    "date": g["date"],
                    "away_team": g["away_team"],
                    "home_team": g["home_team"],
                    "away_score": g["away_score"],
                    "home_score": g["home_score"],
                }
            )
            boxscores.append(bs)

            for ev in bs.get("scoring_summary", []):
                scoring_events.append({"game_id": g["game_id"], **ev})

            # Also attach scoring summary onto the game record for downstream use.
            g["scoring_summary"] = bs.get("scoring_summary", [])
            print(f"  [{i}/{len(games)}] parsed boxscore")
        except Exception as exc:
            print(f"  [{i}/{len(games)}] failed boxscore: {exc}")

    # Build synthetic players and stat rows from box score player sections.
    player_key_to_id: dict[str, int] = {}
    player_accum_stats: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    player_team: dict[str, str] = {}
    player_name: dict[str, str] = {}
    player_url: dict[str, str] = {}

    base_id = 170000
    stats_rows: list[dict[str, Any]] = []

    for bs in boxscores:
        game_id = bs["game_id"]
        week = bs.get("week")
        away_ab = team_abbr(bs.get("away_team", ""))
        home_ab = team_abbr(bs.get("home_team", ""))

        # Team inference for player rows: if unknown, leave blank.
        game_team_guess = {away_ab, home_ab}

        for p in bs.get("players", []):
            name = (p.get("name") or "").strip()
            url = p.get("url", "")
            if not name:
                continue

            pkey = url or name.lower()
            if pkey not in player_key_to_id:
                player_key_to_id[pkey] = base_id + len(player_key_to_id)

            pid = player_key_to_id[pkey]
            player_name[pkey] = name
            player_url[pkey] = url

            # Boxscore rows on footballDB do not always carry explicit team in each table.
            if pkey not in player_team:
                player_team[pkey] = ""

            stats = p.get("stats", {})
            for stat, val in stats.items():
                stat = RENAME_STATS.get(stat, stat)
                if stat in SKIP_STATS:
                    continue
                fval = float(val or 0)
                if fval == 0:
                    continue

                stats_rows.append(
                    {
                        "player_id": pid,
                        "week": week,
                        "stat": stat,
                        "value": fval,
                        "game_id": game_id,
                        "_league": LEAGUE,
                        "_year": SEASON,
                    }
                )
                player_accum_stats[pkey][stat] += fval

            # If team is unknown, infer from game participation by first seen side.
            if not player_team[pkey]:
                # Conservative default: no assignment when ambiguous.
                if len(game_team_guess) == 2:
                    # Keep blank to avoid wrong team assignment from duplicated names.
                    player_team[pkey] = ""

    players_out: list[dict[str, Any]] = []
    for pkey, pid in sorted(player_key_to_id.items(), key=lambda kv: kv[1]):
        name = player_name.get(pkey, "")
        pos = infer_position(player_accum_stats.get(pkey, {}))
        players_out.append(
            synth_player_record(
                pid=pid,
                name=name,
                team=player_team.get(pkey, ""),
                pos=pos,
                url=player_url.get(pkey, ""),
            )
        )

    # Write outputs
    (RAW_DIR / "xfl_2020_games.json").write_text(json.dumps(games, indent=2), encoding="utf-8")
    (RAW_DIR / "xfl_2020_boxscores.json").write_text(json.dumps(boxscores, indent=2), encoding="utf-8")
    (RAW_DIR / "xfl_2020_scoring_plays.json").write_text(json.dumps(scoring_events, indent=2), encoding="utf-8")
    (RAW_DIR / "xfl_2020_players.json").write_text(json.dumps(players_out, indent=2), encoding="utf-8")
    (RAW_DIR / "xfl_2020_stats.json").write_text(json.dumps(stats_rows, indent=2), encoding="utf-8")

    print("\nDone.")
    print(f"Games: {len(games)}")
    print(f"Boxscores: {len(boxscores)}")
    print(f"Scoring events: {len(scoring_events)}")
    print(f"Players: {len(players_out)}")
    print(f"Stat rows: {len(stats_rows)}")


if __name__ == "__main__":
    main()

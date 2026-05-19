#!/usr/bin/env python3
"""Scrape IFL (Indoor Football League) data from the official goifl.com site.

Outputs:
  - pipeline/raw/ifl_official_players.json
  - pipeline/raw/ifl_official_stats.json
  - pipeline/raw/ifl_official_games.json
  - pipeline/raw/ifl_official_raw.json

Notes:
  - goifl.com is fronted by AWS CloudFront with a WAF challenge.
    A Googlebot User-Agent bypasses the challenge (robots.txt explicitly
    allows Googlebot).
  - The boxscore URL on the schedule page returns an SPA shell; the
    printable boxscore HTML lives at the same URL with
    ``?dec=printer-decorator``.
  - Player IDs are synthetic and stable (hashed from "team::name").
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).parent.parent
RAW_DIR = REPO_ROOT / "pipeline" / "raw"
CACHE_DIR = REPO_ROOT / ".cache" / "ifl_official"
PLAYERS_FILE = RAW_DIR / "ifl_official_players.json"
STATS_FILE = RAW_DIR / "ifl_official_stats.json"
GAMES_FILE = RAW_DIR / "ifl_official_games.json"
PBP_FILE = RAW_DIR / "ifl_official_pbp.json"
RAW_FILE = RAW_DIR / "ifl_official_raw.json"

BASE = "https://goifl.com"
LEAGUE_NAME = "IFL"
SYNTHETIC_ID_START = 1_200_000

# Googlebot UA bypasses the CloudFront WAF challenge on goifl.com.
HEADERS = {
    "User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = 3.0  # robots.txt asks Googlebot for Crawl-delay: 10; 3s + backoff stays polite.
MAX_RETRIES = 4
RATE_LIMIT_BACKOFF = 30.0  # seconds to sleep when 429/459 seen


# ---------------------------------------------------------------------------
# Header → canonical stat-key map
# ---------------------------------------------------------------------------

# Per-category mapping from a normalized header cell to a canonical stat key.
# Headers like "C-A" or "Sacks-Yds" expand to two stats (handled inline below).
HEADER_MAP: dict[str, dict[str, str]] = {
    "passing": {
        "yds": "passing_yards",
        "lg": "pass_long",
        "td": "passing_tds",
        "int": "interceptions",
    },
    "rushing": {
        "att": "rushing_attempts",
        "yds": "rushing_yards",
        "avg": "rush_avg",
        "lg": "rush_long",
        "td": "rushing_tds",
    },
    "receiving": {
        "no": "receptions",
        "yds": "receiving_yards",
        "avg": "rec_avg",
        "lg": "rec_long",
        "td": "receiving_tds",
    },
    "kicking": {
        "lg": "field_goal_long",
        "pts": "kicking_points",
    },
    "kickoffs": {
        "no": "kickoffs",
        "yds": "kickoff_yards",
        "avg": "kickoff_avg",
        "tb": "kickoff_touchbacks",
        "ob": "kickoff_out_of_bounds",
    },
    "kickoff_returns": {
        "no": "kick_returns",
        "yds": "kr_yards",
        "avg": "kr_avg",
        "lg": "kr_long",
        "td": "kr_tds",
    },
    "punt_returns": {
        "no": "punt_returns",
        "yds": "pr_yards",
        "avg": "pr_avg",
        "lg": "pr_long",
        "td": "pr_tds",
    },
    "interception_returns": {
        "no": "def_interceptions",
        "yds": "int_yards",
        "avg": "int_avg",
        "lg": "int_long",
        "td": "defensive_tds",
    },
    "fumbles": {
        "no": "fumbles",
        "lost": "fumbles_lost",
    },
}

DEFENSIVE_HEADER_MAP = {
    "solo": "solo_tackles",
    "ast": "assisted_tackles",
    "total": "tackles",
    "ff": "forced_fumbles",
    "brup": "pass_defended",
    "blks": "blocks",
    "qbh": "qb_hits",
}


def _normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def _to_float(s: str) -> float | None:
    if s is None:
        return None
    s = s.strip()
    if not s or s in {"-", "--", "N/A", "n/a"}:
        return None
    s = s.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _split_pair(s: str) -> tuple[float | None, float | None]:
    """Split values like "14-24", "1 - 6", "2/2" into a numeric pair."""
    if not s:
        return None, None
    parts = re.split(r"\s*[-/]\s*", s.strip(), maxsplit=1)
    if len(parts) != 2:
        return None, None
    return _to_float(parts[0]), _to_float(parts[1])


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


class Client:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get(self, url: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(RATE_LIMIT_BACKOFF)
                continue

            if r.status_code in (429, 459, 503):
                wait = RATE_LIMIT_BACKOFF * (attempt + 1)
                print(f"    rate-limited ({r.status_code}), sleeping {wait:.0f}s")
                time.sleep(wait)
                continue

            r.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return r.text

        if last_exc is not None:
            raise last_exc
        raise requests.HTTPError(f"giving up after {MAX_RETRIES} attempts: {url}")


# ---------------------------------------------------------------------------
# Schedule parsing
# ---------------------------------------------------------------------------


def _date_from_boxscore_path(path: str) -> str:
    m = re.search(r"/(\d{4})(\d{2})(\d{2})_", path)
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _parse_week(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"week\s+(\d+)", text, re.I)
    if m:
        return int(m.group(1))
    word_map = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16,
    }
    m = re.search(r"week\s+(\w+)", text, re.I)
    if m and m.group(1).lower() in word_map:
        return word_map[m.group(1).lower()]
    return None


def _team_token(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", name or "UNK").upper() or "UNK"


def build_game_id(year: int, month: int, day: int, away: str, home: str, source_key: str) -> str:
    away_tok = _team_token(away)
    home_tok = _team_token(home)
    key_tok = re.sub(r"[^0-9A-Za-z]", "", source_key or "").upper() or "0"
    return f"FOOTBALL_IFL_{year}_{month}_{day}_T{away_tok}G{key_tok}@T{home_tok}"


def parse_schedule(html_text: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_text, "html.parser")
    games: list[dict[str, Any]] = []

    for row in soup.select("[data-boxscore]"):
        boxscore = row.get("data-boxscore") or ""
        if not boxscore:
            continue

        away_el = row.select_one(".event-team-name.away-team .team-name")
        home_el = row.select_one(".event-team-name.home-team .team-name")
        if not (away_el and home_el):
            continue
        away_team = away_el.get_text(" ", strip=True)
        home_team = home_el.get_text(" ", strip=True)

        # Scores: the two `flex-shrink-1` siblings of the team rows.
        score_nodes = row.select(".card-body .flex-shrink-1")
        score_away = None
        score_home = None
        if len(score_nodes) >= 2:
            score_away = _to_float(score_nodes[0].get_text(" ", strip=True))
            score_home = _to_float(score_nodes[1].get_text(" ", strip=True))

        status_el = row.select_one(".status span:nth-of-type(2)")
        venue_el = row.select_one(".venue span:nth-of-type(2)")
        notes_el = row.select_one(".event-notes")

        status = status_el.get_text(" ", strip=True) if status_el else ""
        venue = venue_el.get_text(" ", strip=True) if venue_el else ""
        notes = notes_el.get_text(" ", strip=True) if notes_el else ""

        iso_date = _date_from_boxscore_path(boxscore)
        if not iso_date:
            continue
        year, month, day = (int(p) for p in iso_date.split("-"))

        m = re.search(r"/(\d{8}_[a-z0-9]+)\.xml", boxscore)
        source_key = m.group(1) if m else boxscore.rsplit("/", 1)[-1]

        game_id = build_game_id(year, month, day, away_team, home_team, source_key)

        games.append({
            "game_id": game_id,
            "league": LEAGUE_NAME,
            "season_year": year,
            "source_game_key": source_key,
            "boxscore_path": boxscore,
            "team_away": away_team,
            "team_home": home_team,
            "score_away": score_away,
            "score_home": score_home,
            "start_time": iso_date,
            "status": status,
            "venue": venue,
            "week": _parse_week(notes),
            "week_notes": notes,
            "sport_id": None,
            "channel": "",
        })

    return games


# ---------------------------------------------------------------------------
# Boxscore parsing
# ---------------------------------------------------------------------------


def _emit(out: list[dict[str, Any]], pid: int, stat: str, value: float | None,
          game_id: str, year: int, week: int | None) -> None:
    if value is None:
        return
    out.append({
        "player_id": pid,
        "week": week,
        "stat": stat,
        "value": float(value),
        "game_id": game_id,
        "league": LEAGUE_NAME,
        "_year": year,
    })


def _player_id(team: str, name: str, players_by_key: dict, players_by_id: dict,
               next_id_box: list[int]) -> int:
    name = (name or "").strip()
    key = (team or "", name)
    pid = players_by_key.get(key)
    if pid is None:
        pid = next_id_box[0]
        next_id_box[0] += 1
        players_by_key[key] = pid
        parts = name.split()
        players_by_id[pid] = {
            "id": pid,
            "full_name": name,
            "short_name": name,
            "first_name": parts[0] if parts else "",
            "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
            "sport_id": None,
            "league": LEAGUE_NAME,
            "team": team,
            "position": "",
            "_ifl_official": True,
            "sportradar_id": None,
            "college": None,
            "jersey": None,
            "height": None,
            "weight": None,
        }
    return pid


def _category_from_header(header_cells: list[str]) -> str | None:
    if not header_cells:
        return None
    first = _normalize(header_cells[0])
    aliases = {
        "passing": "passing",
        "rushing": "rushing",
        "receiving": "receiving",
        "kicking": "kicking",
        "kickoffs": "kickoffs",
        "kickoff_returns": "kickoff_returns",
        "punt_returns": "punt_returns",
        "interception_returns": "interception_returns",
        "fumbles": "fumbles",
    }
    return aliases.get(first)


def _parse_offense_table(inner_table, team: str, game_id: str, year: int,
                         week: int | None, players_by_key: dict,
                         players_by_id: dict, next_id_box: list[int],
                         out: list[dict[str, Any]]) -> None:
    rows = inner_table.find_all("tr")
    if not rows:
        return
    headers = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    category = _category_from_header(headers)
    if not category:
        return

    for tr in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if not cells:
            continue
        name = cells[0]
        # Skip team/total rows
        if name.strip().upper() in {"TEAM", "TOTAL", "TOTALS", ""}:
            continue
        # Skip stray non-player rows (e.g. "class java.util.LinkedHashMap")
        if "linkedhashmap" in name.lower():
            continue

        pid = _player_id(team, name, players_by_key, players_by_id, next_id_box)

        for idx, header in enumerate(headers[1:], start=1):
            if idx >= len(cells):
                continue
            raw = cells[idx]
            header_norm = _normalize(header)

            # Composite headers: C-A, Sacks-Yds, etc. expand to a pair.
            if category == "passing" and header_norm == "c_a":
                comp, att = _split_pair(raw)
                _emit(out, pid, "pass_completions", comp, game_id, year, week)
                _emit(out, pid, "pass_attempts", att, game_id, year, week)
                continue
            if category == "kicking" and header_norm in {"fg", "xp"}:
                made, att = _split_pair(raw)
                if header_norm == "fg":
                    _emit(out, pid, "field_goals_made", made, game_id, year, week)
                    _emit(out, pid, "fg_attempts", att, game_id, year, week)
                else:
                    _emit(out, pid, "xp_made", made, game_id, year, week)
                    _emit(out, pid, "xp_attempts", att, game_id, year, week)
                continue

            stat_key = HEADER_MAP.get(category, {}).get(header_norm)
            if not stat_key:
                # Fall back to category-prefixed key for unknown headers.
                if not header_norm:
                    continue
                stat_key = f"{category}_{header_norm}"
            _emit(out, pid, stat_key, _to_float(raw), game_id, year, week)


def _parse_defensive_table(def_table, away_team: str, home_team: str,
                           game_id: str, year: int, week: int | None,
                           players_by_key: dict, players_by_id: dict,
                           next_id_box: list[int],
                           out: list[dict[str, Any]]) -> None:
    rows = def_table.find_all("tr")
    current_team: str | None = None
    current_headers: list[str] = []

    for tr in rows:
        ths = tr.find_all("th", recursive=False)
        tds = tr.find_all("td", recursive=False)

        if ths and not tds:
            # Could be section title ("Defensive Statistics") or a column-header
            # row whose 2nd cell carries the team name.
            cells = [c.get_text(" ", strip=True) for c in ths]
            if len(cells) >= 3:
                team_label = cells[1]
                if team_label.lower() in {away_team.lower(), home_team.lower()}:
                    current_team = team_label
                    current_headers = cells
            continue

        if not current_team or not tds:
            continue

        cells = [c.get_text(" ", strip=True) for c in tds]
        if not cells:
            continue
        # Player rows: jersey at cells[0], name at cells[1]
        if cells[0].strip().upper() in {"TOTALS", "TOTAL"}:
            continue
        name = cells[1] if len(cells) > 1 else ""
        if not name:
            continue

        pid = _player_id(current_team, name, players_by_key, players_by_id, next_id_box)

        for idx, header in enumerate(current_headers):
            if idx < 2 or idx >= len(cells):
                continue
            raw = cells[idx]
            header_norm = _normalize(header)

            if header_norm in {"sacks_yds", "tfl_yds", "fr_yds", "int_yds"}:
                a, b = _split_pair(raw)
                if header_norm == "sacks_yds":
                    _emit(out, pid, "sacks", a, game_id, year, week)
                    _emit(out, pid, "sack_yards", b, game_id, year, week)
                elif header_norm == "tfl_yds":
                    _emit(out, pid, "tackles_for_loss", a, game_id, year, week)
                    _emit(out, pid, "tfl_yards", b, game_id, year, week)
                elif header_norm == "fr_yds":
                    _emit(out, pid, "fumble_recoveries", a, game_id, year, week)
                    _emit(out, pid, "fumble_recovery_yards", b, game_id, year, week)
                elif header_norm == "int_yds":
                    _emit(out, pid, "def_interceptions", a, game_id, year, week)
                    _emit(out, pid, "int_yards", b, game_id, year, week)
                continue

            stat_key = DEFENSIVE_HEADER_MAP.get(header_norm)
            if not stat_key:
                continue
            _emit(out, pid, stat_key, _to_float(raw), game_id, year, week)


def parse_boxscore(html_text: str, game: dict[str, Any],
                   players_by_key: dict, players_by_id: dict,
                   next_id_box: list[int]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_text, "html.parser")

    game_id = game["game_id"]
    year = game["season_year"]
    week = game.get("week")
    away_team = game["team_away"]
    home_team = game["team_home"]

    out: list[dict[str, Any]] = []

    # Per-team offense grid lives inside a stats-fullbox where the outer table
    # has rows = stat categories and columns = teams (col 0 away, col 1 home).
    for fullbox in soup.select("div.stats-fullbox"):
        outer = fullbox.find("table", recursive=True)
        if not outer:
            continue
        # Skip the team-totals fullbox: its first row has the "Statistics" label
        # in the middle column rather than two team-name columns.
        rows = outer.find_all("tr", recursive=False)
        if not rows:
            tbody = outer.find("tbody", recursive=False)
            if tbody:
                rows = tbody.find_all("tr", recursive=False)
        if not rows:
            continue
        header_cells = rows[0].find_all(["td", "th"], recursive=False)
        header_text = [c.get_text(" ", strip=True) for c in header_cells]
        if len(header_text) != 2:
            continue
        if header_text[0].lower() != away_team.lower() or \
           header_text[1].lower() != home_team.lower():
            continue

        for tr in rows[1:]:
            tds = tr.find_all("td", recursive=False)
            for col_idx, td in enumerate(tds[:2]):
                inner = td.find("table")
                if not inner:
                    continue
                team = away_team if col_idx == 0 else home_team
                _parse_offense_table(inner, team, game_id, year, week,
                                     players_by_key, players_by_id,
                                     next_id_box, out)

    # Defensive table: single combined table whose <th> rows divide teams.
    for tbl in soup.find_all("table"):
        first_cell = tbl.find(["th", "td"])
        if first_cell and first_cell.get_text(strip=True) == "Defensive Statistics":
            _parse_defensive_table(tbl, away_team, home_team, game_id, year, week,
                                   players_by_key, players_by_id, next_id_box, out)
            break

    return out


# ---------------------------------------------------------------------------
# Play-by-play parsing (?view=plays&dec=printer-decorator)
# ---------------------------------------------------------------------------

_PBP_QUARTER_RE = re.compile(r"^(1st|2nd|3rd|4th|OT\d*|OT)$", re.IGNORECASE)
_PBP_DRIVE_HEADER_RE = re.compile(r"^(.+?)\s+at\s+(\d{1,2}:\d{2})$")
_PBP_DRIVE_SUMMARY_RE = re.compile(
    r"^(\d+)\s+plays?,\s+(-?\d+)\s+yards?,\s+(\d{1,2}:\d{2})\s+elapsed$"
)
_PBP_SITUATION_RE = re.compile(
    r"^(1st|2nd|3rd|4th)\s+and\s+(goal|\d+)\s+at\s+(\S+)$", re.IGNORECASE
)
_PBP_SCORE_UPDATE_RE = re.compile(r"^(.+?)\s+(\d+),\s+(.+?)\s+(\d+)$")


def _find_pbp_table(soup: BeautifulSoup):
    for tbl in soup.find_all("table"):
        first_row = tbl.find("tr")
        if first_row and "Quarters:" in first_row.get_text(" ", strip=True):
            return tbl
    return None


def parse_pbp(box_html: str, game: dict[str, Any]) -> dict[str, Any] | None:
    """Parse the printable play-by-play view into a structured dict.

    Returns None if no PBP table is present.
    """
    soup = BeautifulSoup(box_html, "html.parser")
    table = _find_pbp_table(soup)
    if table is None:
        return None

    plays: list[dict[str, Any]] = []
    drives: list[dict[str, Any]] = []
    current_quarter: str | None = None
    current_drive_index = -1
    last_score: dict[str, Any] | None = None

    for row in table.find_all("tr"):
        cells = [
            re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip()
            for c in row.find_all(["td", "th"])
        ]
        if not cells or all(not c for c in cells):
            continue

        classes = row.get("class", []) or []
        is_bold = "bold" in classes

        # Single-cell rows: quarter headers, drive headers, drive summaries, score updates.
        nonempty = [c for c in cells if c]
        if len(nonempty) == 1:
            text = nonempty[0]
            if text.startswith("Quarters:"):
                continue
            if _PBP_QUARTER_RE.match(text):
                current_quarter = text
                continue
            m = _PBP_DRIVE_SUMMARY_RE.match(text)
            if m and drives:
                drives[-1]["summary"] = {
                    "plays": int(m.group(1)),
                    "yards": int(m.group(2)),
                    "elapsed": m.group(3),
                }
                continue
            sm = _PBP_SCORE_UPDATE_RE.match(text)
            if sm and is_bold:
                last_score = {
                    "team_a": sm.group(1),
                    "score_a": int(sm.group(2)),
                    "team_b": sm.group(3),
                    "score_b": int(sm.group(4)),
                }
                if plays:
                    plays[-1]["score_after"] = dict(last_score)
                if drives:
                    drives[-1]["score_after"] = dict(last_score)
                continue
            dm = _PBP_DRIVE_HEADER_RE.match(text)
            if dm:
                current_drive_index = len(drives)
                drives.append({
                    "index": current_drive_index,
                    "quarter": current_quarter,
                    "team": dm.group(1),
                    "start_clock": dm.group(2),
                })
                continue
            # Unknown / informational single-cell row -- skip.
            continue

        # Two-cell rows: situation + play description.
        if len(cells) >= 2:
            situation, text = cells[0], cells[1]
            if not text:
                continue
            play: dict[str, Any] = {
                "index": len(plays),
                "quarter": current_quarter,
                "drive_index": current_drive_index if current_drive_index >= 0 else None,
                "text": text,
                "scoring": is_bold,
            }
            sm2 = _PBP_SITUATION_RE.match(situation) if situation else None
            if sm2:
                play["down"] = sm2.group(1)
                play["distance"] = sm2.group(2)
                play["yardline"] = sm2.group(3)
            if situation:
                play["situation"] = situation
            plays.append(play)

    return {
        "game_id": game["game_id"],
        "source_game_key": game["source_game_key"],
        "drives": drives,
        "plays": plays,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def scrape(year: int, max_games: int | None = None,
           include_pbp: bool = True) -> tuple[list, list, list, list, dict]:
    client = Client()

    schedule_url = f"{BASE}/sports/fball/{year}/schedule"
    print(f"Fetching schedule {schedule_url} ...")
    schedule_html = client.get(schedule_url)
    games = parse_schedule(schedule_html)
    print(f"  found {len(games)} games on schedule")

    # Filter to games with a Final status (have stats); also keep scheduled.
    final_games = [g for g in games if (g.get("status") or "").lower() == "final"]
    print(f"  {len(final_games)} Final games eligible for boxscore parsing")

    players_by_key: dict[tuple, int] = {}
    players_by_id: dict[int, dict] = {}
    next_id_box = [SYNTHETIC_ID_START]
    all_stats: list[dict] = []
    all_pbp: list[dict] = []

    fetched = 0
    failed: list[dict] = []
    pbp_failures: list[dict] = []
    for game in final_games:
        if max_games is not None and fetched >= max_games:
            break
        box_url = f"{BASE}{game['boxscore_path']}?dec=printer-decorator"
        cache_path = CACHE_DIR / f"{year}_{game['source_game_key']}.html"
        if cache_path.exists():
            box_html = cache_path.read_text(encoding="utf-8")
        else:
            try:
                box_html = client.get(box_url)
            except Exception as exc:
                print(f"  FAIL {game['boxscore_path']}: {exc}")
                failed.append({"game_id": game["game_id"], "error": str(exc)})
                continue
            cache_path.write_text(box_html, encoding="utf-8")

        try:
            stat_rows = parse_boxscore(box_html, game, players_by_key,
                                       players_by_id, next_id_box)
        except Exception as exc:
            print(f"  PARSE FAIL {game['boxscore_path']}: {exc}")
            failed.append({"game_id": game["game_id"], "error": f"parse: {exc}"})
            continue

        all_stats.extend(stat_rows)

        if include_pbp:
            pbp_url = f"{BASE}{game['boxscore_path']}?view=plays&dec=printer-decorator"
            pbp_cache = CACHE_DIR / f"{year}_{game['source_game_key']}_plays.html"
            if pbp_cache.exists():
                pbp_html = pbp_cache.read_text(encoding="utf-8")
            else:
                try:
                    pbp_html = client.get(pbp_url)
                    pbp_cache.write_text(pbp_html, encoding="utf-8")
                except Exception as exc:
                    print(f"  PBP FAIL {game['boxscore_path']}: {exc}")
                    pbp_failures.append({"game_id": game["game_id"], "error": str(exc)})
                    pbp_html = None

            if pbp_html:
                try:
                    pbp_obj = parse_pbp(pbp_html, game)
                except Exception as exc:
                    print(f"  PBP PARSE FAIL {game['boxscore_path']}: {exc}")
                    pbp_failures.append({"game_id": game["game_id"],
                                         "error": f"parse: {exc}"})
                    pbp_obj = None
                if pbp_obj and pbp_obj.get("plays"):
                    all_pbp.append(pbp_obj)

        fetched += 1
        if fetched % 5 == 0:
            print(f"  processed {fetched}/{len(final_games)} games, "
                  f"{len(players_by_id)} players, {len(all_stats)} stat rows, "
                  f"{len(all_pbp)} pbp")

    players = sorted(players_by_id.values(), key=lambda p: p["id"])
    games_out = sorted(games, key=lambda g: (g.get("start_time") or "",
                                             g.get("source_game_key") or ""))
    stats_out = sorted(all_stats, key=lambda r: (str(r.get("game_id", "")),
                                                 int(r.get("player_id", 0)),
                                                 str(r.get("stat", ""))))
    pbp_out = sorted(all_pbp, key=lambda p: str(p.get("game_id", "")))

    raw_meta = {
        "source": "goifl.com (Googlebot UA)",
        "year": year,
        "schedule_url": schedule_url,
        "counts": {
            "players": len(players),
            "stats": len(stats_out),
            "games": len(games_out),
            "boxscores_fetched": fetched,
            "boxscore_failures": len(failed),
            "pbp_games": len(pbp_out),
            "pbp_failures": len(pbp_failures),
        },
        "failures": failed[:50],
        "pbp_failures": pbp_failures[:50],
    }

    return players, stats_out, games_out, pbp_out, raw_meta


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape IFL data from the official goifl.com site")
    ap.add_argument("--year", type=int, default=dt.date.today().year,
                    help="Season year (default: current year)")
    ap.add_argument("--max-games", type=int, help="Limit boxscores for quick tests")
    ap.add_argument("--no-pbp", action="store_true",
                    help="Skip play-by-play fetch/parse")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    players, stats, games, pbp, raw_meta = scrape(
        args.year, max_games=args.max_games, include_pbp=not args.no_pbp,
    )

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    GAMES_FILE.write_text(json.dumps(games, indent=2), encoding="utf-8")
    PBP_FILE.write_text(json.dumps(pbp, indent=2), encoding="utf-8")
    RAW_FILE.write_text(json.dumps(raw_meta, indent=2), encoding="utf-8")

    print(f"Wrote {PLAYERS_FILE.name} ({len(players)}), "
          f"{STATS_FILE.name} ({len(stats)}), "
          f"{GAMES_FILE.name} ({len(games)}), "
          f"{PBP_FILE.name} ({len(pbp)}), "
          f"{RAW_FILE.name}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Scrape full NAL historical data from FootballShift/DigitalShift endpoints.

Outputs:
  - pipeline/raw/nal_players.json
  - pipeline/raw/nal_stats.json
  - pipeline/raw/nal_games.json
  - pipeline/raw/nal_raw.json

Notes:
  - Excludes games marked as Non-League Opponent(s).
  - Parses per-game player rows from team-stats tables.
  - Uses stable synthetic player IDs to avoid collisions with SQL IDs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).parent.parent
RAW_DIR = REPO_ROOT / "pipeline" / "raw"
PLAYERS_FILE = RAW_DIR / "nal_players.json"
STATS_FILE = RAW_DIR / "nal_stats.json"
GAMES_FILE = RAW_DIR / "nal_games.json"
RAW_FILE = RAW_DIR / "nal_raw.json"

API_URL = "https://web.api.digitalshift.ca"
HISTORICAL_STATS_URL = "https://www.thenationalarenaleague.com/historical-stats"
CLIENT_SERVICE_ID = "2f77fc4a-2c6f-4835-8918-ed31460e3e56"
LEAGUE_ID = 1200
SYNTHETIC_ID_START = 1_200_000

REQUEST_HEADERS = {
    "User-Agent": "AltSportsArchive/1.0 (https://archive.altfantasysports.com)",
}

TEAM_STAT_TYPES = [
    "offensive",
    "passing",
    "rushing",
    "receiving",
    "defensive",
    "returning",
    "punting",
    "kicking",
]

SKIP_HEADERS = {"#", "name", "player", "pos", "team"}

# Category-aware column mappings to canonical-ish stat keys.
HEADER_MAP = {
    "passing": {
        "comp": "pass_completions",
        "comp_pct": "completion_pct",
        "inc": "pass_incompletions",
        "int": "interceptions",
        "att": "pass_attempts",
        "yds": "passing_yards",
        "yds_avg": "pass_yards_per_attempt",
        "td": "passing_tds",
        "td_pct": "pass_td_pct",
        "rat": "passer_rating",
        "long": "pass_long",
    },
    "rushing": {
        "att": "rushing_attempts",
        "yds": "rushing_yards",
        "yds_avg": "rush_avg",
        "td": "rushing_tds",
        "long": "rush_long",
        "fumbles": "fumbles_lost",
    },
    "receiving": {
        "rec": "receptions",
        "yds": "receiving_yards",
        "yds_avg": "rec_avg",
        "td": "receiving_tds",
        "long": "rec_long",
        "fumbles": "fumbles_lost",
    },
    "defensive": {
        "tck": "tackles",
        "atck": "assisted_tackles",
        "sacks": "sacks",
        "int": "def_interceptions",
        "int_yds": "int_yards",
        "int_avg_yds": "int_avg_yards",
        "int_long": "int_long",
        "pbu": "pass_defended",
        "ff": "forced_fumbles",
        "fr": "fumble_recoveries",
        "fr_yds": "fumble_recovery_yards",
        "fr_avg_yds": "fumble_recovery_avg_yards",
        "fr_long": "fumble_recovery_long",
        "fr_td": "fumble_recovery_tds",
        "sfty": "safeties",
        "td": "defensive_tds",
    },
    "returning": {
        "kr": "kick_returns",
        "kr_yds": "kr_yards",
        "kr_avg_yds": "kr_avg",
        "kr_long": "kr_long",
        "kr_td": "kr_tds",
        "pr": "punt_returns",
        "pr_yds": "pr_yards",
        "pr_avg_yds": "pr_avg",
        "pr_long": "pr_long",
        "pr_td": "pr_tds",
    },
    "punting": {
        "punts": "punts",
        "punt_yds": "punt_yards",
        "punt_avg": "punt_avg",
        "punt_long": "punt_long",
        "punts_in_20": "punts_inside_20",
    },
    "kicking": {
        "fg_att": "fg_attempts",
        "fg": "field_goals_made",
        "fg_pct": "field_goal_pct",
        "fg_long": "field_goal_long",
        "xp_att": "xp_attempts",
        "xp": "xp_made",
        "xp_pct": "xp_pct",
    },
    "offensive": {
        "pts": "points",
        "pts_avg": "points_avg",
        "yds": "total_yards",
        "yds_avg": "yards_per_game",
        "games_played": "games_played",
    },
}


def normalize_header(header: str) -> str:
    h = (header or "").strip().lower()
    h = h.replace("%", " pct")
    h = h.replace("/", " ")
    h = h.replace(".", "")
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")

    # Normalize common variants used by FootballShift tables.
    h = h.replace("avg_yds", "avg_yds")
    h = h.replace("yds_avg", "yds_avg")

    if h in {"comp_pct", "completion_pct", "comp_pctg"}:
        return "comp_pct"
    if h in {"td_pct", "tdpct"}:
        return "td_pct"
    if h in {"fg_pct", "fgp"}:
        return "fg_pct"
    if h in {"xp_pct", "xpp"}:
        return "xp_pct"
    return h


def parse_float(value: str) -> float | None:
    v = (value or "").strip()
    if not v:
        return None
    if v in {"-", "--", "N/A", "n/a"}:
        return None
    v = v.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", v)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def parse_date_parts(date_str: str) -> tuple[int, int, int]:
    d = dt.date.fromisoformat(date_str)
    return d.year, d.month, d.day


def to_team_token(team_id: Any, game_id: Any, include_game: bool = False) -> str:
    tid = re.sub(r"[^0-9A-Za-z]", "", str(team_id or "UNK")).upper() or "UNK"
    if include_game:
        gid = re.sub(r"[^0-9A-Za-z]", "", str(game_id or "0")).upper() or "0"
        return f"T{tid}G{gid}"
    return f"T{tid}"


def football_game_id_from_obj(game_obj: dict[str, Any]) -> str:
    year, month, day = parse_date_parts(game_obj.get("date", "1970-01-01"))
    away = to_team_token(game_obj.get("away_team_id"), game_obj.get("game_id"), include_game=True)
    home = to_team_token(game_obj.get("home_team_id"), game_obj.get("game_id"), include_game=False)
    return f"FOOTBALL_NAL_{year}_{month}_{day}_{away}@{home}"


def parse_week(game_obj: dict[str, Any]) -> int:
    number = str(game_obj.get("number") or "").strip()
    if number.isdigit():
        return int(number)
    m = re.search(r"(\d+)", number)
    if m:
        return int(m.group(1))
    return 1


def extract_json_objects_with_marker(text: str, marker: str) -> list[dict[str, Any]]:
    objs: list[dict[str, Any]] = []
    idx = 0
    while True:
        i = text.find(marker, idx)
        if i == -1:
            break
        s = text.rfind("{", 0, i)
        if s < 0:
            idx = i + 1
            continue

        depth = 0
        in_str = False
        esc = False
        e = s
        while e < len(text):
            ch = text[e]
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
            e += 1

        if depth == 0 and e < len(text):
            blob = text[s : e + 1]
            try:
                objs.append(json.loads(blob))
            except json.JSONDecodeError:
                pass
        idx = i + 1
    return objs


def is_non_league_game(game_obj: dict[str, Any]) -> bool:
    check = " ".join(
        [
            str(game_obj.get("home_division") or ""),
            str(game_obj.get("away_division") or ""),
            str(game_obj.get("game_type") or ""),
        ]
    ).lower()
    return "non-league opponent" in check or "non-league opponents" in check


class DigitalShiftClient:
    def __init__(self, client_service_id: str, api_url: str):
        self.client_service_id = client_service_id
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(REQUEST_HEADERS)
        self.ticket_hash: str | None = None

    def login(self) -> None:
        r = self.session.post(
            f"{self.api_url}/login",
            json={"client_service_id": self.client_service_id},
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json()
        self.ticket_hash = payload["ticket"]["hash"]
        self.session.headers.update({"Authorization": f'ticket="{self.ticket_hash}"'})

    def fetch_partial_content(self, slug: str, params: dict[str, Any]) -> str:
        r = self.session.get(f"{self.api_url}/partials/{slug}", params=params, timeout=45)
        r.raise_for_status()
        payload = r.json()
        return html.unescape(payload.get("content") or "")


def discover_season_ids() -> list[int]:
    r = requests.get(HISTORICAL_STATS_URL, headers=REQUEST_HEADERS, timeout=30)
    r.raise_for_status()
    body = r.text

    ids = {int(x) for x in re.findall(r"season_id=(\d+)", body)}
    active = re.search(r'"active_season_id"\s*:\s*(\d+)', body)
    if active:
        ids.add(int(active.group(1)))

    # Known historical IDs discovered from NAL site markup.
    ids.update({7492, 9264})

    return sorted(ids)


def get_schedule_games(client: DigitalShiftClient, season_id: int) -> list[dict[str, Any]]:
    content = client.fetch_partial_content(
        "stats/schedule/table",
        {"league_id": LEAGUE_ID, "season_id": season_id, "all": "true"},
    )
    objs = extract_json_objects_with_marker(content, '"type":"game"')

    by_game_id: dict[int, dict[str, Any]] = {}
    for obj in objs:
        gid = obj.get("game_id")
        if isinstance(gid, int):
            by_game_id[gid] = obj
    return list(by_game_id.values())


def extract_side_and_type(table_tag) -> tuple[str, str]:
    side = ""
    player_type = ""
    node = table_tag
    while node is not None:
        ng_if = node.attrs.get("ng-if") if hasattr(node, "attrs") else None
        if isinstance(ng_if, str):
            m = re.search(r"ctrl\.side\s*==\s*'([^']+)'", ng_if)
            if m and not side:
                side = m.group(1)
            m = re.search(r"ctrl\.player_type\s*==\s*'([^']+)'", ng_if)
            if m and not player_type:
                player_type = m.group(1)
        node = getattr(node, "parent", None)
    return side, player_type


def extract_team_names(soup: BeautifulSoup) -> dict[str, str]:
    names = [
        a.get_text(" ", strip=True)
        for a in soup.select('div.tabs[aria-label="Team Stats"] a.name')
        if a.get_text(" ", strip=True)
    ]
    side_names = {}
    if len(names) >= 1:
        side_names["left"] = names[0]
    if len(names) >= 2:
        side_names["right"] = names[1]
    return side_names


def map_stat_key(player_type: str, raw_header: str) -> str | None:
    key = normalize_header(raw_header)
    if not key or key in SKIP_HEADERS:
        return None

    mapped = HEADER_MAP.get(player_type, {}).get(key)
    if mapped:
        return mapped

    # Fallback for unknown columns: namespaced key so we do not lose data.
    if player_type:
        return f"{player_type}_{key}"
    return key


def parse_team_stats_for_game(
    game_obj: dict[str, Any],
    content: str,
    player_id_map: dict[int, int],
    players_by_id: dict[int, dict[str, Any]],
    next_player_id: int,
) -> tuple[list[dict[str, Any]], int]:
    soup = BeautifulSoup(content, "html.parser")
    side_names = extract_team_names(soup)

    game_id = football_game_id_from_obj(game_obj)
    week = parse_week(game_obj)
    year = parse_date_parts(game_obj.get("date", "1970-01-01"))[0]

    out_stats: list[dict[str, Any]] = []
    seen_tables: set[tuple[str, str, tuple[str, ...]]] = set()
    seen_rows: set[tuple[int, str, str, float]] = set()

    for table in soup.select("table.stats-table"):
        side, player_type = extract_side_and_type(table)
        if not player_type or player_type not in TEAM_STAT_TYPES:
            continue

        headers = [th.get_text(" ", strip=True) for th in table.select("thead th")]
        dedupe_table_key = (side, player_type, tuple(headers))
        if dedupe_table_key in seen_tables:
            continue
        seen_tables.add(dedupe_table_key)

        trs = table.select("tbody tr")
        for tr in trs:
            tds = tr.find_all("td")
            if not tds:
                continue

            link = tr.select_one('a[href*="/player/"]')
            if not link:
                continue
            href = link.get("href") or ""
            m = re.search(r"/player/(\d+)", href)
            if not m:
                continue

            source_pid = int(m.group(1))
            if source_pid not in player_id_map:
                player_id_map[source_pid] = next_player_id
                next_player_id += 1
            pid = player_id_map[source_pid]

            name = link.get_text(" ", strip=True)

            pos = ""
            if "Pos" in headers:
                pos_idx = headers.index("Pos")
                if pos_idx < len(tds):
                    pos = tds[pos_idx].get_text(" ", strip=True)

            if pid not in players_by_id:
                names = name.split()
                players_by_id[pid] = {
                    "id": pid,
                    "full_name": name,
                    "short_name": name,
                    "first_name": names[0] if names else "",
                    "last_name": " ".join(names[1:]) if len(names) > 1 else "",
                    "sport_id": None,
                    "league": "NAL",
                    "team": side_names.get(side, ""),
                    "position": pos,
                    "_nal": True,
                    "_nal_person_id": source_pid,
                    "sportradar_id": None,
                    "college": None,
                    "jersey": None,
                    "height": None,
                    "weight": None,
                }
            else:
                if not players_by_id[pid].get("team") and side_names.get(side):
                    players_by_id[pid]["team"] = side_names[side]
                if not players_by_id[pid].get("position") and pos:
                    players_by_id[pid]["position"] = pos

            for i, header in enumerate(headers):
                if i >= len(tds):
                    continue
                stat_key = map_stat_key(player_type, header)
                if not stat_key:
                    continue
                val = parse_float(tds[i].get_text(" ", strip=True))
                if val is None:
                    continue

                row_key = (pid, stat_key, game_id, val)
                if row_key in seen_rows:
                    continue
                seen_rows.add(row_key)

                out_stats.append(
                    {
                        "player_id": pid,
                        "week": week,
                        "stat": stat_key,
                        "value": float(val),
                        "game_id": game_id,
                        "league": "NAL",
                        "_year": year,
                    }
                )

    return out_stats, next_player_id


def build_game_record(game_obj: dict[str, Any]) -> dict[str, Any]:
    gid = football_game_id_from_obj(game_obj)
    return {
        "game_id": gid,
        "league": "NAL",
        "season_id": game_obj.get("season_id"),
        "source_game_id": game_obj.get("game_id"),
        "team_home": game_obj.get("home_team"),
        "team_away": game_obj.get("away_team"),
        "score_home": game_obj.get("home_score"),
        "score_away": game_obj.get("away_score"),
        "start_time": game_obj.get("datetime_tz") or game_obj.get("datetime"),
        "status": game_obj.get("status"),
        "week": parse_week(game_obj),
        "sport_id": None,
        "channel": "",
    }


def scrape_nal(season_ids: list[int], max_games: int | None = None) -> tuple[list[dict], list[dict], list[dict], dict]:
    client = DigitalShiftClient(CLIENT_SERVICE_ID, API_URL)
    client.login()

    players_by_id: dict[int, dict[str, Any]] = {}
    player_id_map: dict[int, int] = {}
    next_player_id = SYNTHETIC_ID_START

    all_stats: list[dict[str, Any]] = []
    all_games: list[dict[str, Any]] = []

    skipped_non_league = 0
    fetched_games = 0
    seasons_summary: list[dict[str, Any]] = []

    for season_id in season_ids:
        print(f"Season {season_id}: fetching schedule...")
        season_games = get_schedule_games(client, season_id)

        # Sort for deterministic output.
        season_games.sort(key=lambda g: (g.get("date") or "", int(g.get("game_id") or 0)))

        kept = 0
        skipped = 0
        for game_obj in season_games:
            if is_non_league_game(game_obj):
                skipped += 1
                continue

            if max_games is not None and fetched_games >= max_games:
                break

            all_games.append(build_game_record(game_obj))

            source_gid = game_obj.get("game_id")
            try:
                content = client.fetch_partial_content("stats/game/team-stats", {"game_id": source_gid})
            except Exception as exc:  # broad catch to continue scraping remaining games
                print(f"  game_id {source_gid}: team-stats fetch failed ({exc})")
                kept += 1
                fetched_games += 1
                continue

            game_stats, next_player_id = parse_team_stats_for_game(
                game_obj,
                content,
                player_id_map,
                players_by_id,
                next_player_id,
            )
            all_stats.extend(game_stats)

            kept += 1
            fetched_games += 1
            if fetched_games % 10 == 0:
                print(f"  processed {fetched_games} games total...")

        skipped_non_league += skipped
        seasons_summary.append(
            {
                "season_id": season_id,
                "schedule_games": len(season_games),
                "kept_games": kept,
                "skipped_non_league": skipped,
            }
        )

        if max_games is not None and fetched_games >= max_games:
            break

    # Deterministic sorting.
    players = sorted(players_by_id.values(), key=lambda p: p["id"])
    all_games = sorted(all_games, key=lambda g: (g.get("start_time") or "", str(g.get("source_game_id") or "")))
    all_stats = sorted(
        all_stats,
        key=lambda r: (r.get("_year", 0), str(r.get("game_id", "")), int(r.get("player_id", 0)), str(r.get("stat", ""))),
    )

    raw_meta = {
        "source": "digitalshift",
        "league_id": LEAGUE_ID,
        "client_service_id": CLIENT_SERVICE_ID,
        "season_ids": season_ids,
        "seasons": seasons_summary,
        "counts": {
            "players": len(players),
            "stats": len(all_stats),
            "games": len(all_games),
            "skipped_non_league": skipped_non_league,
        },
    }

    return players, all_stats, all_games, raw_meta


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape NAL data from DigitalShift")
    ap.add_argument(
        "--season-id",
        action="append",
        type=int,
        help="Season ID to scrape (repeatable). If omitted, discover from historical-stats page.",
    )
    ap.add_argument("--max-games", type=int, help="Limit number of games (for quick tests)")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if args.season_id:
        season_ids = sorted(set(args.season_id))
    else:
        season_ids = discover_season_ids()

    print(f"Target seasons: {season_ids}")
    players, stats, games, raw_meta = scrape_nal(season_ids, max_games=args.max_games)

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    GAMES_FILE.write_text(json.dumps(games, indent=2), encoding="utf-8")
    RAW_FILE.write_text(json.dumps(raw_meta, indent=2), encoding="utf-8")

    years = sorted({s.get("_year") for s in stats if s.get("_year")})
    print(
        f"Wrote {PLAYERS_FILE.name} ({len(players)}), "
        f"{STATS_FILE.name} ({len(stats)}), "
        f"{GAMES_FILE.name} ({len(games)}), "
        f"{RAW_FILE.name}; years={years}"
    )


if __name__ == "__main__":
    main()

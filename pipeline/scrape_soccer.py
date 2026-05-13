#!/usr/bin/env python3
"""
scrape_soccer.py — US Soccer player stats via the American Soccer Analysis API.

Covers:
  mls     Major League Soccer (1996+)
  nwsl    National Women's Soccer League (2013+)
  uslc    USL Championship
  usl1    USL League One
  usls    USL Super League
  mlsnp   MLS Next Pro
  nasl    North American Soccer League (2011–2017 revival)

Source: https://app.americansocceranalysis.com/api/v1/ (free, no auth required)

Players often move between these leagues; consolidated pages are produced by
build_data.py's canonical identity merge.

Outputs (pipeline/raw/):
  soccer_players.json  — synthetic player records (IDs start at 1_500_000)
  soccer_stats.json    — per-player-season stat rows
  soccer_state.json    — incremental scrape state (id_map, done_leagues)
"""

import argparse
import json
import time
from pathlib import Path

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
RAW  = BASE / "raw"
RAW.mkdir(exist_ok=True)

STATE_FILE   = RAW / "soccer_state.json"
PLAYERS_FILE = RAW / "soccer_players.json"
STATS_FILE   = RAW / "soccer_stats.json"
GAMES_FILE   = RAW / "soccer_games.json"

# ── Constants ─────────────────────────────────────────────────────────────────
ASA_BASE = "https://app.americansocceranalysis.com/api/v1"
HEADERS  = {
    "User-Agent": (
        "AltSportsArchive/1.0 (archive.altfantasysports.com; "
        "educational/archival; contact: altfantasysports.com)"
    )
}

SYNTHETIC_ID_START = 1_500_000

# Maps ASA league code → display label used in the rest of the pipeline
LEAGUES: dict[str, str] = {
    "mls":   "MLS",
    "nwsl":  "NWSL",
    "uslc":  "USLC",
    "usl1":  "USL1",
    "usls":  "USLS",
    "mlsnp": "MLSNP",
    "nasl":  "NASL",
}

LEAGUE_LONG: dict[str, str] = {
    "mls":   "Major League Soccer",
    "nwsl":  "National Women's Soccer League",
    "uslc":  "USL Championship",
    "usl1":  "USL League One",
    "usls":  "USL Super League",
    "mlsnp": "MLS Next Pro",
    "nasl":  "North American Soccer League",
}

LEAGUE_GENDER: dict[str, str] = {
    "mls":   "M",
    "nwsl":  "F",
    "uslc":  "M",
    "usl1":  "M",
    "usls":  "F",
    "mlsnp": "M",
    "nasl":  "M",
}

# ── HTTP helper ───────────────────────────────────────────────────────────────

PAGE_SIZE = 1000


def _get(url: str, params: dict | None = None) -> list | dict | None:
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=45)
        if r.status_code == 200:
            return r.json()
        print(f"  HTTP {r.status_code}: {url}")
        return None
    except Exception as exc:
        print(f"  Error fetching {url}: {exc}")
        return None


def _get_all(url: str, params: dict | None = None) -> list:
    """Fetch all pages from a paginated ASA endpoint using offset pagination."""
    base_params = dict(params or {})
    all_rows: list = []
    offset = 0
    while True:
        page_params = {**base_params, "offset": offset}
        rows = _get(url, params=page_params)
        if not isinstance(rows, list) or not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break          # last page
        offset += PAGE_SIZE
        time.sleep(0.5)    # be polite between pages
    return all_rows


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))


# ── API fetch helpers ─────────────────────────────────────────────────────────

def _fetch_players(lc: str) -> list[dict]:
    return _get_all(f"{ASA_BASE}/{lc}/players")


def _fetch_teams(lc: str) -> list[dict]:
    return _get_all(f"{ASA_BASE}/{lc}/teams")


def _fetch_xgoals(lc: str) -> list[dict]:
    return _get_all(
        f"{ASA_BASE}/{lc}/players/xgoals",
        params={"split_by_seasons": "true", "split_by_teams": "true"},
    )


def _fetch_xpass(lc: str) -> list[dict]:
    return _get_all(
        f"{ASA_BASE}/{lc}/players/xpass",
        params={"split_by_seasons": "true", "split_by_teams": "true"},
    )


def _fetch_gk_xgoals(lc: str) -> list[dict]:
    return _get_all(
        f"{ASA_BASE}/{lc}/goalkeepers/xgoals",
        params={"split_by_seasons": "true", "split_by_teams": "true"},
    )


def _fetch_games(lc: str) -> list[dict]:
    return _get_all(f"{ASA_BASE}/{lc}/games")


# ── Stat normalisation ────────────────────────────────────────────────────────

XGOALS_STAT_MAP: dict[str, str] = {
    "minutes_played":               "minutes_played",
    "shots":                        "shots",
    "shots_on_target":              "shots_on_target",
    "goals":                        "goals",
    "xgoals":                       "xg",
    "xplace":                       "xg_place",
    "goals_minus_xgoals":           "goals_minus_xg",
    "key_passes":                   "key_passes",
    "primary_assists":              "assists",
    "xassists":                     "xa",
    "primary_assists_minus_xassists": "assists_minus_xa",
    "goals_plus_primary_assists":   "goals_plus_assists",
    "xgoals_plus_xassists":         "xg_plus_xa",
    "points_added":                 "points_added",
    "xpoints_added":                "xpoints_added",
}

XPASS_STAT_MAP: dict[str, str] = {
    "attempted_passes":                  "passes_attempted",
    "pass_completion_percentage":        "pass_completion_pct",
    "xpass_completion_percentage":       "xpass_completion_pct",
    "passes_completed_over_expected":    "passes_over_expected",
    "passes_completed_over_expected_p100": "passes_over_expected_p100",
    "avg_distance_yds":                  "avg_pass_distance",
    "avg_vertical_distance_yds":         "avg_pass_vertical",
    "share_aerial_passes":               "aerial_pass_share",
    "xpass_total":                       "xpass_total",
}

GK_XGOALS_STAT_MAP: dict[str, str] = {
    "minutes_played":               "minutes_played",
    "shots_faced":                  "shots_faced",
    "goals_conceded":               "goals_conceded",
    "saves":                        "saves",
    "share_headed_shots":           "headed_shot_share",
    "xgoals_gk_faced":              "xg_faced",
    "goals_minus_xgoals_gk":        "goals_conceded_minus_xg",
    "goals_divided_by_xgoals_gk":   "goals_per_xg",
}


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if (f != f) else f   # isnan → None
    except (TypeError, ValueError):
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Scrape US soccer stats from ASA API")
    ap.add_argument("--reset", action="store_true",
                    help="Clear all state and output files; re-scrape everything")
    ap.add_argument("--leagues", nargs="+", choices=list(LEAGUES.keys()),
                    default=list(LEAGUES.keys()),
                    help="Which leagues to (re-)scrape (default: all)")
    ap.add_argument("--force", action="store_true",
                    help="Re-scrape specified leagues even if already marked done")
    ap.add_argument("--status", action="store_true",
                    help="Print scrape status and exit")
    args = ap.parse_args()

    # ── Load state ────────────────────────────────────────────────────────────
    state: dict = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}

    if args.reset:
        state = {}
        players_out: list[dict] = []
        stats_out: list[dict]   = []
        games_out: list[dict]   = []
    else:
        players_out = load_json(PLAYERS_FILE, [])
        stats_out   = load_json(STATS_FILE,   [])
        games_out   = load_json(GAMES_FILE,   [])

    # ASA string player_id → our synthetic int id
    id_map: dict[str, int]   = state.get("id_map", {})
    done_leagues: set[str]   = set(state.get("done_leagues", []))
    next_id: int             = state.get("next_id", SYNTHETIC_ID_START)

    if args.status:
        print("Soccer scraper status:")
        print(f"  Done leagues : {sorted(done_leagues)}")
        print(f"  Players      : {len(players_out)}")
        print(f"  Stat rows    : {len(stats_out)}")
        print(f"  Next ID      : {next_id}")
        return

    # Quick-index existing players by numeric id
    player_index: dict[int, dict] = {p["id"]: p for p in players_out}

    # Track which (numeric_id, game_id, stat) tuples are already stored so
    # re-runs don't create duplicates.
    stored_keys: set[tuple] = {
        (r["player_id"], r["game_id"], r["stat"]) for r in stats_out
    }
    stored_game_ids: set[str] = {str(g.get("game_id", "")) for g in games_out if g.get("game_id")}

    def _flush():
        save_json(PLAYERS_FILE, players_out)
        save_json(STATS_FILE,   stats_out)
        save_json(GAMES_FILE,   games_out)
        state["id_map"]        = id_map
        state["done_leagues"]  = sorted(done_leagues)
        state["next_id"]       = next_id
        save_json(STATE_FILE, state)

    def _ensure_id(asa_id: str) -> int:
        nonlocal next_id
        if asa_id not in id_map:
            id_map[asa_id] = next_id
            next_id += 1
        return id_map[asa_id]

    def _add_stats(num_id: int, game_id: str, year: int | None,
                   league_label: str, team_id: str, stat_map_row: dict):
        """Append stat rows, skipping already-stored (player, game, stat) keys."""
        for stat_name, val in stat_map_row.items():
            fval = _safe_float(val)
            if fval is None:
                continue
            key = (num_id, game_id, stat_name)
            if key in stored_keys:
                continue
            stats_out.append({
                "player_id": num_id,
                "week":      1,
                "stat":      stat_name,
                "value":     fval,
                "game_id":   game_id,
                "_year":     year,
                "_league":   league_label,
                "_team_id":  team_id,
            })
            stored_keys.add(key)

    # ── Per-league scrape ─────────────────────────────────────────────────────
    for lc in args.leagues:
        label = LEAGUES[lc]

        if lc in done_leagues and not args.force:
            print(f"[{label}] already done — skip (use --force to re-scrape)")
            continue

        print(f"\n── {LEAGUE_LONG[lc]} ({lc}) ──────────────────")

        print("  Fetching teams...")
        raw_teams = _fetch_teams(lc)
        team_map = {t.get("team_id"): t for t in raw_teams if t.get("team_id")}
        time.sleep(1)

        # 1. Player profiles ──────────────────────────────────────────────────
        print("  Fetching player profiles...")
        raw_players = _fetch_players(lc)
        time.sleep(1)

        if not raw_players:
            print("  No players returned — skipping league.")
            continue

        for p in raw_players:
            asa_id = p.get("player_id", "")
            if not asa_id:
                continue
            num_id = _ensure_id(asa_id)

            if num_id not in player_index:
                ht = (
                    f"{p['height_ft']}'{p['height_in']}\""
                    if p.get("height_ft") is not None else None
                )
                rec = {
                    "id":            num_id,
                    "full_name":     p.get("player_name", ""),
                    "team":          "",
                    "position":      p.get("primary_general_position", "") or p.get("primary_broad_position", ""),
                    "sport_id":      None,
                    "league":        label,
                    "jersey":        None,
                    "college":       None,
                    "college_stats": None,
                    "height":        ht,
                    "weight":        str(p["weight_lb"]) if p.get("weight_lb") else None,
                    "birth_date":    p.get("birth_date", ""),
                    "nationality":   p.get("nationality", ""),
                    "gender":        LEAGUE_GENDER.get(lc, ""),
                    "_asa_id":       asa_id,
                    "_leagues":      [label],
                }
                player_index[num_id] = rec
                players_out.append(rec)
            else:
                # Merge in this league if not already present
                ex = player_index[num_id]
                ex.setdefault("_leagues", [])
                if label not in ex["_leagues"]:
                    ex["_leagues"].append(label)

        print(f"  {len(raw_players)} players")

        # 2. xGoals (outfield players) ─────────────────────────────────────────
        print("  Fetching xGoals (outfield)...")
        xg_rows = _fetch_xgoals(lc)
        time.sleep(1)

        xg_added = 0
        for row in xg_rows:
            asa_id = row.get("player_id", "")
            season = str(row.get("season_name", "")).strip()
            team_id = row.get("team_id", "")
            if not asa_id or not season:
                continue
            num_id  = _ensure_id(asa_id)
            year    = int(season) if season.isdigit() else None
            game_id = f"soccer-{lc}-{season}-{team_id}"

            stat_vals = {
                out_name: row.get(in_name)
                for in_name, out_name in XGOALS_STAT_MAP.items()
            }
            before = len(stats_out)
            _add_stats(num_id, game_id, year, label, team_id, stat_vals)
            xg_added += len(stats_out) - before

        print(f"  +{xg_added} xGoals stat rows ({len(xg_rows)} source rows)")

        # 3. xPass ─────────────────────────────────────────────────────────────
        print("  Fetching xPass...")
        xp_rows = _fetch_xpass(lc)
        time.sleep(1)

        xp_added = 0
        for row in xp_rows:
            asa_id  = row.get("player_id", "")
            season  = str(row.get("season_name", "")).strip()
            team_id = row.get("team_id", "")
            if not asa_id or not season:
                continue
            num_id  = _ensure_id(asa_id)
            year    = int(season) if season.isdigit() else None
            game_id = f"soccer-{lc}-{season}-{team_id}"

            stat_vals = {
                out_name: row.get(in_name)
                for in_name, out_name in XPASS_STAT_MAP.items()
            }
            before = len(stats_out)
            _add_stats(num_id, game_id, year, label, team_id, stat_vals)
            xp_added += len(stats_out) - before

        print(f"  +{xp_added} xPass stat rows ({len(xp_rows)} source rows)")

        # 4. Goalkeeper xGoals ─────────────────────────────────────────────────
        print("  Fetching GK xGoals...")
        gk_rows = _fetch_gk_xgoals(lc)
        time.sleep(1)

        gk_added = 0
        for row in gk_rows:
            asa_id  = row.get("player_id", "")
            season  = str(row.get("season_name", "")).strip()
            team_id = row.get("team_id", "")
            if not asa_id or not season:
                continue
            num_id  = _ensure_id(asa_id)
            year    = int(season) if season.isdigit() else None
            game_id = f"soccer-{lc}-{season}-{team_id}"

            stat_vals = {
                out_name: row.get(in_name)
                for in_name, out_name in GK_XGOALS_STAT_MAP.items()
            }
            before = len(stats_out)
            _add_stats(num_id, game_id, year, label, team_id, stat_vals)
            gk_added += len(stats_out) - before

        print(f"  +{gk_added} GK xGoals stat rows ({len(gk_rows)} source rows)")

        # 5. Real fixture list for league pages ──────────────────────────────
        print("  Fetching games...")
        game_rows = _fetch_games(lc)
        time.sleep(1)

        games_added = 0
        for g in game_rows:
            asa_game_id = g.get("game_id")
            season = str(g.get("season_name", "")).strip()
            if not asa_game_id or not season:
                continue

            home_id = g.get("home_team_id", "")
            away_id = g.get("away_team_id", "")
            home_team = team_map.get(home_id, {})
            away_team = team_map.get(away_id, {})
            home_abbr = home_team.get("team_abbreviation") or home_id
            away_abbr = away_team.get("team_abbreviation") or away_id
            game_id = f"soccer-{lc}-{season}-{away_abbr}-{home_abbr}-{asa_game_id}"
            if game_id in stored_game_ids:
                continue

            start_time = (g.get("date_time_utc") or "").replace(" UTC", "")
            games_out.append({
                "id": f"soccer-{lc}-{asa_game_id}",
                "sport_id": None,
                "status": g.get("status"),
                "start_time": start_time,
                "team_home": home_team.get("team_short_name") or home_team.get("team_name") or home_id,
                "team_away": away_team.get("team_short_name") or away_team.get("team_name") or away_id,
                "created_at": None,
                "updated_at": g.get("last_updated_utc"),
                "week": g.get("matchday"),
                "game_id": game_id,
                "score_home": g.get("home_score"),
                "score_away": g.get("away_score"),
                "channel": "",
                "streaming_link": None,
                "period": None,
                "time_left": None,
                "active": 0,
                "possession_home": None,
                "possession_away": None,
                "record_home": None,
                "record_away": None,
                "status_line": g.get("status"),
                "spread_home": None,
                "spread_away": None,
                "moneyline_home": None,
                "moneyline_away": None,
                "total_home": None,
                "total_away": None,
                "league": label,
            })
            stored_game_ids.add(game_id)
            games_added += 1

        print(f"  +{games_added} games ({len(game_rows)} source rows)")

        # ── Save after each league ─────────────────────────────────────────────
        done_leagues.add(lc)
        _flush()
        print(
            f"  Saved. Running total: {len(players_out)} players, "
            f"{len(stats_out)} stat rows, {len(games_out)} games"
        )

    print(
        f"\nDone. {len(players_out)} players, {len(stats_out)} stat rows, {len(games_out)} games "
        f"across {len(done_leagues)} leagues."
    )


if __name__ == "__main__":
    main()

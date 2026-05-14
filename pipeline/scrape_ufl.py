#!/usr/bin/env python3
"""
scrape_ufl.py — United Football League (UFL) game data and play-by-play archive
Source: https://www.theufl.com/api/ (Laravel-style JSON API)

UFL seasons and schedule_week_id ranges:
  2024 PST: week_ids  1-2   (June 2024 playoffs)      → sport_id=19
  2024 REG: week_ids  3-12  (March-June 2024)          → sport_id=19
  2025 PST: week_ids 13-14  (June 2025 playoffs)       → sport_id=25
  2025 REG: week_ids 15-24  (March-June 2025)          → sport_id=25
  2026 PST: week_ids 25-26  (June 2026 playoffs)       → sport_id=31
  2026 REG: week_ids 27-36  (March-June 2026)          → sport_id=31

API endpoints used:
  GET /api/schedules                         — Season/schedule metadata
  GET /api/schedule-events?per_page=200      — All games (max 200 per call)
  GET /api/schedule-events/{id}/boxscore     — Game boxscore + scoring plays
       ?include[0]=scheduleEventBoxScore
       &include[1]=scheduleEventScoring
  GET /api/teams                             — Team metadata

Boxscore vendor_data keys:
  .scoring_plays[]   — Scoring plays with player details
  .scoring_drives[]  — Scoring drives with full play-by-play for each drive

Scoring play detail roles:
  rush        — Rushing TD carrier
  pass        — Passing TD thrower
  pass_recv   — Passing TD receiver
  kick / kicker — Field goal / PAT kicker
  return / returner — Return TD
  defense     — Defensive TD

Outputs (pipeline/raw/):
  ufl_raw.json     — Raw API response cache (game list + all boxscores)
  ufl_games.json   — Game records compatible with the raw_games table
  ufl_pbp.json     — Play-by-play archive (scoring drives + scoring plays per game)
  ufl_players.json — Synthetic player records (IDs starting at 1,200,000)
  ufl_stats.json   — Per-game scoring stat rows (rush_td, pass_td, recv_td, fg_made, …)

build_data.py integration:
  Add to _new_league_pairs:
    ("ufl_players.json", "ufl_stats.json", "UFL supplemental")
  Add to games loading section:
    ufl_games_file = RAW / "ufl_games.json"
    if ufl_games_file.exists(): raw_games.extend(...)

Usage:
  python pipeline/scrape_ufl.py              # Scrape all closed games (uses cache)
  python pipeline/scrape_ufl.py --reset      # Force re-fetch everything
  python pipeline/scrape_ufl.py --game 47    # Scrape single game by UFL API ID
  python pipeline/scrape_ufl.py --status     # Show scrape progress and exit
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = "https://www.theufl.com/api"
DELAY    = 1.0   # polite delay between requests (seconds)

PIPELINE = Path(__file__).parent
RAW      = PIPELINE / "raw"
RAW.mkdir(exist_ok=True)

RAW_CACHE_FILE = RAW / "ufl_raw.json"
GAMES_FILE     = RAW / "ufl_games.json"
PBP_FILE       = RAW / "ufl_pbp.json"
PLAYERS_FILE   = RAW / "ufl_players.json"
STATS_FILE     = RAW / "ufl_stats.json"

HEADERS = {
    "User-Agent": (
        "AltSportsArchive/1.0 (https://archive.altfantasysports.com; "
        "altfantasysports@gmail.com) Python/requests"
    ),
    "Accept": "application/json",
}

LEAGUE             = "UFL"
SYNTHETIC_ID_START = 1_200_000

# sport_id from docs/data/sports.json: UFL 2024→19, 2025→25, 2026→31
SPORT_ID_BY_YEAR = {2024: 19, 2025: 25, 2026: 31}

# schedule_week_id → (season_year, season_type, display_week_number)
# Derived from /api/schedule-events datetime analysis.
_WEEK_MAP: dict[int, tuple[int, str, int]] = {}
for _wid in range(1,  3):  _WEEK_MAP[_wid] = (2024, "PST", _wid)
for _wid in range(3,  13): _WEEK_MAP[_wid] = (2024, "REG", _wid - 2)
for _wid in range(13, 15): _WEEK_MAP[_wid] = (2025, "PST", _wid - 12)
for _wid in range(15, 25): _WEEK_MAP[_wid] = (2025, "REG", _wid - 14)
for _wid in range(25, 27): _WEEK_MAP[_wid] = (2026, "PST", _wid - 24)
for _wid in range(27, 37): _WEEK_MAP[_wid] = (2026, "REG", _wid - 26)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def safe_get(url: str, params: dict | None = None, retries: int = 3) -> dict | None:
    """GET JSON with retry. Returns parsed dict or None on failure."""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                print(f"  404: {url}")
                return None
            print(f"  HTTP {r.status_code}: {url} (attempt {attempt + 1})")
        except requests.RequestException as exc:
            print(f"  WARN: {exc} (attempt {attempt + 1})")
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    return None


# ── Cache ─────────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if RAW_CACHE_FILE.exists():
        return json.loads(RAW_CACHE_FILE.read_text())
    return {"game_list": None, "boxscores": {}}


def save_cache(cache: dict) -> None:
    RAW_CACHE_FILE.write_text(json.dumps(cache, indent=2))


# ── Fetch logic ───────────────────────────────────────────────────────────────

def fetch_game_list(cache: dict, reset: bool) -> list[dict]:
    if cache.get("game_list") and not reset:
        games = cache["game_list"]
        print(f"Game list from cache ({len(games)} games)")
        return games

    print("Fetching /api/schedule-events ...")
    data = safe_get(f"{API_BASE}/schedule-events", params={"per_page": 200})
    if not data:
        sys.exit("ERROR: Could not fetch game list from UFL API")

    # API may return { data: [...] } (flat) or { data: { data: [...] } } (paginated)
    raw = data.get("data", [])
    games = raw.get("data", []) if isinstance(raw, dict) else raw
    if not isinstance(games, list):
        sys.exit(f"ERROR: Unexpected game list format: {type(games)}")

    cache["game_list"] = games
    save_cache(cache)
    print(f"  Fetched {len(games)} games")
    return games


def fetch_boxscore(game_id: int, cache: dict, reset: bool) -> dict | None:
    key = str(game_id)
    if key in cache.get("boxscores", {}) and not reset:
        return cache["boxscores"][key]

    url = f"{API_BASE}/schedule-events/{game_id}/boxscore"
    params = {
        "include[0]": "scheduleEventBoxScore",
        "include[1]": "scheduleEventScoring",
    }
    time.sleep(DELAY)
    data = safe_get(url, params=params)
    if data:
        cache.setdefault("boxscores", {})[key] = data
    return data


# ── Game metadata helpers ─────────────────────────────────────────────────────

def game_datetime_year(game: dict) -> int:
    dt = game.get("datetime") or ""
    try:
        return int(dt[:4])
    except (ValueError, TypeError):
        return 2024


def game_week_info(game: dict) -> tuple[int, str, int]:
    """Return (season_year, season_type, display_week) for a game."""
    wid = game.get("schedule_week_id", 0)
    if wid in _WEEK_MAP:
        return _WEEK_MAP[wid]
    year = game_datetime_year(game)
    return (year, "REG", wid)


def canonical_game_id(game: dict) -> str:
    """Build a string game ID: FOOTBALL_UFL_{YEAR}_{M}_{D}_{AWAY}@{HOME}"""
    dt = game.get("datetime") or ""
    month, day = 1, 1
    if dt:
        try:
            parts = dt.split("T")[0].split("-")
            month, day = int(parts[1]), int(parts[2])
        except (IndexError, ValueError):
            pass
    year, _, _ = game_week_info(game)
    away = (game.get("away_team_alias") or "UNK").upper()
    home = (game.get("home_team_alias") or "UNK").upper()
    return f"FOOTBALL_UFL_{year}_{month}_{day}_{away}@{home}"


# ── Stat extraction ───────────────────────────────────────────────────────────

# Category prefixes that indicate a scoring play type.
# The API uses compound-ish categories like "rush", "pass_reception", "field_goal", etc.
# We identify scoring type at the PLAY level (all details together) to avoid duplicates.
# Each scoring play may have multiple detail entries:
#   - The primary action (e.g. rush/touchdown)
#   - A generic "touchdown/touchdown" summary duplicate — SKIP
#   - For FGs: kicker (kick role) + holder (hold) + snapper (snap) — only count kicker
#   - For passing TDs: pass_completion (passer) + pass_reception/touchdown (receiver)

def extract_stats_from_scoring_plays(
    scoring_plays: list[dict],
    game: dict,
) -> tuple[list[dict], dict[str, dict]]:
    """
    Parse scoring_plays at the PLAY level and return:
      stat_rows    — list of stat row dicts keyed by _ufl_id (UUID)
      player_index — {uuid: player_dict} for all players seen
    """
    year, _, display_week = game_week_info(game)
    sport_id = SPORT_ID_BY_YEAR.get(year, 19)
    gid      = canonical_game_id(game)

    player_index: dict[str, dict] = {}
    stat_rows: list[dict] = []

    def register_player(p: dict) -> str | None:
        uid  = p.get("id", "")
        name = p.get("name", "")
        if not uid or not name:
            return None
        if uid not in player_index:
            player_index[uid] = {
                "_ufl_id":      uid,
                "full_name":    name,
                "position":     p.get("position", ""),
                "sport_id":     sport_id,
                "league":       LEAGUE,
                "season":       year,
                "_data_source": "theufl.com",
            }
        return uid

    def add_stat(uid: str, stat: str) -> None:
        stat_rows.append({
            "_ufl_id":  uid,
            "game_id":  gid,
            "stat":     stat,
            "value":    1.0,
            "week":     display_week,
            "season":   year,
            "league":   LEAGUE,
            "sport_id": sport_id,
            "_year":    year,
        })

    for play in scoring_plays:
        details = play.get("details") or []

        # Build a set of (category, result, role, player_uuid) tuples for this play,
        # skipping the generic "touchdown/touchdown" duplicate summary entries.
        # Also build a map of role → player for cross-detail lookups.
        role_players: dict[str, list[dict]] = {}  # role → [player, ...]
        cat_results: list[tuple[str, str]] = []    # (category, result) pairs seen

        for detail in details:
            cat = (detail.get("category") or "").lower()
            res = (detail.get("result")   or "").lower()
            # Skip generic duplicate summary entries (category="touchdown")
            if cat == "touchdown":
                continue
            cat_results.append((cat, res))
            for p in (detail.get("players") or []):
                register_player(p)
                role = (p.get("role") or "").lower()
                role_players.setdefault(role, []).append(p)

        # ── Rush TD ────────────────────────────────────────────────────────
        if any(cat == "rush" and res == "touchdown" for cat, res in cat_results):
            for p in role_players.get("rush", []):
                uid = p.get("id", "")
                if uid:
                    add_stat(uid, "rush_td")

        # ── Receiving TD (pass_reception / pass_recv) ───────────────────────
        recv_td_cats = {"pass_reception", "reception", "receive"}
        recv_roles   = {"catch", "pass_recv", "receiver", "receive"}
        if any(cat in recv_td_cats and res == "touchdown" for cat, res in cat_results):
            for role in recv_roles:
                for p in role_players.get(role, []):
                    uid = p.get("id", "")
                    if uid:
                        add_stat(uid, "recv_td")
            # Also credit the passer
            for role in ("pass", "qb", "quarterback"):
                for p in role_players.get(role, []):
                    uid = p.get("id", "")
                    if uid:
                        add_stat(uid, "pass_td")

        # ── Field Goal ─────────────────────────────────────────────────────
        fg_cats = {"field_goal", "fg"}
        if any(cat in fg_cats and res in ("good", "made") for cat, res in cat_results):
            for p in role_players.get("kick", []):
                uid = p.get("id", "")
                if uid:
                    add_stat(uid, "fg_made")

        # ── PAT (1-point conversion: extra_point or one_point_*) ───────────
        pat_cats = {"extra_point", "pat", "point_after",
                    "one_point_rush", "one_point_pass", "one_point_kick"}
        if any(cat in pat_cats and res in ("good", "made") for cat, res in cat_results):
            # Could be kick PAT or rush/pass PAT
            pat_role_prio = ["kick", "rush", "pass", "qb"]
            for role in pat_role_prio:
                pats = role_players.get(role, [])
                if pats:
                    uid = pats[0].get("id", "")
                    if uid:
                        add_stat(uid, "pat_made")
                    break

        # ── Return TD (punt/kick return, blocked kick return, etc.) ────────
        return_cats = {
            "punt_return", "kick_return", "kickoff_return",
            "missed_fg_return", "blocked_punt_return", "blocked_kick_return",
        }
        return_roles = {"return", "returner"}
        if any(cat in return_cats and res == "touchdown" for cat, res in cat_results):
            for role in return_roles:
                for p in role_players.get(role, []):
                    uid = p.get("id", "")
                    if uid:
                        add_stat(uid, "return_td")

        # ── Defensive TD (interception return, fumble return, etc.) ────────
        def_td_cats = {"interception", "fumble_return", "blocked_punt", "defensive"}
        def_roles   = {"defense", "defensive", "intercept"}
        if any(cat in def_td_cats and res == "touchdown" for cat, res in cat_results):
            for role in def_roles:
                for p in role_players.get(role, []):
                    uid = p.get("id", "")
                    if uid:
                        add_stat(uid, "def_td")

        # ── Safety ─────────────────────────────────────────────────────────
        if any(res == "safety" for _, res in cat_results):
            # Credit the defensive team — no specific player info typically
            pass

    return stat_rows, player_index


# ── Output builders ───────────────────────────────────────────────────────────

def build_game_record(game: dict, periods: list[dict]) -> dict:
    year, season_type, display_week = game_week_info(game)
    dt = (game.get("datetime") or "")
    return {
        "game_id":     canonical_game_id(game),
        "date":        dt.split("T")[0] if dt else "",
        "week":        display_week,
        "season":      year,
        "season_type": season_type,
        "sport_id":    SPORT_ID_BY_YEAR.get(year, 19),
        "league":      LEAGUE,
        "home_team":   game.get("home_team_alias", ""),
        "away_team":   game.get("away_team_alias", ""),
        "home_score":  game.get("home_team_score"),
        "away_score":  game.get("away_team_score"),
        "status":      game.get("status", ""),
        "venue":       game.get("venue_name", ""),
        "broadcast":   game.get("broadcast", ""),
        "periods":     periods,
        "_ufl_game_id": game.get("id"),
        "_ufl_sr_id":   game.get("sr_id", ""),
    }


def build_pbp_record(ufl_game_id: int, gid_str: str,
                     scoring_plays: list[dict], scoring_drives: list[dict]) -> dict:
    return {
        "ufl_game_id":    ufl_game_id,
        "game_id":        gid_str,
        "scoring_plays":  scoring_plays,
        "scoring_drives": scoring_drives,
    }


def assign_synthetic_ids(player_index: dict[str, dict]) -> dict[str, int]:
    """Return {uuid: numeric_id} starting at SYNTHETIC_ID_START."""
    return {uid: SYNTHETIC_ID_START + i for i, uid in enumerate(player_index)}


# ── Main logic ────────────────────────────────────────────────────────────────

def print_status(cache: dict, games: list[dict]) -> None:
    closed   = [g for g in games if g.get("status") == "closed"]
    scraped  = [g for g in closed if str(g["id"]) in cache.get("boxscores", {})]
    pending  = len(closed) - len(scraped)
    print(f"Games : {len(games)} total | {len(closed)} closed | "
          f"{len(scraped)} scraped | {pending} pending")
    by_year: dict[int, int] = {}
    for g in scraped:
        y = game_datetime_year(g)
        by_year[y] = by_year.get(y, 0) + 1
    for y, cnt in sorted(by_year.items()):
        print(f"  {y} : {cnt} games")


def run(args: argparse.Namespace) -> None:
    cache = load_cache()
    games = fetch_game_list(cache, reset=args.reset)

    if args.status:
        print_status(cache, games)
        return

    # Optionally restrict to a single game
    if args.game:
        target = [g for g in games if g.get("id") == args.game]
        if not target:
            sys.exit(f"ERROR: Game ID {args.game} not found in game list")
        games = target

    closed = [g for g in games if g.get("status") == "closed"]
    cached_ids = set(cache.get("boxscores", {}).keys())
    to_fetch   = [g for g in closed if str(g["id"]) not in cached_ids or args.reset]

    if to_fetch:
        print(f"Fetching boxscores for {len(to_fetch)} game(s) ...")
        for i, game in enumerate(to_fetch, 1):
            gid  = game["id"]
            away = game.get("away_team_alias", "?")
            home = game.get("home_team_alias", "?")
            date = (game.get("datetime") or "")[:10]
            print(f"  [{i}/{len(to_fetch)}] #{gid} {away} @ {home} ({date})")
            bs = fetch_boxscore(gid, cache, reset=args.reset)
            if not bs:
                print(f"    WARNING: No boxscore data for game {gid}")
            # Save cache incrementally so progress is preserved on interruption
            if i % 10 == 0:
                save_cache(cache)
        save_cache(cache)
        print("Done fetching.")
    else:
        print(f"All {len(closed)} closed game(s) already cached — use --reset to re-fetch")

    # ── Build output data ─────────────────────────────────────────────────────
    print("Building output files ...")
    game_records: list[dict] = []
    pbp_records:  list[dict] = []
    all_stat_rows: list[dict] = []
    global_player_index: dict[str, dict] = {}

    for game in closed:
        gid    = game["id"]
        bs_raw = cache.get("boxscores", {}).get(str(gid))
        if not bs_raw:
            continue

        game_data = bs_raw.get("data", {})
        vd        = (game_data.get("schedule_event_boxscore") or {}).get("vendor_data") or {}
        ses       = game_data.get("schedule_event_scoring") or {}

        scoring_plays  = vd.get("scoring_plays")  or []
        scoring_drives = vd.get("scoring_drives") or []
        periods        = ses.get("periods")        or []

        game_records.append(build_game_record(game, periods))
        pbp_records.append(build_pbp_record(gid, canonical_game_id(game),
                                            scoring_plays, scoring_drives))

        stat_rows, player_idx = extract_stats_from_scoring_plays(scoring_plays, game)
        all_stat_rows.extend(stat_rows)
        for uid, pd in player_idx.items():
            if uid not in global_player_index:
                global_player_index[uid] = pd

    uid_to_int = assign_synthetic_ids(global_player_index)

    # Finalise player records (add numeric id, drop internal key)
    final_players = []
    for uid, pd in global_player_index.items():
        rec = {k: v for k, v in pd.items() if k != "_ufl_id"}
        rec["id"]      = uid_to_int[uid]
        rec["_ufl_id"] = uid   # keep for future dedup / merge
        final_players.append(rec)
    final_players.sort(key=lambda p: p["id"])

    # Replace _ufl_id placeholder with numeric player_id in stat rows
    final_stats = []
    for row in all_stat_rows:
        uid = row.pop("_ufl_id", None)
        pid = uid_to_int.get(uid)
        if pid is None:
            continue
        row["player_id"] = pid
        final_stats.append(row)

    # Aggregate: sum per (player_id, game_id, stat) so counts are correct
    # (build_data.py dedup takes MAX, so pre-summing here is required)
    agg: dict[tuple, dict] = {}
    for row in final_stats:
        key = (row["player_id"], row["game_id"], row["stat"])
        if key in agg:
            agg[key]["value"] += row["value"]
        else:
            agg[key] = dict(row)
    final_stats = list(agg.values())

    GAMES_FILE.write_text(json.dumps(game_records, indent=2))
    PBP_FILE.write_text(json.dumps(pbp_records, indent=2))
    PLAYERS_FILE.write_text(json.dumps(final_players, indent=2))
    STATS_FILE.write_text(json.dumps(final_stats, indent=2))

    print(f"  {len(game_records):>5} game records  → {GAMES_FILE.name}")
    print(f"  {len(pbp_records):>5} PBP records   → {PBP_FILE.name}")
    print(f"  {len(final_players):>5} players       → {PLAYERS_FILE.name}")
    print(f"  {len(final_stats):>5} stat rows     → {STATS_FILE.name}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Scrape UFL game data + play-by-play from theufl.com API"
    )
    ap.add_argument("--reset",  action="store_true",
                    help="Force re-fetch all data (ignore cache)")
    ap.add_argument("--game",   type=int, metavar="ID",
                    help="Scrape a single game by its UFL API integer ID")
    ap.add_argument("--status", action="store_true",
                    help="Print scrape progress and exit")
    run(ap.parse_args())


if __name__ == "__main__":
    main()

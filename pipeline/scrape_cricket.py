#!/usr/bin/env python3
"""
scrape_cricket.py — Cricket match data via Cricsheet.org

Sources:
  https://cricsheet.org/downloads/{slug}_json.zip  (ball-by-ball match JSON)
  https://cricsheet.org/register/people.csv         (player registry)

Leagues:
  International: t20s (T20I), odis (ODI), tests (Test)
  Domestic T20:  ipl, bbl, wbb (WBBL), psl, mlc, cpl, wpl, bpl, lpl, hnd, ilt, sat, npl

Stats per player per match (from ball-by-ball innings data):
  Batting:  batting_runs, batting_balls, batting_fours, batting_sixes,
            batting_not_out, batting_position
  Bowling:  bowling_balls, bowling_runs, bowling_wickets,
            bowling_wides, bowling_noballs, bowling_maidens
  Fielding: fielding_catches, fielding_runouts
  General:  match_played

Players often appear across many leagues; build_data.py merges them by
the stable Cricsheet person_id (8-char hex).

Outputs (pipeline/raw/):
  cricket_players.json  — player records (IDs start at 1_600_000)
  cricket_stats.json    — per-player-match stat rows
  cricket_games.json    — match metadata rows
  cricket_state.json    — incremental state (id_map, zip_etags, next_id)
"""

import argparse
import csv
import io
import json
import re
import time
import zipfile
from pathlib import Path

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE      = Path(__file__).parent
RAW       = BASE / "raw"
RAW.mkdir(exist_ok=True)
ZIP_CACHE = RAW / "cricket_zips"
ZIP_CACHE.mkdir(exist_ok=True)

STATE_FILE   = RAW / "cricket_state.json"
PLAYERS_FILE = RAW / "cricket_players.json"
STATS_FILE   = RAW / "cricket_stats.json"
GAMES_FILE   = RAW / "cricket_games.json"

# ── Constants ─────────────────────────────────────────────────────────────────
CRICSHEET_BASE     = "https://cricsheet.org/downloads"
PEOPLE_CSV_URL     = "https://cricsheet.org/register/people.csv"
SYNTHETIC_ID_START = 1_600_000

HEADERS = {
    "User-Agent": (
        "AltSportsArchive/1.0 (archive.altfantasysports.com; "
        "educational/archival; contact: altfantasysports.com)"
    )
}

# Cricsheet zip slug → display label used in the pipeline
LEAGUES: dict[str, str] = {
    "t20s":  "T20I",
    "odis":  "ODI",
    "tests": "Tests",
    "ipl":   "IPL",
    "bbl":   "BBL",
    "wbb":   "WBBL",
    "psl":   "PSL",
    "mlc":   "MLC",
    "cpl":   "CPL",
    "wpl":   "WPL",
    "bpl":   "BPL",
    "lpl":   "LPL",
    "hnd":   "HND",
    "ilt":   "ILT20",
    "sat":   "SA20",
    "npl":   "NPL",
}

LEAGUE_LONG: dict[str, str] = {
    "t20s":  "T20 Internationals",
    "odis":  "One-Day Internationals",
    "tests": "Test Matches",
    "ipl":   "Indian Premier League",
    "bbl":   "Big Bash League",
    "wbb":   "Women's Big Bash League",
    "psl":   "Pakistan Super League",
    "mlc":   "Major League Cricket",
    "cpl":   "Caribbean Premier League",
    "wpl":   "Women's Premier League",
    "bpl":   "Bangladesh Premier League",
    "lpl":   "Lanka Premier League",
    "hnd":   "The Hundred",
    "ilt":   "International League T20",
    "sat":   "SA20",
    "npl":   "Nepal Premier League",
}

LEAGUE_GENDER: dict[str, str] = {
    "t20s":  "",    # combined (men + women internationals in one zip)
    "odis":  "",
    "tests": "",
    "ipl":   "M",
    "bbl":   "M",
    "wbb":   "F",
    "psl":   "M",
    "mlc":   "M",
    "cpl":   "M",
    "wpl":   "F",
    "bpl":   "M",
    "lpl":   "M",
    "hnd":   "",    # combined (men + women)
    "ilt":   "M",
    "sat":   "M",
    "npl":   "M",
}


# ── Utilities ─────────────────────────────────────────────────────────────────

def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", str(s)).strip().lower()
    return re.sub(r"[\s_]+", "-", s)


def _parse_season_year(season_str: str) -> int | None:
    """Extract a 4-digit year from a season string like '2024' or '2023/24'."""
    if not season_str:
        return None
    m = re.search(r"\d{4}", str(season_str))
    return int(m.group()) if m else None


# ── People registry ───────────────────────────────────────────────────────────

def _download_people_csv() -> dict[str, dict]:
    """
    Download people.csv and return a dict keyed by Cricsheet person_id (8-char hex).
    Each value has: {name, unique_name, key_cricinfo, ...}
    """
    print("Downloading people.csv …")
    try:
        r = requests.get(PEOPLE_CSV_URL, headers=HEADERS, timeout=60)
        r.raise_for_status()
    except Exception as exc:
        print(f"  Warning: could not download people.csv: {exc}")
        return {}

    registry: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        pid = row.get("identifier", "").strip()
        if pid:
            registry[pid] = {
                "name":            row.get("name", "").strip(),
                "unique_name":     row.get("unique_name", "").strip(),
                "key_cricinfo":    row.get("key_cricinfo", "").strip(),
                "key_cricketarchive": row.get("key_cricketarchive", "").strip(),
            }
    print(f"  Loaded {len(registry)} players from people.csv")
    return registry


# ── Zip download with ETag caching ────────────────────────────────────────────

def _should_download(slug: str, etags: dict[str, str]) -> tuple[bool, str | None]:
    """
    HEAD the remote zip to check Last-Modified/ETag.
    Returns (should_download, etag_or_last_modified).
    """
    url = f"{CRICSHEET_BASE}/{slug}_json.zip"
    try:
        r = requests.head(url, headers=HEADERS, timeout=30, allow_redirects=True)
        if r.status_code != 200:
            print(f"  {slug}: HTTP {r.status_code} — skipping")
            return False, None
        remote_tag = r.headers.get("ETag") or r.headers.get("Last-Modified") or ""
        local_tag  = etags.get(slug, "")
        if local_tag and local_tag == remote_tag:
            return False, remote_tag
        return True, remote_tag
    except Exception as exc:
        print(f"  {slug}: HEAD error: {exc}")
        return False, None


def _download_zip(slug: str) -> Path | None:
    """Download and cache the zip for a league slug. Returns the local path."""
    url      = f"{CRICSHEET_BASE}/{slug}_json.zip"
    out_path = ZIP_CACHE / f"{slug}_json.zip"
    print(f"  Downloading {url} …")
    try:
        r = requests.get(url, headers=HEADERS, timeout=300, stream=True)
        r.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=65536):
                fh.write(chunk)
        size_mb = out_path.stat().st_size / 1_048_576
        print(f"  Saved {out_path.name} ({size_mb:.1f} MB)")
        return out_path
    except Exception as exc:
        print(f"  Error downloading {slug}: {exc}")
        return None


# ── Match parsing ─────────────────────────────────────────────────────────────

def _parse_match(
    match_data: dict,
    file_stem:  str,
    league_label: str,
) -> tuple[list[dict], list[dict], dict | None]:
    """
    Parse a single Cricsheet match JSON.

    Returns:
      persons   : [{person_id, name, gender}]  — players who appeared
      temp_stats: [{_person_id, stat, value, game_id, _year, _league, _team}]
      game      : dict with match metadata, or None if match has no result data
    """
    info = match_data.get("info", {})

    # ── Basic metadata ────────────────────────────────────────────────────────
    teams      = info.get("teams", [])
    dates      = info.get("dates", [])
    match_date = dates[0] if dates else ""
    gender     = info.get("gender", "")          # "male" / "female"
    match_type = info.get("match_type", "")
    season_str = str(info.get("season", ""))
    season_year = _parse_season_year(season_str)
    venue      = info.get("venue", "")
    outcome    = info.get("outcome", {})

    # ── Registry: name → person_id ───────────────────────────────────────────
    registry: dict[str, str] = info.get("registry", {}).get("people", {})
    # name → team mapping from players
    name_to_team: dict[str, str] = {}
    players_by_team: dict[str, list[str]] = info.get("players", {})
    for team, names in players_by_team.items():
        for n in names:
            name_to_team[n] = team

    # ── Game ID & display ─────────────────────────────────────────────────────
    stem_slug = re.sub(r"[^a-z0-9]+", "-", file_stem.lower()).strip("-")
    game_id   = f"cricket-{league_label.lower()}-{match_date}-{stem_slug}"

    team_a = teams[0] if len(teams) > 0 else "TBD"
    team_b = teams[1] if len(teams) > 1 else "TBD"

    # Determine winner / result
    winner = outcome.get("winner", "")
    result_str = outcome.get("result", "")   # "no result", "tie", etc.
    score_a = score_b = None                 # filled later from innings totals

    # ── Innings processing ───────────────────────────────────────────────────
    # Accumulate per-player stats across all innings
    # Keys: person_id_hex or player_name (fallback)
    batting_stats:  dict[str, dict] = {}   # pid → {runs, balls, fours, sixes, out, pos}
    bowling_stats:  dict[str, dict] = {}   # pid → {balls, runs, wkts, wides, nballs}
    fielding_stats: dict[str, dict] = {}   # pid → {catches, runouts}
    bowling_overs:  dict[str, list[int]] = {}  # pid → [over_runs, ...] for maiden calc
    team_scores: dict[str, int] = {t: 0 for t in teams}

    for inning in match_data.get("innings", []):
        batting_team = inning.get("team", "")
        batting_order: list[str] = []   # names in order of first appearance
        current_over_runs: dict[str, int] = {}   # bowler_pid → runs this over

        for over_block in inning.get("overs", []):
            over_num = over_block.get("over", 0)
            # Reset over tracking for maidens
            over_bowlers: dict[str, int] = {}  # bowler_pid → runs this over

            for delivery in over_block.get("deliveries", []):
                batter_name  = delivery.get("batter", "")
                bowler_name  = delivery.get("bowler", "")
                non_striker  = delivery.get("non_striker", "")

                batter_pid  = registry.get(batter_name,  batter_name)
                bowler_pid  = registry.get(bowler_name,  bowler_name)

                runs        = delivery.get("runs", {})
                batter_runs = runs.get("batter", 0)
                extra_runs  = runs.get("extras", 0)
                total_runs  = runs.get("total", 0)
                extras      = delivery.get("extras", {})
                is_wide     = "wides"   in extras
                is_noball   = "noballs" in extras
                bye_runs    = extras.get("byes", 0) + extras.get("legbyes", 0)

                # ── Batting ─────────────────────────────────────
                if batter_name:
                    if batter_pid not in batting_stats:
                        batting_stats[batter_pid] = {
                            "name":  batter_name,
                            "team":  batting_team,
                            "runs":  0, "balls": 0, "fours": 0,
                            "sixes": 0, "out": False, "pos": 0,
                        }
                        batting_stats[batter_pid]["pos"] = len(batting_order) + 1
                        batting_order.append(batter_pid)

                    b = batting_stats[batter_pid]
                    b["runs"] += batter_runs
                    if not is_wide:          # wides don't count as balls faced
                        b["balls"] += 1
                    if batter_runs == 4:
                        b["fours"] += 1
                    elif batter_runs == 6:
                        b["sixes"] += 1

                # ── Wickets ──────────────────────────────────────
                for wicket in delivery.get("wickets", []):
                    kind       = wicket.get("kind", "")
                    player_out = wicket.get("player_out", "")
                    out_pid    = registry.get(player_out, player_out)

                    if out_pid in batting_stats:
                        batting_stats[out_pid]["out"] = True

                    # Bowling wicket (not run out)
                    if kind not in ("run out",):
                        if bowler_pid not in bowling_stats:
                            bowling_stats[bowler_pid] = {
                                "name": bowler_name, "team": name_to_team.get(bowler_name, ""),
                                "balls": 0, "runs": 0, "wkts": 0, "wides": 0, "nballs": 0,
                            }
                        bowling_stats[bowler_pid]["wkts"] += 1

                    # Fielding
                    for fielder in wicket.get("fielders", []):
                        fielder_name = fielder.get("name", "") if isinstance(fielder, dict) else str(fielder)
                        fielder_pid  = registry.get(fielder_name, fielder_name)
                        if fielder_pid not in fielding_stats:
                            fielding_stats[fielder_pid] = {"name": fielder_name, "catches": 0, "runouts": 0}
                        if kind == "caught":
                            fielding_stats[fielder_pid]["catches"] += 1
                        elif kind in ("run out", "stumped"):
                            fielding_stats[fielder_pid]["runouts"] += 1

                # ── Bowling ──────────────────────────────────────
                if bowler_name:
                    if bowler_pid not in bowling_stats:
                        bowling_stats[bowler_pid] = {
                            "name": bowler_name, "team": name_to_team.get(bowler_name, ""),
                            "balls": 0, "runs": 0, "wkts": 0, "wides": 0, "nballs": 0,
                        }
                    bs = bowling_stats[bowler_pid]
                    if is_wide:
                        bs["wides"] += 1
                    elif is_noball:
                        bs["nballs"] += 1
                        bs["balls"] += 0   # no-balls don't count as legal deliveries
                        # still counts as ball faced by batter (handled above)
                    else:
                        bs["balls"] += 1

                    # Runs charged to bowler = total - byes - legbyes
                    bowler_run_charge = total_runs - bye_runs
                    bs["runs"] += bowler_run_charge

                    # Track for maiden calculation
                    if bowler_pid not in over_bowlers:
                        over_bowlers[bowler_pid] = 0
                    over_bowlers[bowler_pid] += bowler_run_charge

                # ── Team scores ──────────────────────────────────
                if batting_team in team_scores:
                    team_scores[batting_team] += total_runs

            # ── End of over: check for maidens ───────────────────
            for bowl_pid, over_run_charge in over_bowlers.items():
                if bowl_pid not in bowling_stats:
                    continue
                bs = bowling_stats[bowl_pid]
                # A maiden: 6 legal balls in over, 0 runs, no wides/noballs
                # (simplified: check only runs and that this bowler bowled 6 legal balls)
                # We track per-over separately using bowling_overs
                if bowl_pid not in bowling_overs:
                    bowling_overs[bowl_pid] = []
                bowling_overs[bowl_pid].append(over_run_charge)

    # ── Compute maidens ───────────────────────────────────────────────────────
    for pid, over_charges in bowling_overs.items():
        if pid in bowling_stats:
            bowling_stats[pid]["maidens"] = sum(1 for r in over_charges if r == 0)

    # ── Team scores for game row ──────────────────────────────────────────────
    if len(teams) >= 2:
        score_a = team_scores.get(team_a)
        score_b = team_scores.get(team_b)

    # ── Build game row ────────────────────────────────────────────────────────
    by = outcome.get("by", {})
    result_detail = ""
    if winner:
        if "runs" in by:
            result_detail = f"{winner} won by {by['runs']} runs"
        elif "wickets" in by:
            result_detail = f"{winner} won by {by['wickets']} wickets"
        elif "innings" in by and "runs" in by.get("innings", {}):
            result_detail = f"{winner} won by an innings and {by['innings']['runs']} runs"
        else:
            result_detail = f"{winner} won"
    elif result_str:
        result_detail = result_str

    game = {
        "id":          f"cricket-{league_label.lower()}-{stem_slug}",
        "sport_id":    None,
        "status":      "Final" if (winner or result_str) else "Unknown",
        "start_time":  match_date,
        "team_home":   team_b,   # second team listed = home (convention)
        "team_away":   team_a,
        "created_at":  None,
        "updated_at":  None,
        "week":        None,
        "game_id":     game_id,
        "score_home":  score_b,
        "score_away":  score_a,
        "channel":     "",
        "streaming_link": None,
        "period":      None,
        "time_left":   None,
        "active":      0,
        "possession_home": None,
        "possession_away": None,
        "record_home": None,
        "record_away": None,
        "status_line": result_detail,
        "spread_home": None,
        "spread_away": None,
        "moneyline_home": None,
        "moneyline_away": None,
        "total_home":  None,
        "total_away":  None,
        "league":      league_label,
        "venue":       venue,
        "match_type":  match_type,
        "gender":      gender,
        "season":      season_str,
    }

    # ── Collect person records ────────────────────────────────────────────────
    all_pids = (
        set(batting_stats.keys()) |
        set(bowling_stats.keys()) |
        set(fielding_stats.keys())
    )
    persons: list[dict] = []
    for pid in all_pids:
        name = (
            batting_stats.get(pid, {}).get("name") or
            bowling_stats.get(pid, {}).get("name") or
            fielding_stats.get(pid, {}).get("name") or
            pid
        )
        team = (
            batting_stats.get(pid, {}).get("team") or
            bowling_stats.get(pid, {}).get("team") or
            name_to_team.get(name, "")
        )
        persons.append({
            "_person_id": pid,
            "name":       name,
            "gender":     gender,
            "team":       team,
        })

    # ── Build temp stat rows ──────────────────────────────────────────────────
    temp_stats: list[dict] = []

    def _emit(pid: str, team: str, stat_name: str, val: float):
        temp_stats.append({
            "_person_id": pid,
            "_team":      team,
            "stat":       stat_name,
            "value":      float(val),
            "game_id":    game_id,
            "_year":      season_year,
            "_league":    league_label,
        })

    for pid in all_pids:
        team = (
            batting_stats.get(pid, {}).get("team") or
            bowling_stats.get(pid, {}).get("team") or
            fielding_stats.get(pid, {}).get("name") or
            ""
        )
        team = (
            batting_stats.get(pid, {}).get("team") or
            bowling_stats.get(pid, {}).get("team") or
            name_to_team.get(
                batting_stats.get(pid, {}).get("name") or
                bowling_stats.get(pid, {}).get("name") or "", ""
            )
        )
        _emit(pid, team, "match_played", 1)

        if pid in batting_stats:
            b = batting_stats[pid]
            _emit(pid, team, "batting_runs",     b["runs"])
            _emit(pid, team, "batting_balls",    b["balls"])
            _emit(pid, team, "batting_fours",    b["fours"])
            _emit(pid, team, "batting_sixes",    b["sixes"])
            _emit(pid, team, "batting_not_out",  0 if b["out"] else 1)
            _emit(pid, team, "batting_position", b["pos"])

        if pid in bowling_stats:
            bs = bowling_stats[pid]
            if bs["balls"] > 0 or bs["wides"] > 0 or bs["nballs"] > 0:
                _emit(pid, team, "bowling_balls",    bs["balls"])
                _emit(pid, team, "bowling_runs",     bs["runs"])
                _emit(pid, team, "bowling_wickets",  bs["wkts"])
                _emit(pid, team, "bowling_wides",    bs["wides"])
                _emit(pid, team, "bowling_noballs",  bs["nballs"])
                _emit(pid, team, "bowling_maidens",  bs.get("maidens", 0))

        if pid in fielding_stats:
            fs = fielding_stats[pid]
            if fs["catches"] > 0:
                _emit(pid, team, "fielding_catches",  fs["catches"])
            if fs["runouts"] > 0:
                _emit(pid, team, "fielding_runouts",  fs["runouts"])

    return persons, temp_stats, game


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Scrape cricket data from Cricsheet.org")
    ap.add_argument("--reset", action="store_true",
                    help="Clear all state and output files; re-process everything")
    ap.add_argument("--leagues", nargs="+", choices=list(LEAGUES.keys()),
                    default=list(LEAGUES.keys()),
                    help="Which leagues to process (default: all)")
    ap.add_argument("--force", action="store_true",
                    help="Re-download and reprocess even if zip hasn't changed")
    ap.add_argument("--status", action="store_true",
                    help="Print status and exit")
    ap.add_argument("--skip-download", action="store_true",
                    help="Use cached zip files only, don't check for updates")
    args = ap.parse_args()

    # ── Load state ────────────────────────────────────────────────────────────
    state: dict = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}

    if args.reset:
        state         = {}
        players_out   = []
        stats_out     = []
        games_out     = []
    else:
        players_out = load_json(PLAYERS_FILE, [])
        stats_out   = load_json(STATS_FILE,   [])
        games_out   = load_json(GAMES_FILE,   [])

    id_map:     dict[str, int] = state.get("id_map",     {})
    zip_etags:  dict[str, str] = state.get("zip_etags",  {})
    done_zips:  set[str]       = set(state.get("done_zips", []))
    next_id:    int            = state.get("next_id", SYNTHETIC_ID_START)
    people_reg_loaded: bool    = state.get("people_loaded", False)

    if args.status:
        print("Cricket scraper status:")
        print(f"  Done zips   : {sorted(done_zips)}")
        print(f"  Players     : {len(players_out)}")
        print(f"  Stat rows   : {len(stats_out)}")
        print(f"  Games       : {len(games_out)}")
        print(f"  Next ID     : {next_id}")
        return

    # ── People registry ───────────────────────────────────────────────────────
    people_registry: dict[str, dict] = load_json(RAW / "cricket_people.json", {})
    if not people_registry or args.reset:
        people_registry = _download_people_csv()
        save_json(RAW / "cricket_people.json", people_registry)
        state["people_loaded"] = True

    # person_id → canonical name
    person_name: dict[str, str] = {
        pid: info.get("unique_name") or info.get("name") or pid
        for pid, info in people_registry.items()
    }

    # ── Build dedup indices ───────────────────────────────────────────────────
    player_index:    dict[int, dict] = {p["id"]: p for p in players_out}
    stored_game_ids: set[str]        = {str(g.get("game_id", "")) for g in games_out if g.get("game_id")}
    # For stats: key = (player_id, game_id, stat)
    stored_stat_keys: set[tuple] = {
        (r["player_id"], r["game_id"], r["stat"]) for r in stats_out
    }

    def _ensure_id(person_id_hex: str) -> int:
        nonlocal next_id
        if person_id_hex not in id_map:
            id_map[person_id_hex] = next_id
            next_id += 1
        return id_map[person_id_hex]

    def _flush():
        save_json(PLAYERS_FILE, players_out)
        save_json(STATS_FILE,   stats_out)
        save_json(GAMES_FILE,   games_out)
        state["id_map"]       = id_map
        state["zip_etags"]    = zip_etags
        state["done_zips"]    = sorted(done_zips)
        state["next_id"]      = next_id
        state["people_loaded"] = True
        save_json(STATE_FILE, state)

    # ── Process each league ───────────────────────────────────────────────────
    for slug in args.leagues:
        label    = LEAGUES[slug]
        long_name = LEAGUE_LONG[slug]
        print(f"\n── {long_name} ({slug}) ──────────────────────────────")

        # Check if we need to (re-)download the zip
        zip_path = ZIP_CACHE / f"{slug}_json.zip"

        if args.force or not zip_path.exists():
            need_download = True
            remote_tag    = None
        elif args.skip_download:
            need_download = False
            remote_tag    = zip_etags.get(slug)
        else:
            need_download, remote_tag = _should_download(slug, zip_etags)

        if need_download:
            dl_path = _download_zip(slug)
            if dl_path is None:
                print(f"  Skipping {slug} — download failed.")
                continue
            if remote_tag:
                zip_etags[slug] = remote_tag
            # Reset done status so we reprocess
            done_zips.discard(slug)
        else:
            if not zip_path.exists():
                print(f"  No local zip for {slug} and skip-download set — skipping.")
                continue
            print(f"  Using cached {zip_path.name} (no remote changes)")
            if slug in done_zips and not args.force:
                print(f"  Already processed — skip (use --force to reprocess)")
                continue

        # ── Process zip contents ──────────────────────────────────────────────
        print(f"  Processing {zip_path.name} …")
        n_matches = 0
        n_persons = 0
        n_stats   = 0
        n_games   = 0
        n_errors  = 0

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                json_names = [n for n in zf.namelist() if n.endswith(".json")]
                total = len(json_names)
                print(f"  {total} match files found")

                for i, fname in enumerate(json_names):
                    if i > 0 and i % 500 == 0:
                        print(f"    … {i}/{total} processed, flushing …")
                        _flush()

                    file_stem = Path(fname).stem

                    # Build game_id to check for duplicates before full parse
                    # We don't know the date yet, so check after parse
                    try:
                        raw = zf.read(fname)
                        match_data = json.loads(raw)
                    except Exception as exc:
                        n_errors += 1
                        continue

                    # Quick date check for game_id (need to parse first)
                    info = match_data.get("info", {})
                    dates = info.get("dates", [])
                    match_date = dates[0] if dates else ""
                    stem_slug = re.sub(r"[^a-z0-9]+", "-", file_stem.lower()).strip("-")
                    game_id = f"cricket-{label.lower()}-{match_date}-{stem_slug}"

                    if game_id in stored_game_ids and not args.force:
                        continue

                    try:
                        persons, temp_stats, game = _parse_match(match_data, file_stem, label)
                    except Exception as exc:
                        n_errors += 1
                        if n_errors <= 5:
                            print(f"    Parse error in {fname}: {exc}")
                        continue

                    if game is None:
                        continue

                    # ── Register players ──────────────────────────────────────
                    for person in persons:
                        pid_hex = person["_person_id"]
                        num_id  = _ensure_id(pid_hex)

                        if num_id not in player_index:
                            # Canonical name: prefer people.csv, fall back to match name
                            canon_name = person_name.get(pid_hex, "") or person["name"]
                            gender_val = person.get("gender", "")
                            gender_code = "M" if gender_val == "male" else ("F" if gender_val == "female" else LEAGUE_GENDER.get(slug, ""))

                            rec = {
                                "id":           num_id,
                                "full_name":    canon_name,
                                "team":         person.get("team", ""),
                                "position":     "",
                                "sport_id":     None,
                                "league":       label,
                                "jersey":       None,
                                "college":      None,
                                "college_stats": None,
                                "height":       None,
                                "weight":       None,
                                "birth_date":   "",
                                "nationality":  "",
                                "gender":       gender_code,
                                "_cricsheet_id": pid_hex,
                                "_leagues":     [label],
                                "_cricinfo":    people_registry.get(pid_hex, {}).get("key_cricinfo", ""),
                            }
                            player_index[num_id] = rec
                            players_out.append(rec)
                            n_persons += 1
                        else:
                            ex = player_index[num_id]
                            ex.setdefault("_leagues", [])
                            if label not in ex["_leagues"]:
                                ex["_leagues"].append(label)

                    # ── Store stat rows ──────────────────────────────────────
                    for ts in temp_stats:
                        pid_hex = ts["_person_id"]
                        num_id  = _ensure_id(pid_hex)
                        key     = (num_id, ts["game_id"], ts["stat"])
                        if key in stored_stat_keys and not args.force:
                            continue
                        stats_out.append({
                            "player_id": num_id,
                            "week":      1,
                            "stat":      ts["stat"],
                            "value":     ts["value"],
                            "game_id":   ts["game_id"],
                            "_year":     ts["_year"],
                            "_league":   ts["_league"],
                            "_team_id":  _slugify(ts.get("_team", "")),
                        })
                        stored_stat_keys.add(key)
                        n_stats += 1

                    # ── Store game ────────────────────────────────────────────
                    if game["game_id"] not in stored_game_ids or args.force:
                        games_out.append(game)
                        stored_game_ids.add(game["game_id"])
                        n_games += 1

                    n_matches += 1

        except zipfile.BadZipFile as exc:
            print(f"  Bad zip for {slug}: {exc}")
            continue

        if n_errors > 5:
            print(f"  … and {n_errors - 5} more parse errors")

        done_zips.add(slug)
        _flush()
        print(
            f"  Processed {n_matches} matches → "
            f"{n_persons} new players, {n_stats} stat rows, {n_games} games. "
            f"({n_errors} errors)"
        )
        print(
            f"  Running total: {len(players_out)} players, "
            f"{len(stats_out)} stat rows, {len(games_out)} games"
        )

    print(
        f"\nDone. {len(players_out)} players, {len(stats_out)} stat rows, "
        f"{len(games_out)} games across {len(done_zips)} leagues."
    )


if __name__ == "__main__":
    main()

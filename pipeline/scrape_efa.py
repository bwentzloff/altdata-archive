"""
scrape_efa.py
Fetches EFA (European Football Alliance) player stats from the league's
stats backend (clubee.com) and builds the pipeline/raw/efa_*.json files.

Source:
  https://efafootball.com/  (website front-end, no usable data)
  https://apiv3.clubee.com  (Clubee REST API hosting EFA stats)

The official EFA website embeds clubee.com via iframe. clubee.com exposes a
public JSON API for the league at apiv3.clubee.com. Authentication is via
a single static header (`X-Website: europeanfootballallianceefa`); no
bearer token is required for the public competition/stats endpoints.

Endpoints used:
  /competitions/{COMPETITION_ID}/stats/categories
  /competitions/{COMPETITION_ID}/seasons/{SEASON_ID}/stats/categories/{cat}/players
  /competitions/{COMPETITION_ID}/seasons/{SEASON_ID}/scenes   (games)

The five player-level stat categories (13 Passing, 14 Receiving,
15 Rushing, 16 Defense, 17 Returns) are merged per player into a single
season-total row.

Synthetic player IDs start at 320000 to avoid collisions with the ELF
historical synthetic range (300000+).
Game ID format for the season-total stub: FOOTBALL_EFA_{year}_SEASON_TOTAL
"""

import json
import sys
import time
from pathlib import Path

import requests

PIPELINE = Path(__file__).parent
RAW = PIPELINE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "efa_players.json"
STATS_FILE   = RAW / "efa_stats.json"
GAMES_FILE   = RAW / "efa_games.json"
CACHE_DIR    = RAW / "efa_cache"
CACHE_DIR.mkdir(exist_ok=True)

BASE_URL       = "https://apiv3.clubee.com"
COMPETITION_ID = 17083            # EFA competition (league) id
SEASON_ID      = 217              # 2026 season id (current/only season)
YEAR           = 2026
LEAGUE_NAME    = "EFA"

SYNTHETIC_ID_START = 320000

HEADERS = {
    "X-Website":  "europeanfootballallianceefa",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ── Per-category stat field map ────────────────────────────────────────────
# Each clubee.com stat field is named `statNNN`. Map only the cumulative /
# non-derived stats; rates (CMP%, Y/A, Passer Rating, etc.) are skipped
# since they are recomputed downstream from totals.
CATEGORY_STAT_MAPS = {
    13: {   # Passing
        "stat103": "gp",
        "stat154": "pass_att",
        "stat155": "pass_cmp",
        "stat156": "pass_yds",
        "stat105": "pass_td",
        "stat181": "pass_int",
        "stat182": "pass_sacked",
    },
    14: {   # Receiving
        "stat103": "gp",
        "stat184": "recv_rec",
        "stat185": "recv_tgt",
        "stat187": "recv_yds",
        "stat189": "recv_td",
    },
    15: {   # Rushing
        "stat103": "gp",
        "stat157": "rush_att",
        "stat158": "rush_yds",
        "stat104": "rush_td",
        "stat47":  "fumbles",
    },
    16: {   # Defense
        "stat103": "gp",
        "stat190": "def_tackles",
        "stat191": "def_tackles_solo",
        "stat192": "def_tackles_ast",
        "stat193": "def_tfl",
        "stat147": "def_sacks",
        "stat134": "def_int",
        "stat194": "def_pd",
        "stat106": "def_td",
    },
    17: {   # Returns
        "stat103": "gp",
        "stat195": "kr_yds",
        "stat108": "kr_td",
        "stat196": "pr_yds",
        "stat107": "pr_td",
    },
}

CATEGORY_IDS = list(CATEGORY_STAT_MAPS.keys())


def safe_float(v):
    if v is None or v == "":
        return 0.0
    try:
        f = float(v)
        return 0.0 if (f != f) else f
    except (TypeError, ValueError):
        return 0.0


def infer_position(row: dict) -> str:
    pa   = safe_float(row.get("pass_att"))
    ra   = safe_float(row.get("rush_att"))
    rcv  = safe_float(row.get("recv_rec"))
    tack = safe_float(row.get("def_tackles"))
    kr   = safe_float(row.get("kr_yds"))
    pr   = safe_float(row.get("pr_yds"))

    if pa >= 5:
        return "QB"
    if ra >= 10 and pa < 5:
        return "RB"
    if rcv >= 5 and ra < 5 and pa < 5:
        return "WR"
    if tack >= 5 and ra < 5:
        return "LB"
    if (kr + pr) >= 50 and rcv < 5 and ra < 5:
        return "RS"
    return ""


def fetch_json(path: str, cache_name: str) -> dict | list:
    """GET a JSON endpoint with on-disk cache."""
    cache = CACHE_DIR / cache_name
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    url = f"{BASE_URL}{path}"
    print(f"  GET {url}")
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    cache.write_text(r.text, encoding="utf-8")
    time.sleep(0.75)
    return r.json()


def fetch_category(cat_id: int) -> dict:
    return fetch_json(
        f"/competitions/{COMPETITION_ID}/seasons/{SEASON_ID}/stats/categories/{cat_id}/players",
        f"cat_{cat_id}.json",
    )


def fetch_scenes() -> list:
    return fetch_json(
        f"/competitions/{COMPETITION_ID}/seasons/{SEASON_ID}/scenes",
        "scenes.json",
    )


def merge_players() -> dict[int, dict]:
    """Fetch all player categories and merge per-user.id into one row each."""
    merged: dict[int, dict] = {}

    for cat_id in CATEGORY_IDS:
        payload = fetch_category(cat_id)
        rows = payload.get("rows") or []
        stat_map = CATEGORY_STAT_MAPS[cat_id]
        print(f"  cat {cat_id}: {len(rows)} rows")

        for row in rows:
            user = row.get("user") or {}
            uid = user.get("id")
            if not uid:
                continue
            group = row.get("group") or {}
            stats = row.get("stats") or {}

            tgt = merged.setdefault(uid, {
                "user_id":   uid,
                "first":     (user.get("firstname") or "").strip(),
                "last":      (user.get("lastname") or "").strip(),
                "team":      (group.get("name") or "").strip(),
                "team_id":   group.get("id"),
            })
            # Prefer non-empty identity fields
            if not tgt["team"] and group.get("name"):
                tgt["team"] = group["name"].strip()

            for sf, name in stat_map.items():
                v = safe_float(stats.get(sf))
                if name == "gp":
                    tgt["gp"] = max(safe_float(tgt.get("gp")), v)
                elif v:
                    tgt[name] = safe_float(tgt.get(name)) + v

    return merged


def build_outputs(merged: dict[int, dict]):
    # Preserve synthetic IDs across runs
    prior_by_uid: dict[int, dict] = {}
    next_id = SYNTHETIC_ID_START
    if PLAYERS_FILE.exists():
        try:
            for p in json.loads(PLAYERS_FILE.read_text()):
                uid = p.get("_efa_user_id")
                pid = p.get("id")
                if isinstance(uid, int) and isinstance(pid, int):
                    prior_by_uid[uid] = p
                    if pid >= next_id:
                        next_id = pid + 1
            print(f"Preserving {len(prior_by_uid)} existing synthetic EFA IDs; "
                  f"next new id = {next_id}")
        except Exception as e:
            print(f"Could not load prior {PLAYERS_FILE.name}: {e}")

    out_players: list[dict] = []
    out_stats:   list[dict] = []
    game_id = f"FOOTBALL_EFA_{YEAR}_SEASON_TOTAL"
    # Stat field names produced by the merge (everything except identity / gp)
    _identity_keys = {"user_id", "first", "last", "team", "team_id", "gp"}

    for uid, row in sorted(merged.items()):
        prior = prior_by_uid.get(uid)
        if prior:
            syn_id = prior["id"]
            # Carry forward the prior record; refresh team if changed
            player = dict(prior)
            if row["team"] and row["team"] != player.get("team"):
                player["team"] = row["team"]
            # Position may improve once stats accumulate
            if not player.get("position"):
                player["position"] = infer_position(row)
        else:
            syn_id = next_id
            next_id += 1
            full_name = (f"{row['first']} {row['last']}").strip()
            player = {
                "id":          syn_id,
                "full_name":   full_name,
                "short_name":  full_name,
                "first_name":  row["first"],
                "last_name":   row["last"],
                "sport_id":    None,
                "league":      LEAGUE_NAME,
                "team":        row["team"],
                "position":    infer_position(row),
                "_efa_historical": True,
                "_efa_user_id":    uid,
                "_efa_team_id":    row.get("team_id"),
                "_norm_name":  full_name.lower(),
                "sportradar_id": None,
                "college":     None,
                "jersey":      None,
                "height":      None,
                "weight":      None,
            }
        out_players.append(player)

        # Emit one stat row per accumulated stat (incl. gp if > 0)
        gp = safe_float(row.get("gp"))
        if gp > 0:
            out_stats.append({
                "player_id": syn_id,
                "week":      1,
                "stat":      "gp",
                "value":     gp,
                "game_id":   game_id,
                "_year":     YEAR,
            })
        for k, v in row.items():
            if k in _identity_keys:
                continue
            v = safe_float(v)
            if v:
                out_stats.append({
                    "player_id": syn_id,
                    "week":      1,
                    "stat":      k,
                    "value":     v,
                    "game_id":   game_id,
                    "_year":     YEAR,
                })

    PLAYERS_FILE.write_text(json.dumps(out_players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(out_stats,   indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name}: {len(out_players)} players")
    print(f"Wrote {STATS_FILE.name}: {len(out_stats)} stat rows")


def build_games(scenes: list):
    """Convert clubee 'scenes' into the pipeline games.json row shape."""
    out_games = []
    for sc in scenes:
        gid = sc.get("id")
        if gid is None:
            continue
        t1 = sc.get("team1") or {}
        t2 = sc.get("team2") or {}
        completed = bool(sc.get("completed"))
        row = {
            "game_id":       f"FOOTBALL_EFA_{gid}",
            "sport_id":      None,
            "league":        LEAGUE_NAME,
            "season":        YEAR,
            "_year":         YEAR,
            "week":          sc.get("game_day"),
            "start_date":    sc.get("start_date"),
            "home_team":     t1.get("name"),
            "away_team":     t2.get("name"),
            "home_score":    sc.get("score1") if completed else None,
            "away_score":    sc.get("score2") if completed else None,
            "completed":     completed,
            "venue":         sc.get("venue_name"),
            "_efa_scene_id": gid,
        }
        out_games.append(row)
    GAMES_FILE.write_text(json.dumps(out_games, indent=2), encoding="utf-8")
    print(f"Wrote {GAMES_FILE.name}: {len(out_games)} games "
          f"({sum(1 for g in out_games if g['completed'])} completed)")


def main():
    print("=== EFA (European Football Alliance) Stats Import ===")
    try:
        merged = merge_players()
    except requests.HTTPError as e:
        sys.exit(f"ERROR: HTTP failure fetching EFA stats: {e}")
    if not merged:
        sys.exit("ERROR: no EFA player rows fetched")
    print(f"Unique EFA players across all categories: {len(merged)}")
    build_outputs(merged)

    try:
        scenes = fetch_scenes()
        if isinstance(scenes, list):
            build_games(scenes)
    except requests.HTTPError as e:
        print(f"  warn: could not fetch scenes: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()

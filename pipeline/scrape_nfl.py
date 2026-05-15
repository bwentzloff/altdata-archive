"""
scrape_nfl.py — Incrementally fetch career NFL stats via the ESPN public API.

For each canonical player in players_merged.json, searches the ESPN search API
to find their NFL athlete ID, then fetches career statistics.  Counting stats
only — rates, averages, and ESPN proprietary ratings are filtered out.

The NFL is treated as a "special historical section" on player pages (like
college stats), NOT as a league in the main stat system.

Outputs  (pipeline/raw/, all gitignored):
  nfl_stats_raw.json    — {canonical_id: {espn_id, name, stats: {key: val}}}
  nfl_scrape_state.json — progress tracking

Usage:
  python pipeline/scrape_nfl.py              # process next 30 players (default)
  python pipeline/scrape_nfl.py --batch 50
    python pipeline/scrape_nfl.py --retry-every 14 --retry-batch 10
  python pipeline/scrape_nfl.py --status     # print progress without fetching
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests

# ── ESPN API endpoints ────────────────────────────────────────────────────────
SEARCH_URL = "https://site.api.espn.com/apis/search/v2"
# Per-season splits, one row per (year, team).
STATS_URL = (
    "https://site.web.api.espn.com"
    "/apis/common/v3/sports/football/nfl/athletes/{id}/stats"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
DELAY = 1.0  # seconds between requests

# ── Paths ─────────────────────────────────────────────────────────────────────
RAW_DIR    = Path(__file__).parent / "raw"
MERGED     = Path(__file__).parent / "merged"
STATE_FILE = RAW_DIR / "nfl_scrape_state.json"
STATS_FILE = RAW_DIR / "nfl_stats_raw.json"

RAW_DIR.mkdir(exist_ok=True)

# ── Stat filtering ────────────────────────────────────────────────────────────
# Categories to capture from the ESPN splits response
KEEP_CATEGORIES = {
    "passing",
    "rushing",
    "receiving",
    "defensive",
    "defensiveInterceptions",
    "kicking",
    "punting",
    "general",
}

# Short prefix per category — used to namespace stat keys and avoid collisions
# e.g. both "passing" and "defensiveInterceptions" have a stat named "interceptions"
CAT_PREFIX = {
    "passing":                "p",
    "rushing":                "r",
    "receiving":              "re",
    "defensive":              "d",
    "defensiveInterceptions": "di",
    "kicking":                "k",
    "punting":                "pn",
    "general":                "g",
    "scoring":                "sc",
}

# Skip any stat whose name (lowercased) contains one of these substrings —
# this removes rates, averages, ESPN proprietary ratings, and "longest play" stats
SKIP_SUBSTRINGS = frozenset({
    "pct", "avg", "rate", "rating", "long", "espn", "ratio",
    "grade", "rank", "misc", "percent", "pergame", "perg",
})

FOOTBALL_LEAGUES = {
    "NFL", "CFL", "USFL", "XFL", "UFL", "AAF", "AFL", "IFL", "MLFB",
    "ELF", "AF1", "FCF", "NAL", "LFA", "X-LEAGUE",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _should_skip(stat_name: str) -> bool:
    n = stat_name.lower()
    return any(p in n for p in SKIP_SUBSTRINGS)


def _is_football_candidate(player: dict) -> bool:
    sport_names = {
        str(s or "").upper().strip()
        for s in (player.get("sport_names") or [])
    }
    if sport_names & FOOTBALL_LEAGUES:
        return True

    # Fallback for records where sport_names may be sparse.
    for app in (player.get("appearances") or []):
        sport = str(app.get("sport") or "").upper().strip()
        if sport in FOOTBALL_LEAGUES:
            return True
    return False


# ── ESPN API calls ────────────────────────────────────────────────────────────

def search_player(name: str) -> int | None:
    """
    Search ESPN for an NFL player by name.
    Returns ESPN athlete ID if exactly one player result is found, else None.
    Multiple results means ambiguous — we skip rather than guess.
    """
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"query": name, "limit": 10, "section": "nfl"},
            headers=HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    # Collect player-type contents from the results list
    player_contents = []
    for result_group in data.get("results", []):
        if result_group.get("type") == "player":
            player_contents.extend(result_group.get("contents", []))

    if len(player_contents) != 1:
        return None  # 0 = not found, 2+ = ambiguous name

    uid = player_contents[0].get("uid", "")
    m = re.search(r"a:(\d+)", uid)
    return int(m.group(1)) if m else None


def fetch_player_seasons(espn_id: int) -> dict:
    """
    Fetch per-season NFL stats for an ESPN athlete.

    Returns:
      {
        "seasons": [
          {"year": int, "team_id": str, "team_slug": str, "position": str,
           "stats": {nfl_<prefix>_<name>: float, ...}},
          ...
        ],
        "totals": {nfl_<prefix>_<name>: float, ...},   # summed across seasons
      }

    Empty dict on failure or no usable rows.
    """
    url = STATS_URL.format(id=espn_id)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return {}
        data = resp.json()
    except Exception:
        return {}

    season_rows: list[dict] = []
    totals: dict[str, float] = {}

    categories = data.get("categories", []) or []
    # Aggregate all categories into a per-season-and-team dict so passing,
    # rushing, etc. for the same season+team merge into one row.
    bucket: dict[tuple[int, str, str], dict] = {}

    for cat in categories:
        cat_name = cat.get("name", "")
        if cat_name not in KEEP_CATEGORIES:
            continue
        prefix = CAT_PREFIX.get(cat_name, cat_name[:2])
        names = cat.get("names", []) or []
        for row in (cat.get("statistics", []) or []):
            year = (row.get("season") or {}).get("year")
            if year is None:
                continue
            try:
                year = int(year)
            except (TypeError, ValueError):
                continue
            team_id   = str(row.get("teamId") or "")
            team_slug = row.get("teamSlug") or ""
            position  = row.get("position") or ""
            raw_stats = row.get("stats") or []

            key = (year, team_id, team_slug)
            entry = bucket.setdefault(key, {
                "year": year,
                "team_id": team_id,
                "team_slug": team_slug,
                "position": position,
                "stats": {},
            })
            # Prefer a non-empty position when one becomes available.
            if position and not entry["position"]:
                entry["position"] = position

            for sname, sval in zip(names, raw_stats):
                if not sname or _should_skip(sname):
                    continue
                try:
                    fval = float(sval)
                except (TypeError, ValueError):
                    continue
                if fval == 0.0:
                    continue
                stat_key = f"nfl_{prefix}_{sname}"
                entry["stats"][stat_key] = entry["stats"].get(stat_key, 0.0) + fval
                totals[stat_key] = totals.get(stat_key, 0.0) + fval

    season_rows = sorted(
        bucket.values(),
        key=lambda r: (r["year"], r["team_slug"]),
    )
    if not season_rows:
        return {}
    return {"seasons": season_rows, "totals": totals}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--batch", type=int, default=30,
        help="Players to look up this run (default: 30)",
    )
    parser.add_argument(
        "--retry-every", type=int, default=14,
        help="Re-try previously not-found players every N runs (default: 14)",
    )
    parser.add_argument(
        "--retry-batch", type=int, default=10,
        help="Maximum number of retry attempts to include per run (default: 10)",
    )
    parser.add_argument(
        "--refetch-batch", type=int, default=50,
        help=(
            "Players already found whose payload is missing per-season data "
            "to refetch this run (default: 50). Refetch reuses the known ESPN "
            "id, so it costs one HTTP call per player instead of two."
        ),
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print progress without fetching",
    )
    args = parser.parse_args()

    players_all = _load_json(MERGED / "players_merged.json", [])
    players = [p for p in players_all if _is_football_candidate(p)]
    valid_cids = {p["canonical_id"] for p in players}
    state     = _load_json(STATE_FILE, {
        "searched": [],
        "not_found": [],
        "not_found_meta": {},
        "run_counter": 0,
    })
    nfl_stats = _load_json(STATS_FILE, {})

    # Normalize legacy all-sport state/data to football-only IDs.
    nfl_stats = {
        cid: rec
        for cid, rec in nfl_stats.items()
        if cid in valid_cids
    }

    searched_set  = set(state.get("searched", [])) & valid_cids
    # Backward-compatible load: old state stored not_found as a list only.
    not_found_meta = state.get("not_found_meta", {}) or {}
    legacy_not_found = set(state.get("not_found", [])) & valid_cids
    if legacy_not_found and not not_found_meta:
        not_found_meta = {
            cid: {"attempts": 1, "last_run": 0}
            for cid in legacy_not_found
        }
    not_found_meta = {
        cid: meta
        for cid, meta in not_found_meta.items()
        if cid in valid_cids
    }
    not_found_set = set(not_found_meta.keys()) | legacy_not_found

    run_counter = int(state.get("run_counter", 0)) + 1

    # Players we haven't attempted yet
    remaining = [
        p for p in players
        if p["canonical_id"] not in searched_set
    ]

    players_by_cid = {p["canonical_id"]: p for p in players}
    main_complete = len(remaining) == 0

    # Players already found but stored without per-season data — refetch using
    # their known ESPN id (no search call needed). This lets the upgraded
    # endpoint backfill historical players without re-searching them.
    refetch_eligible = []
    for cid, rec in nfl_stats.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("seasons"):
            continue
        if not rec.get("espn_id"):
            continue
        p = players_by_cid.get(cid)
        if not p:
            continue
        refetch_eligible.append(p)
    refetch_batch = refetch_eligible[: max(0, args.refetch_batch)]

    retry_eligible = []
    if main_complete:
        for cid in sorted(not_found_set):
            p = players_by_cid.get(cid)
            if not p:
                continue
            meta = not_found_meta.get(cid, {"attempts": 1, "last_run": 0})
            last_run = int(meta.get("last_run", 0) or 0)
            if (run_counter - last_run) >= max(1, args.retry_every):
                retry_eligible.append(p)

        retry_eligible = retry_eligible[: max(0, args.retry_batch)]

    total       = len(players)
    done_count  = len(searched_set)
    found_count = len(nfl_stats)
    pct = 100 * done_count // total if total else 0

    print(
        f"NFL index: {done_count}/{total} searched ({pct}%)  "
        f"— {found_count} players with stats, "
        f"{len(not_found_set)} not found, "
        f"{len(remaining)} remaining, "
        f"{len(refetch_eligible)} need per-season backfill, "
        f"{len(retry_eligible)} retry-eligible"
    )
    if not main_complete and not_found_set:
        print("Retries are paused until initial search reaches 100% coverage.")

    # Persist normalized football-only state/data even on status-only runs.
    state["searched"] = sorted(searched_set)
    state["not_found"] = sorted(not_found_set)
    state["not_found_meta"] = not_found_meta
    state["run_counter"] = run_counter
    _save_json(STATE_FILE, state)
    _save_json(STATS_FILE, nfl_stats)

    if args.status or (not remaining and not retry_eligible and not refetch_batch):
        if not remaining and not retry_eligible and not refetch_batch:
            print("All players searched, backfilled, and no retries are due.")
        return

    fresh_batch = remaining[: args.batch]
    batch = fresh_batch + retry_eligible
    print(
        f"Looking up {len(batch)} players "
        f"({len(fresh_batch)} new, {len(retry_eligible)} retries) "
        f"+ refetching {len(refetch_batch)} for per-season data ...\n"
    )

    found_this_run = 0
    refetched_this_run = 0

    # ── Refetch pass (known espn_id, no search call) ───────────────────────
    for cp in refetch_batch:
        cid  = cp["canonical_id"]
        name = cp["canonical_name"]
        rec  = nfl_stats.get(cid) or {}
        espn_id = rec.get("espn_id")
        if not espn_id:
            continue
        print(f"  {name} [backfill ESPN:{espn_id}] ...", end="  ", flush=True)
        time.sleep(DELAY)
        result = fetch_player_seasons(espn_id)
        if result and result.get("seasons"):
            nfl_stats[cid] = {
                "espn_id": espn_id,
                "name":    name,
                "stats":   result["totals"],
                "seasons": result["seasons"],
            }
            refetched_this_run += 1
            print(f"{len(result['seasons'])} seasons, {len(result['totals'])} stat keys")
        else:
            # Preserve the old career-totals payload but mark as backfilled-empty
            # so we don't keep retrying on every run.
            rec["seasons"] = []
            nfl_stats[cid] = rec
            print("no per-season rows")
        _save_json(STATS_FILE, nfl_stats)

    for cp in batch:
        cid  = cp["canonical_id"]
        name = cp["canonical_name"]
        is_retry = cid in not_found_set
        retry_tag = " [retry]" if is_retry else ""
        print(f"  {name}{retry_tag} ...", end="  ", flush=True)

        time.sleep(DELAY)
        espn_id = search_player(name)

        if espn_id is None:
            print("—")
            not_found_set.add(cid)
            prev = not_found_meta.get(cid, {"attempts": 0, "last_run": 0})
            not_found_meta[cid] = {
                "attempts": int(prev.get("attempts", 0) or 0) + 1,
                "last_run": run_counter,
            }
        else:
            time.sleep(DELAY)
            result = fetch_player_seasons(espn_id)
            if result and result.get("seasons"):
                nfl_stats[cid] = {
                    "espn_id": espn_id,
                    "name":    name,
                    "stats":   result["totals"],
                    "seasons": result["seasons"],
                }
                print(
                    f"ESPN:{espn_id}  "
                    f"({len(result['seasons'])} seasons, "
                    f"{len(result['totals'])} stat keys)"
                )
                found_this_run += 1
                if cid in not_found_set:
                    not_found_set.discard(cid)
                    not_found_meta.pop(cid, None)
            else:
                # Found on ESPN but no NFL stat rows (e.g., practice squad / undrafted tryout)
                not_found_set.add(cid)
                print(f"ESPN:{espn_id}  (no stats)")
                prev = not_found_meta.get(cid, {"attempts": 0, "last_run": 0})
                not_found_meta[cid] = {
                    "attempts": int(prev.get("attempts", 0) or 0) + 1,
                    "last_run": run_counter,
                }

        searched_set.add(cid)

        # Persist after every player so a crash doesn't lose progress
        state["searched"] = sorted(searched_set)
        state["not_found"] = sorted(not_found_set)  # keep list for compatibility
        state["not_found_meta"] = not_found_meta
        state["run_counter"] = run_counter
        _save_json(STATE_FILE, state)
        _save_json(STATS_FILE, nfl_stats)

    remaining_after_fresh = max(0, len(remaining) - len(fresh_batch))
    total_seasons = sum(
        len(r.get("seasons") or [])
        for r in nfl_stats.values()
        if isinstance(r, dict)
    )
    print(
        f"\nDone.  +{found_this_run} found, +{refetched_this_run} backfilled this run "
        f"({len(nfl_stats)} players, {total_seasons} season rows total).  "
        f"{remaining_after_fresh} new players remaining."
    )


if __name__ == "__main__":
    main()

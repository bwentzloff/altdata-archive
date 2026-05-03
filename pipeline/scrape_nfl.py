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
STATS_URL  = (
    "https://sports.core.api.espn.com"
    "/v2/sports/football/leagues/nfl/athletes/{id}/statistics/0"
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _should_skip(stat_name: str) -> bool:
    n = stat_name.lower()
    return any(p in n for p in SKIP_SUBSTRINGS)


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


def fetch_career_stats(espn_id: int) -> dict:
    """
    Fetch career NFL stats for an ESPN athlete.
    Returns a flat {stat_key: float} dict of non-zero counting stats,
    keyed as  nfl_{cat_prefix}_{espnStatName}.
    """
    url = STATS_URL.format(id=espn_id)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return {}
        data = resp.json()
    except Exception:
        return {}

    stats = {}
    categories = data.get("splits", {}).get("categories", [])
    for cat in categories:
        cat_name = cat.get("name", "")
        if cat_name not in KEEP_CATEGORIES:
            continue
        prefix = CAT_PREFIX.get(cat_name, cat_name[:2])
        for stat in cat.get("stats", []):
            sname = stat.get("name", "")
            sval  = stat.get("value")
            if sval is None or sval == 0.0:
                continue
            if _should_skip(sname):
                continue
            key = f"nfl_{prefix}_{sname}"
            stats[key] = sval

    return stats


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
        "--status", action="store_true",
        help="Print progress without fetching",
    )
    args = parser.parse_args()

    players   = _load_json(MERGED / "players_merged.json", [])
    state     = _load_json(STATE_FILE, {"searched": [], "not_found": []})
    nfl_stats = _load_json(STATS_FILE, {})

    searched_set  = set(state.get("searched", []))
    not_found_set = set(state.get("not_found", []))

    # Players we haven't attempted yet
    remaining = [
        p for p in players
        if p["canonical_id"] not in searched_set
    ]

    total       = len(players)
    done_count  = len(searched_set)
    found_count = len(nfl_stats)
    pct = 100 * done_count // total if total else 0

    print(
        f"NFL index: {done_count}/{total} searched ({pct}%)  "
        f"— {found_count} players with stats, "
        f"{len(not_found_set)} not found, "
        f"{len(remaining)} remaining"
    )

    if args.status or not remaining:
        if not remaining:
            print("All players searched.")
        return

    batch = remaining[: args.batch]
    print(f"Looking up {len(batch)} players ...\n")

    found_this_run = 0
    for cp in batch:
        cid  = cp["canonical_id"]
        name = cp["canonical_name"]
        print(f"  {name} ...", end="  ", flush=True)

        time.sleep(DELAY)
        espn_id = search_player(name)

        if espn_id is None:
            print("—")
            not_found_set.add(cid)
        else:
            time.sleep(DELAY)
            player_stats = fetch_career_stats(espn_id)
            if player_stats:
                nfl_stats[cid] = {
                    "espn_id": espn_id,
                    "name":    name,
                    "stats":   player_stats,
                }
                print(f"ESPN:{espn_id}  ({len(player_stats)} stats)")
                found_this_run += 1
            else:
                # Found on ESPN but no NFL stats (e.g., practice squad / undrafted tryout)
                not_found_set.add(cid)
                print(f"ESPN:{espn_id}  (no stats)")

        searched_set.add(cid)

        # Persist after every player so a crash doesn't lose progress
        state["searched"] = sorted(searched_set)
        state["not_found"] = sorted(not_found_set)
        _save_json(STATE_FILE, state)
        _save_json(STATS_FILE, nfl_stats)

    print(
        f"\nDone.  +{found_this_run} found this run "
        f"({len(nfl_stats)} total).  "
        f"{len(remaining) - len(batch)} players remaining."
    )


if __name__ == "__main__":
    main()

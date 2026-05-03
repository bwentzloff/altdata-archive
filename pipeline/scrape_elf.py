"""
scrape_elf.py
Fetches ELF historical player stats from the Wayback Machine archive of
sportsmetrics.football and builds the pipeline/raw/elf_historical_*.json files.

Sources:
  - 2021 + 2022 RS + PO: June 2023 snapshot (has complete prior-season totals)
    https://web.archive.org/web/20230609180600/https://www.sportsmetrics.football/stats/player-stats

Years targeted:
  2021, 2022   (ELF 2023 is already in the live database as sport id=16)

Synthetic player IDs start at 300000.
Game ID format: FOOTBALL_ELF_{year}_SEASON_TOTAL
"""

import json
import re
import sys
import time
from pathlib import Path

import requests

PIPELINE = Path(__file__).parent
RAW = PIPELINE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "elf_historical_players.json"
STATS_FILE   = RAW / "elf_historical_stats.json"
CACHE_FILE   = RAW / "elf_historical_raw.json"   # raw Wayback snapshot cache

WAYBACK_URL = (
    "https://web.archive.org/web/20230609180600/"
    "https://www.sportsmetrics.football/stats/player-stats"
)

SYNTHETIC_ID_START = 300000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# Years to include (ELF 2023 already in live DB)
TARGET_YEARS = {2021, 2022}

# ── Stat field mapping: sportsmetrics field → pipeline stat name ────────────
STAT_MAP = {
    # Passing
    "gp":           "pass_gp",    # overridden per category below
    "pass_att":     "pass_att",
    "pass_comp":    "pass_cmp",
    "pass_yds":     "pass_yds",
    "pass_td":      "pass_td",
    "pass_int":     "pass_int",
    # Rushing
    "rush_att":     "rush_att",
    "rush_yds":     "rush_yds",
    "rush_td":      "rush_td",
    # Receiving
    "rcv_no":       "recv_rec",
    "rcv_yds":      "recv_yds",
    "rcv_td":       "recv_td",
    # Defense
    "defense_tot_tack": "def_tackles",
    "defense_sack":     "def_sacks",
    "defense_int":      "def_int",
    "defense_ff":       "def_ff",
    "defense_fr":       "def_fr",
    "defense_brup":     "def_pd",
    # Kicking
    "fg_made":      "fg_made",
    "fg_att":       "fg_att",
    "pat_kickmade": "pat_made",
    "pat_kickatt":  "pat_att",
    # Punting
    "punt_no":      "punt_no",
    "punt_yds":     "punt_yds",
    # Returns
    "kr_no":        "kr_no",
    "kr_yds":       "kr_yds",
    "kr_td":        "kr_td",
    "pr_no":        "pr_no",
    "pr_yds":       "pr_yds",
    "pr_td":        "pr_td",
}

# Numeric fields that may arrive as strings or "NaN"
NUMERIC_FIELDS = set(STAT_MAP.keys())


def safe_float(v):
    """Convert value to float, returning 0.0 for NaN/None/empty."""
    if v is None or v == "" or v != v:
        return 0.0
    try:
        f = float(v)
        return 0.0 if (f != f) else f   # isnan check
    except (TypeError, ValueError):
        return 0.0


def infer_position(row: dict) -> str:
    """Guess position from dominant stats."""
    pa = safe_float(row.get("pass_att", 0))
    ra = safe_float(row.get("rush_att", 0))
    rcv = safe_float(row.get("rcv_no", 0))
    tack = safe_float(row.get("defense_tot_tack", 0))
    fg = safe_float(row.get("fg_att", 0))
    punt = safe_float(row.get("punt_no", 0))

    if pa >= 5:
        return "QB"
    if ra >= 10 and pa < 5:
        return "RB"
    if rcv >= 5 and ra < 5 and pa < 5:
        return "WR"
    if tack >= 5 and ra < 5:
        return "LB"
    if fg >= 1:
        return "K"
    if punt >= 5 and fg < 1:
        return "P"
    return ""


def fetch_raw_data() -> dict:
    """Fetch Wayback snapshot and return parsed NEXT_DATA."""
    if CACHE_FILE.exists():
        print(f"Loading from cache: {CACHE_FILE}")
        return json.loads(CACHE_FILE.read_text())

    print(f"Fetching Wayback snapshot (this may take 30-60s)...")
    print(f"  {WAYBACK_URL}")
    r = requests.get(WAYBACK_URL, headers=HEADERS, timeout=90)
    r.raise_for_status()

    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        r.text, re.DOTALL
    )
    if not m:
        sys.exit("ERROR: Could not find __NEXT_DATA__ in archived page")

    data = json.loads(m.group(1))
    CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    print(f"  Saved cache to {CACHE_FILE.name}")
    return data


def build_players_and_stats(raw_data: dict):
    """
    Process the raw NEXT_DATA and write elf_historical_players.json
    and elf_historical_stats.json.
    """
    page_props = raw_data["props"]["pageProps"]
    players_by_id = {
        p["id"]: p
        for p in page_props["players"]["data"]
    }
    totals = page_props["totals"]["data"]

    # Filter to target years only
    rows = [r for r in totals if r.get("year") in TARGET_YEARS]
    print(f"Rows after year filter ({TARGET_YEARS}): {len(rows)}")

    # Aggregate RS + PO per (player_id, year) into season totals
    # Key: (leaguetool player id, year)
    season_totals: dict[tuple, dict] = {}
    for row in rows:
        pid = row["id"]
        year = row["year"]
        key = (pid, year)
        if key not in season_totals:
            season_totals[key] = {
                "id": pid,
                "year": year,
                "team": row.get("team", ""),
                "name": row.get("name", ""),
            }
            for f in NUMERIC_FIELDS:
                season_totals[key][f] = 0.0
        # Accumulate all seasons (RS + PO)
        for f in NUMERIC_FIELDS:
            season_totals[key][f] += safe_float(row.get(f, 0))

    print(f"Unique (player, year) season totals: {len(season_totals)}")

    # Build synthetic player list (deduplicated across years)
    # Use leaguetool UUID as deduplication key
    seen_lt_ids: dict[str, int] = {}   # leaguetool id → synthetic int id
    out_players = []
    synthetic_id = SYNTHETIC_ID_START

    for (lt_id, year), agg in sorted(season_totals.items(), key=lambda x: x[0][0]):
        if lt_id not in seen_lt_ids:
            # Get full name from players dict if available
            p_info = players_by_id.get(lt_id, {})
            first = p_info.get("firstname", "")
            last  = p_info.get("lastname", "")
            if first and last:
                full_name = f"{first} {last}"
            else:
                # Fall back to abbreviated name from stats (e.g. "A. Agackesen")
                full_name = agg["name"]

            position = infer_position(agg)

            player = {
                "id":          synthetic_id,
                "full_name":   full_name,
                "short_name":  full_name,
                "first_name":  first or full_name.split()[0],
                "last_name":   last or full_name.split()[-1],
                "sport_id":    None,
                "league":      "ELF",
                "team":        "",
                "position":    position,
                "_elf_historical": True,
                "_elf_lt_id":  lt_id,
                "_norm_name":  full_name.lower(),
                "sportradar_id": None,
                "college":     None,
                "jersey":      None,
                "height":      None,
                "weight":      None,
            }
            out_players.append(player)
            seen_lt_ids[lt_id] = synthetic_id
            synthetic_id += 1

    print(f"Unique synthetic ELF players: {len(out_players)}")

    # Build stat rows
    out_stats = []
    for (lt_id, year), agg in season_totals.items():
        syn_id = seen_lt_ids[lt_id]
        game_id = f"FOOTBALL_ELF_{year}_SEASON_TOTAL"

        # Games played — use max of any non-zero gp stat in the row
        gp = safe_float(agg.get("gp", 0))

        # Emit one stat row per mapped field
        # Emit GP as a general stat
        if gp > 0:
            out_stats.append({
                "player_id": syn_id,
                "week":      1,
                "stat":      "gp",
                "value":     gp,
                "game_id":   game_id,
                "_year":     year,
            })

        for src_field, stat_name in STAT_MAP.items():
            if src_field == "gp":
                continue  # handled above
            val = agg.get(src_field, 0.0)
            if val and val > 0:
                out_stats.append({
                    "player_id": syn_id,
                    "week":      1,
                    "stat":      stat_name,
                    "value":     val,
                    "game_id":   game_id,
                    "_year":     year,
                })

    print(f"Total stat rows generated: {len(out_stats)}")

    # Save
    PLAYERS_FILE.write_text(json.dumps(out_players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(out_stats, indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name}: {len(out_players)} players")
    print(f"Wrote {STATS_FILE.name}: {len(out_stats)} stat rows")

    # Summary
    from collections import Counter
    year_counts = Counter(s["_year"] for s in out_stats)
    print("\nStat rows by year:")
    for yr, cnt in sorted(year_counts.items()):
        print(f"  ELF {yr}: {cnt}")

    return out_players, out_stats


def main():
    print("=== ELF Historical Stats Import ===")
    raw_data = fetch_raw_data()
    build_players_and_stats(raw_data)
    print("\nDone.")


if __name__ == "__main__":
    main()

"""
scrape_elf.py
Fetches ELF (European League of Football) historical player stats from the
league's live site (europeanleague.football) and builds the
pipeline/raw/elf_historical_*.json files.

Source:
  https://europeanleague.football/stats/player/{year}   (one page per season)

The page is a Next.js App Router route; per-category player arrays
(passers, rushers, receivers, tacklers, kickers, punters, returners,
passdefs, qualpassers) are embedded in the RSC payload streamed via
`self.__next_f.push([1, "..."])` script tags. Player UUIDs are stable
across seasons and match the UUIDs in the original sportsmetrics.football
dataset that previously seeded 2021/2022, so historical IDs carry forward.

Years targeted: 2021..current. Years with no records are skipped.

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
CACHE_DIR    = RAW / "elf_cache"
CACHE_DIR.mkdir(exist_ok=True)

BASE_URL = "https://europeanleague.football/stats/player/{year}"

SYNTHETIC_ID_START = 300000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# Years to attempt. Years with no player records on the live page are skipped
# (e.g. seasons that haven't started yet).
TARGET_YEARS = [2021, 2022, 2023, 2024, 2025, 2026]

# Category arrays inside the RSC payload that contain per-player stat rows.
CATEGORY_KEYS = (
    "passers", "qualpassers", "rushers", "receivers",
    "tacklers", "passdefs", "kickers", "punters", "returners",
)

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


def _extract_rsc_body(html: str) -> str:
    """Concatenate and decode every `self.__next_f.push([1, "..."])` chunk."""
    parts = re.findall(r'self\.__next_f\.push\(\[1,"(.+?)"\]\)', html, re.DOTALL)
    out = []
    for p in parts:
        try:
            out.append(json.loads('"' + p + '"'))
        except json.JSONDecodeError:
            continue
    return "".join(out)


def _extract_array(body: str, key: str):
    """Return the JSON array following `"<key>":[` in body, or [] if absent."""
    needle = f'"{key}":[' + '{'
    idx = body.find(needle)
    if idx < 0:
        return []
    start = idx + len(key) + 3  # position of opening '['
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(body)):
        c = body[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(body[start:i + 1])
                except json.JSONDecodeError:
                    return []
    return []


def fetch_year(year: int) -> list[dict]:
    """Fetch one season's /stats/player/{year} page; return merged player rows."""
    cache = CACHE_DIR / f"elf_{year}.html"
    if cache.exists():
        html = cache.read_text(encoding="utf-8")
        print(f"  [{year}] loaded from cache ({len(html)} bytes)")
    else:
        url = BASE_URL.format(year=year)
        print(f"  [{year}] fetching {url}")
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        html = r.text
        cache.write_text(html, encoding="utf-8")
        time.sleep(1.0)  # be polite

    body = _extract_rsc_body(html)
    if not body:
        print(f"  [{year}] WARN: empty RSC payload")
        return []

    # Merge per-player rows across all category arrays. Each record has
    # {id, name, team, pos, gp, <stat fields>}. Different categories
    # contribute disjoint stat fields for the same player.
    merged: dict[str, dict] = {}
    for key in CATEGORY_KEYS:
        arr = _extract_array(body, key)
        for rec in arr:
            pid = rec.get("id")
            if not pid:
                continue
            if pid not in merged:
                merged[pid] = {
                    "id":   pid,
                    "name": rec.get("name", ""),
                    "team": rec.get("team", ""),
                    "pos":  rec.get("pos", ""),
                    "year": year,
                    "gp":   safe_float(rec.get("gp", 0)),
                }
            tgt = merged[pid]
            # Prefer non-empty identity fields from any record
            for f in ("name", "team", "pos"):
                if not tgt.get(f) and rec.get(f):
                    tgt[f] = rec[f]
            tgt["gp"] = max(tgt["gp"], safe_float(rec.get("gp", 0)))
            for src_field in NUMERIC_FIELDS:
                if src_field == "gp":
                    continue
                v = safe_float(rec.get(src_field, 0))
                if v:
                    tgt[src_field] = tgt.get(src_field, 0.0) + v
    rows = list(merged.values())
    print(f"  [{year}] {len(rows)} players merged from {sum(1 for k in CATEGORY_KEYS if _extract_array(body, k))} category arrays")
    return rows


_POS_SHORT = {
    "quarterback": "QB", "running back": "RB", "wide receiver": "WR",
    "tight end": "TE", "fullback": "FB", "kicker": "K", "punter": "P",
    "long snapper": "LS",
    "offensive lineman": "OL", "offensive tackle": "OT",
    "offensive guard": "OG", "center": "C",
    "defensive lineman": "DL", "defensive end": "DE",
    "defensive tackle": "DT", "nose tackle": "NT",
    "linebacker": "LB", "cornerback": "CB", "safety": "S",
    "defensive back": "DB", "return specialist": "RS",
}


def _normalize_pos(pos: str, row: dict) -> str:
    if pos:
        short = _POS_SHORT.get(pos.strip().lower())
        if short:
            return short
    return infer_position(row)


def build_players_and_stats(year_rows: dict[int, list[dict]]):
    """
    Process per-year merged rows and write elf_historical_players.json and
    elf_historical_stats.json.
    """
    # Flatten into (lt_id, year) -> aggregated row
    season_totals: dict[tuple, dict] = {}
    for year, rows in year_rows.items():
        for row in rows:
            pid = row["id"]
            key = (pid, year)
            if key in season_totals:
                # Shouldn't happen (already merged inside fetch_year) but
                # tolerate duplicate years defensively.
                agg = season_totals[key]
                for f in NUMERIC_FIELDS:
                    agg[f] = agg.get(f, 0.0) + safe_float(row.get(f, 0))
            else:
                season_totals[key] = {
                    "id":   pid,
                    "year": year,
                    "team": row.get("team", ""),
                    "name": row.get("name", ""),
                    "pos":  row.get("pos", ""),
                    **{f: safe_float(row.get(f, 0)) for f in NUMERIC_FIELDS},
                }

    print(f"Unique (player, year) season totals: {len(season_totals)}")

    # Build synthetic player list (deduplicated across years).
    # Use leaguetool UUID as deduplication key. To keep synthetic IDs stable
    # across runs, reuse the IDs from any pre-existing players file.
    seen_lt_ids: dict[str, int] = {}
    out_players = []
    synthetic_id = SYNTHETIC_ID_START

    if PLAYERS_FILE.exists():
        try:
            prior = json.loads(PLAYERS_FILE.read_text())
            for p in prior:
                lt = p.get("_elf_lt_id")
                pid = p.get("id")
                if lt and isinstance(pid, int):
                    seen_lt_ids[lt] = pid
                    synthetic_id = max(synthetic_id, pid + 1)
            print(f"Preserving {len(seen_lt_ids)} existing synthetic IDs; "
                  f"next new ID = {synthetic_id}")
        except Exception as e:
            print(f"Could not load prior {PLAYERS_FILE.name}: {e}")

    prior_players_by_synth = {}
    if PLAYERS_FILE.exists():
        try:
            for p in json.loads(PLAYERS_FILE.read_text()):
                if isinstance(p.get("id"), int):
                    prior_players_by_synth[p["id"]] = p
        except Exception:
            pass

    for (lt_id, year), agg in sorted(season_totals.items(), key=lambda x: (x[0][1], x[0][0])):
        if lt_id not in seen_lt_ids:
            # Source records carry abbreviated names like "A. Brown".
            full_name = agg.get("name") or ""
            first = full_name.split(".")[0].strip() if "." in full_name else (full_name.split()[0] if full_name else "")
            last  = full_name.split()[-1] if full_name else ""

            position = _normalize_pos(agg.get("pos", ""), agg)

            player = {
                "id":          synthetic_id,
                "full_name":   full_name,
                "short_name":  full_name,
                "first_name":  first or (full_name.split()[0] if full_name else ""),
                "last_name":   last,
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
        else:
            # Existing player: keep prior record unchanged on first sighting
            syn = seen_lt_ids[lt_id]
            if syn in prior_players_by_synth and not any(p["id"] == syn for p in out_players):
                out_players.append(prior_players_by_synth[syn])

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
    year_rows: dict[int, list[dict]] = {}
    for year in TARGET_YEARS:
        try:
            rows = fetch_year(year)
        except requests.HTTPError as e:
            print(f"  [{year}] HTTP error: {e}; skipping")
            continue
        except requests.RequestException as e:
            print(f"  [{year}] request error: {e}; skipping")
            continue
        if rows:
            year_rows[year] = rows
        else:
            print(f"  [{year}] no records on page; skipping")

    if not year_rows:
        sys.exit("ERROR: no ELF data fetched for any year")

    build_players_and_stats(year_rows)
    print("\nDone.")


if __name__ == "__main__":
    main()

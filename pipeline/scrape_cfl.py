#!/usr/bin/env python3
"""
scrape_cfl.py — Incremental CFL historical stats backfill
Source: footballdb.com /statistics/cfl/player-stats/{cat}/{year}

Scrapes CFL seasons not yet in the DB (2019, 2021, 2022).
2020 was cancelled (COVID). 2023/2024/2025 come from the DB directly.

Outputs (all in pipeline/raw/):
  cfl_historical_players.json  — synthetic player records (IDs starting at 200000)
  cfl_historical_stats.json    — synthetic stat rows (one season-total row per stat)
  cfl_historical_state.json    — scrape progress state

Injection: build_data.py reads these and extends raw_players + raw_stats
before aggregation, so historical CFL seasons appear naturally in each
player's game log and season_totals.
"""

import argparse
import json
import re
import time
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW = BASE / "raw"
RAW.mkdir(exist_ok=True)

STATE_FILE   = RAW / "cfl_historical_state.json"
PLAYERS_FILE = RAW / "cfl_historical_players.json"
STATS_FILE   = RAW / "cfl_historical_stats.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

BASE_URL = "https://www.footballdb.com"

# Years to backfill — 2020 was cancelled; 2023+ is in the live DB
TARGET_YEARS = [2019, 2021, 2022]

# Synthetic player IDs start here (AAF used 100000–100372)
SYNTHETIC_ID_START = 200000

# ── Category definitions ─────────────────────────────────────────────────────
# (category_slug, stat_prefix, counting_cols_to_keep)
# Rate/average columns (Pct, Avg, YPA, YPG, Lg, FGPct, etc.) are skipped.
CATEGORIES = [
    ("passing",   "pass",  {"GP", "Att", "Cmp", "Yds", "TD", "Int"}),
    ("rushing",   "rush",  {"GP", "Att", "Yds", "TD"}),
    ("receiving", "recv",  {"GP", "Rec", "Yds", "TD"}),
    ("defense",   "def",   {"GP", "Solo", "Ast", "Sack", "Int", "FF", "FR"}),
    ("kicking",   "kick",  {"GP", "FGM", "FGA", "XPM", "XPA", "Pts"}),
    ("returns",   "ret",   {"GP", "KR", "KRYds", "KRTD", "PR", "PRYds", "PRTD"}),
    ("scoring",   "score", {"GP", "TD", "Pts"}),
]


def _infer_position(categories_seen: set) -> str:
    """Infer a broad position from which stat categories a player appears in."""
    if "passing" in categories_seen:
        return "QB"
    if "kicking" in categories_seen:
        return "K"
    if "defense" in categories_seen:
        return "DB"
    if "rushing" in categories_seen:
        return "RB"
    if "receiving" in categories_seen:
        return "WR"
    return ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """Lowercase + strip diacritics for fuzzy matching."""
    nfkd = unicodedata.normalize("NFD", str(name))
    ascii_n = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", ascii_n).strip().lower()


def clean_player_name(raw: str) -> str:
    """
    footballdb encodes player cells as "First LastTEAMShort LastTEAM"
    e.g. "Nathan RourkeBCN.\xa0RourkeBC"
    Strip the team abbrev + repeated short name.
    """
    raw = raw.replace("\xa0", " ").strip()
    # Team abbreviations are 2–3 uppercase letters optionally followed by '.'
    m = re.search(r"[A-Z]{2,3}\.?", raw)
    if m:
        return raw[: m.start()].strip()
    return raw.strip()


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


# ── Scraper ───────────────────────────────────────────────────────────────────

def scrape_category_year(cat: str, year: int) -> list[dict]:
    """
    Fetch one category/year stats page and return list of
    {"name": str, "stats": {col: value_str}}.
    """
    url = f"{BASE_URL}/statistics/cfl/player-stats/{cat}/{year}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"    HTTP {r.status_code} — skipping")
            return []
        soup = BeautifulSoup(r.text, "lxml")
        tables = soup.find_all("table")
        if not tables:
            return []
        table = tables[0]
        rows = table.find_all("tr")
        if len(rows) < 2:
            return []
        # Parse header
        headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
        players = []
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            raw_name = cells[0].get_text(strip=True)
            name = clean_player_name(raw_name)
            if not name or len(name) < 3:
                continue
            stats = {}
            for i, h in enumerate(headers[1:], 1):
                if i < len(cells):
                    val = cells[i].get_text(strip=True).replace(",", "")
                    stats[h] = val
            players.append({"name": name, "stats": stats})
        return players
    except Exception as e:
        print(f"    Error: {e}")
        return []


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CFL historical backfill scraper")
    parser.add_argument("--batch", type=int, default=5,
                        help="Number of category/year pages to scrape per run")
    parser.add_argument("--year", type=int, choices=TARGET_YEARS,
                        help="Scrape only this year")
    parser.add_argument("--status", action="store_true",
                        help="Print progress and exit")
    args = parser.parse_args()

    state = load_json(STATE_FILE, {"done": [], "next_id": SYNTHETIC_ID_START})
    players_out = load_json(PLAYERS_FILE, [])
    stats_out   = load_json(STATS_FILE,   [])

    done = set(state["done"])
    next_id = state.get("next_id", SYNTHETIC_ID_START)

    # Build a name→id map and a categories-seen map from already-saved players
    name_to_id: dict[str, int] = {p["full_name"]: p["id"] for p in players_out}
    name_to_cats: dict[str, set] = {
        p["full_name"]: set(p.get("_categories_seen", []))
        for p in players_out
    }
    # Index by id for fast updates
    players_by_id: dict[int, dict] = {p["id"]: p for p in players_out}

    total_pages = len(TARGET_YEARS) * len(CATEGORIES)
    if args.status:
        print(f"CFL historical: {len(done)}/{total_pages} pages done")
        print(f"Players: {len(players_out)}  Stat rows: {len(stats_out)}")
        return

    # Build work queue
    years = [args.year] if args.year else TARGET_YEARS
    work = []
    for year in years:
        for cat, prefix, keep_cols in CATEGORIES:
            key = f"{cat}/{year}"
            if key not in done:
                work.append((cat, prefix, keep_cols, year, key))

    if not work:
        print("All CFL historical pages already scraped.")
        return

    batch = work[: args.batch]
    for cat, prefix, keep_cols, year, key in batch:
        print(f"  Scraping CFL {cat}/{year} ...")
        scraped = scrape_category_year(cat, year)
        print(f"    {len(scraped)} players")

        # Synthetic game_id for this season total.
        # Parsed by parse_game_meta in build_data.py via the FOOTBALL_*_SEASON_TOTAL pattern.
        game_id = f"FOOTBALL_CFL_{year}_SEASON_TOTAL"

        for p in scraped:
            name = p["name"]

            # Track categories seen — used to infer position
            cats = name_to_cats.setdefault(name, set())
            cats.add(cat)

            # Assign synthetic ID if this is a new player
            if name not in name_to_id:
                pid = next_id
                next_id += 1
                name_to_id[name] = pid
                record = {
                    "id":               pid,
                    "full_name":        name,
                    "short_name":       name,
                    "first_name":       name.split()[0] if name else "",
                    "last_name":        " ".join(name.split()[1:]) if name else "",
                    "sport_id":         None,   # no DB sport_id for synthetic
                    "league":           "CFL",
                    "team":             "",
                    "position":         _infer_position(cats),
                    "_cfl_historical":  True,
                    "_norm_name":       _norm(name),
                    "_categories_seen": list(cats),
                    # unused fields to match SQL player shape
                    "sportradar_id": None, "college": None, "jersey": None,
                    "height": None, "weight": None,
                }
                players_out.append(record)
                players_by_id[pid] = record
            else:
                pid = name_to_id[name]
                rec = players_by_id[pid]
                rec["_categories_seen"] = list(cats)
                rec["position"] = _infer_position(cats)

            pid = name_to_id[name]

            # Emit one stat row per counting stat, tagged with the season game_id
            for col, raw_val in p["stats"].items():
                if col not in keep_cols:
                    continue
                try:
                    val = float(raw_val)
                except (ValueError, TypeError):
                    continue
                if val == 0:
                    continue
                stat_name = f"{prefix}_{col.lower()}"
                stats_out.append({
                    "player_id": pid,
                    "week":      1,           # season total treated as a single game
                    "stat":      stat_name,
                    "value":     val,
                    "game_id":   game_id,
                    "_year":     year,
                })

        done.add(key)
        state["done"] = list(done)
        state["next_id"] = next_id
        save_json(STATE_FILE, state)
        save_json(PLAYERS_FILE, players_out)
        save_json(STATS_FILE, stats_out)
        time.sleep(1.0)

    remaining = len(work) - len(batch)
    print(f"Done. {remaining} pages remaining.")
    print(f"Total players: {len(players_out)},  stat rows: {len(stats_out)}")


if __name__ == "__main__":
    main()

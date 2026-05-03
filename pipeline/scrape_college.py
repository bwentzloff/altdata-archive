"""
scrape_college.py — Incrementally scrape college football stats from footballdb.com.

Processes a small batch of (year, mode, league) pages per run to stay polite to
the server.  Re-run repeatedly until the backfill is complete; the script picks
up exactly where it left off each time.

Coverage: 2005–2025 × 5 stat modes × FBS + FCS = 210 pages total.
At the default batch of 20 pages/run that's about 11 runs to finish the index.

Outputs (pipeline/raw/, all gitignored):
  college_stats_raw.json    — {fdb_url: {name, school, seasons: {yr: {stat: val}}}}
  college_name_index.json   — {normalised_name: [fdb_url, ...]}
  college_scrape_state.json — progress tracking

Usage:
  python pipeline/scrape_college.py              # fetch next 20 pages (default)
  python pipeline/scrape_college.py --batch 50   # larger batch
  python pipeline/scrape_college.py --status     # show progress only, no fetching
"""

import argparse
import json
import re
import time
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://www.footballdb.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}
DELAY = 1.5   # seconds between real (non-cached) requests

RAW_DIR   = Path(__file__).parent / "raw"
RAW_DIR.mkdir(exist_ok=True)
CACHE_DIR = RAW_DIR / "_fdb_cache"
CACHE_DIR.mkdir(exist_ok=True)

STATS_FILE = RAW_DIR / "college_stats_raw.json"
INDEX_FILE = RAW_DIR / "college_name_index.json"
STATE_FILE = RAW_DIR / "college_scrape_state.json"

# Scrape range
YEARS   = list(range(2005, 2026))   # 21 seasons
LEAGUES = ["FBS"]   # FCS stats pages are JS-rendered and not scrape-able via requests
MODES   = ["P", "R", "C", "D", "K"]  # Passing, Rushing, Receiving, Defense, Kicking

# Per-mode: page column header → our stat key
# Only counting stats; skip rates, percentages, and "long" columns.
MODE_COLS = {
    "P": {
        "Att":  "pass_att",
        "Cmp":  "pass_cmp",
        "Yds":  "pass_yds",
        "TD":   "pass_td",
        "Int":  "pass_int",
        "Sack": "pass_sack",
    },
    "R": {
        "Att": "rush_att",
        "Yds": "rush_yds",
        "TD":  "rush_td",
    },
    "C": {
        "Rec": "rec_num",
        "Yds": "rec_yds",
        "TD":  "rec_td",
    },
    "D": {
        "Solo": "def_solo",
        "Ast":  "def_ast",
        "Tot":  "def_tot",
        "Sack": "def_sack",
        "Int":  "def_int",
        "PD":   "def_pd",
    },
    "K": {
        "FGM": "fg_made",
        "FGA": "fg_att",
        "XPM": "xp_made",
        "XPA": "xp_att",
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_page(url: str) -> BeautifulSoup:
    """Fetch with disk cache.  Only sleeps DELAY on a real network request."""
    safe = re.sub(r"[^a-z0-9]", "_", url.lower())[:200]
    cache_file = CACHE_DIR / (safe + ".html")
    if cache_file.exists():
        return BeautifulSoup(cache_file.read_text(encoding="utf-8"), "lxml")
    time.sleep(DELAY)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    cache_file.write_text(resp.text, encoding="utf-8")
    return BeautifulSoup(resp.text, "lxml")


def normalize_name(name: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace."""
    nfkd = unicodedata.normalize("NFD", str(name))
    ascii_n = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", ascii_n).strip().lower()


def parse_player_cell(td):
    """
    Extract (name, fdb_url_path, school_abbr) from a player cell.
    footballdb uses desktop/mobile span pairs; we prefer the desktop span.
    Returns (name, href, school) — href is '' if it's not a /players/ link.
    """
    desktop = td.find("span", class_=lambda c: c and "d-xl-inline" in c)
    if desktop:
        link = desktop.find("a")
        team_span = desktop.find("span", class_="statplayer-team")
        name   = link.get_text(strip=True) if link else ""
        href   = link["href"] if link and link.get("href") else ""
        school = team_span.get_text(strip=True) if team_span else ""
        return name, href, school
    # Fallback: first <a> in cell
    link = td.find("a")
    if link:
        href   = link.get("href", "")
        name   = link.get_text(strip=True)
        team_span = td.find("span", class_="statplayer-team")
        school = team_span.get_text(strip=True) if team_span else ""
        return name, href, school
    return td.get_text(strip=True), "", ""


# ── Core scraping ─────────────────────────────────────────────────────────────

def scrape_one_page(year: int, mode: str, league: str) -> int:
    """
    Scrape one (year, mode, league) stats page and merge results into the
    two output files.  Returns the number of new player URLs added.
    """
    url = (
        f"{BASE_URL}/college-football/stats/stats.html"
        f"?mode={mode}&yr={year}&lg={league}"
    )
    try:
        soup = get_page(url)
    except Exception as exc:
        print(f"    fetch error: {exc}")
        return 0

    table = soup.find("table", class_="statistics")
    if not table:
        return 0
    thead = table.find("thead")
    if not thead:
        return 0

    # Discover column positions from the actual header row
    header_cells = [th.get_text(strip=True) for th in thead.find_all("th")]
    col_map = MODE_COLS.get(mode, {})
    stat_col_index = {col_map[h]: i for i, h in enumerate(header_cells) if h in col_map}
    if not stat_col_index:
        return 0

    stats_data = _load_json(STATS_FILE, {})
    name_index = _load_json(INDEX_FILE, {})
    yr_str = str(year)
    new_count = 0

    tbody = table.find("tbody")
    if not tbody:
        return 0

    for tr in tbody.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue

        name, href, school = parse_player_cell(cells[0])
        if not href.startswith("/players/"):
            continue

        # Parse stat values for this row
        row_stats = {}
        for stat_key, col_idx in stat_col_index.items():
            if col_idx < len(cells):
                raw = cells[col_idx].get_text(strip=True).replace(",", "").rstrip("t*")
                try:
                    row_stats[stat_key] = float(raw)
                except (ValueError, TypeError):
                    pass
        if not row_stats:
            continue

        # Merge into stats_data
        if href not in stats_data:
            stats_data[href] = {"name": name, "school": school, "seasons": {}}
            new_count += 1
        entry = stats_data[href]
        if not entry.get("school") and school:
            entry["school"] = school
        if yr_str not in entry["seasons"]:
            entry["seasons"][yr_str] = {}
        for sk, sv in row_stats.items():
            entry["seasons"][yr_str][sk] = entry["seasons"][yr_str].get(sk, 0) + sv

        # Update name → URL index
        norm = normalize_name(name)
        urls = name_index.setdefault(norm, [])
        if href not in urls:
            urls.append(href)

    _save_json(STATS_FILE, stats_data)
    _save_json(INDEX_FILE, name_index)
    return new_count


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_work_units():
    """All work units ordered recent-years-first (most relevant to our players)."""
    units = []
    for year in sorted(YEARS, reverse=True):
        for mode in MODES:
            for league in LEAGUES:
                key = f"{mode}_{league}_{year}"
                units.append((key, year, mode, league))
    return units


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--batch", type=int, default=20,
        help="Max pages to fetch this run (default: 20 ≈ 30 seconds)"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print progress summary without fetching anything"
    )
    args = parser.parse_args()

    state     = _load_json(STATE_FILE, {"completed": []})
    completed = set(state["completed"])
    all_units = build_work_units()
    remaining = [u for u in all_units if u[0] not in completed]

    stats_data  = _load_json(STATS_FILE, {})
    total_pages = len(all_units)
    done_pages  = len(completed)
    pct = 100 * done_pages // total_pages if total_pages else 0

    print(
        f"College stats index:  {done_pages}/{total_pages} pages done ({pct}%)  "
        f"— {len(stats_data)} unique players indexed"
    )

    if args.status or not remaining:
        if not remaining:
            print("All pages complete!")
        return

    batch = remaining[: args.batch]
    print(
        f"Fetching {len(batch)} pages  "
        f"({len(remaining) - len(batch)} remaining after this run) ...\n"
    )

    for key, year, mode, league in batch:
        print(f"  {mode:1s} {league:3s} {year}  ...", end="  ", flush=True)
        n = scrape_one_page(year, mode, league)
        print(f"+{n} new")
        completed.add(key)
        state["completed"] = sorted(completed)
        _save_json(STATE_FILE, state)

    stats_data = _load_json(STATS_FILE, {})
    print(f"\nDone.  {len(stats_data)} unique players in index.")
    if remaining[args.batch:]:
        print(f"Run again to continue ({len(remaining) - len(batch)} pages left).")
    else:
        print("Index is complete!")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scrape_dgpt.py — Disc Golf Pro Tour stats backfill
Source: pdga.com/players/stats (official PDGA statistics)

Scrapes PDGA player statistics for:
  - MPO (Mixed Pro Open / Men's professional)
  - FPO (Female Pro Open / Women's professional)
  Years: 2019–2024

pdga.com/players/stats returns a paginated HTML table with:
  Name | PDGA # | Rating | Year | Gender | Class | Division |
  Country | State/Province | Events | Points | Cash

PDGA Points = season ranking points (proxy for competitive performance)
Cash = career prize money for the season

Outputs (all in pipeline/raw/):
  dgpt_players.json  — synthetic player records (IDs starting at 900000)
  dgpt_stats.json    — synthetic stat rows (season totals)
  dgpt_raw.json      — cache of fetched table pages

Injection: build_data.py reads these and extends raw_players + raw_stats.
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW  = BASE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "dgpt_players.json"
STATS_FILE   = RAW / "dgpt_stats.json"
CACHE_FILE   = RAW / "dgpt_raw.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

STATS_URL = "https://www.pdga.com/players/stats"

# Pro divisions to include
DIVISIONS = ["MPO", "FPO"]

# Target years (2019 = first full year of DGPT as a named organization)
TARGET_YEARS = [2019, 2020, 2021, 2022, 2023, 2024]

SYNTHETIC_ID_START = 900_000

# Map PDGA division name to a short division code
DIVISION_DISPLAY = {
    "MPO": "MPO",
    "FPO": "FPO",
}


def safe_float(v) -> float:
    if v is None or str(v).strip() in ("", "-", "—"):
        return 0.0
    try:
        cleaned = str(v).replace(",", "").replace("$", "").strip()
        f = float(cleaned)
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def name_from_link(href: str) -> str:
    """
    Extract a display name from a PDGA player profile link.
    Typical format: /player/PDGA_NUM  (no slug)
    or: /players/PDGA_NUM/first-last
    """
    m = re.search(r"/players?/\d+/([a-z][-a-z0-9]+)", href)
    if m:
        slug = m.group(1)
        return " ".join(p.title() for p in slug.split("-"))
    return ""


def fetch_page(year: int, division: str, page: int, cache: dict, reset: bool) -> tuple[list[dict], bool]:
    """
    Fetch one paginated page of PDGA stats.
    Returns (rows, has_next_page).
    """
    cache_key = f"{year}:{division}:{page}"
    # Only use cache for successful results — never cache failures (429, network errors)
    if cache_key in cache and cache[cache_key] is not None and not reset:
        return cache[cache_key], cache.get(f"{cache_key}:next", False)

    url = f"{STATS_URL}?Year={year}&Division={division}&page={page}"

    # Retry with exponential backoff to handle PDGA rate limiting (HTTP 429)
    for attempt in range(5):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            print(f"  WARN: {exc}")
            return [], False   # do NOT cache — allow retry next run

        if r.status_code == 200:
            break
        if r.status_code == 429:
            wait = 30 * (2 ** attempt)   # 30s, 60s, 120s, 240s, 480s
            print(f"  HTTP 429 — rate limited, waiting {wait}s before retry ({attempt+1}/5) …")
            time.sleep(wait)
        else:
            print(f"  HTTP {r.status_code}  {url}")
            return [], False   # do NOT cache — allow retry next run
    else:
        print(f"  Giving up on {url} after 5 attempts")
        return [], False   # do NOT cache — allow retry next run

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if not table:
        return [], False   # do NOT cache — page structure may change

    col_headers = [th.get_text(strip=True) for th in table.find_all("th")]

    rows: list[dict] = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if not cells:
            continue
        row: dict = {}
        for i, cell in enumerate(cells):
            if i < len(col_headers):
                key = col_headers[i]
                row[key] = cell.get_text(strip=True)
                if key == "Name":
                    a = cell.find("a")
                    if a and a.get("href"):
                        row["_link"] = a["href"]
        if row:
            rows.append(row)

    # Detect next page
    has_next = bool(soup.find(class_="pager-next") and soup.find(class_="pager-next").find("a"))

    cache[cache_key] = rows
    cache[f"{cache_key}:next"] = has_next
    return rows, has_next


def scrape_all(reset: bool = False, batch: int = 0) -> dict[str, dict]:
    """
    Scrape all years/divisions.
    Returns: {pdga_num: {name, division, country, state, gender, seasons: {year: {...}}}}
    batch: max number of new (uncached) page fetches this run; 0 = unlimited
    """
    cache: dict = {}
    if CACHE_FILE.exists() and not reset:
        cache = json.loads(CACHE_FILE.read_text())

    player_data: dict[str, dict] = {}
    fetched = 0  # count of new page fetches this run

    for division in DIVISIONS:
        for year in TARGET_YEARS:
            if batch and fetched >= batch:
                break
            page = 0
            total = 0

            while True:
                cache_key = f"{year}:{division}:{page}"
                is_new = reset or cache_key not in cache
                rows, has_next = fetch_page(year, division, page, cache, reset)
                if is_new and rows is not None:
                    fetched += 1

                for row in rows:
                    pdga_num = row.get("PDGA #", "").strip()
                    if not pdga_num or not pdga_num.isdigit():
                        continue

                    # Build display name: prefer slug from link, fall back to abbreviated
                    abbrev_name = row.get("Name", "").strip()
                    link = row.get("_link", "")
                    full_name = name_from_link(link) if link else ""
                    display_name = full_name if full_name else abbrev_name

                    if pdga_num not in player_data:
                        player_data[pdga_num] = {
                            "name":     display_name,
                            "name_abbrev": abbrev_name,
                            "division": division,
                            "country":  row.get("Country", ""),
                            "state":    row.get("State/Province", ""),
                            "gender":   row.get("Gender", ""),
                            "seasons":  {},
                        }
                    else:
                        # Update with more complete name if we got one
                        existing = player_data[pdga_num]["name"]
                        if full_name and len(full_name.split()) > len(existing.split()):
                            player_data[pdga_num]["name"] = full_name

                    player_data[pdga_num]["seasons"][year] = {
                        "events":  safe_float(row.get("Events")),
                        "points":  safe_float(row.get("Points")),
                        "rating":  safe_float(row.get("Rating")),
                        "cash":    safe_float(row.get("Cash")),
                    }

                total += len(rows)

                if not has_next or not rows:
                    break
                if batch and fetched >= batch:
                    break
                page += 1
                time.sleep(2.0)   # polite crawl rate; PDGA rate-limits aggressive scrapers

            if total > 0:
                print(f"  {division} {year}: {total} players ({page + 1} page(s))")

            # Brief pause between year/division combos to stay under rate limit
            time.sleep(3.0)

    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    return player_data


def build_outputs(player_data: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    out_players: list[dict] = []
    out_stats:   list[dict] = []
    synthetic_id = SYNTHETIC_ID_START

    for pdga_num, pdata in sorted(player_data.items(), key=lambda x: int(x[0])):
        if not pdata.get("seasons"):
            continue

        name = pdata["name"]
        if not name:
            continue

        parts = name.split()
        first = parts[0]  if parts          else ""
        last  = parts[-1] if len(parts) > 1 else ""

        # Position label: use division (MPO/FPO) as a proxy for
        # gender/competitive category in the UI
        position = DIVISION_DISPLAY.get(pdata["division"], "PRO")

        out_players.append({
            "id":         synthetic_id,
            "full_name":  name,
            "short_name": name,
            "first_name": first,
            "last_name":  last,
            "sport_id":   None,
            "league":     "DGPT",
            "team":       "",          # disc golf is individual sport
            "position":   position,
            "_dgpt":      True,
            "_pdga_source_id": pdga_num,
            "_division":  pdata["division"],
            "_norm_name": name.lower(),
            "sportradar_id": None,
            "college":    None,
            "jersey":     None,
            "height":     None,
            "weight":     None,
        })

        for year, season in sorted(pdata["seasons"].items()):
            game_id = f"DGPT_{year}_SEASON_TOTAL"

            stat_pairs = [
                ("events_played", season.get("events", 0)),
                ("pdga_points",   season.get("points", 0)),
                ("pdga_rating",   season.get("rating", 0)),
            ]
            for stat_name, value in stat_pairs:
                v = safe_float(value)
                if v:
                    out_stats.append({
                        "player_id": synthetic_id,
                        "week":      1,
                        "stat":      stat_name,
                        "value":     v,
                        "game_id":   game_id,
                        "_year":     int(year),
                    })

        synthetic_id += 1

    return out_players, out_stats


def main():
    ap = argparse.ArgumentParser(
        description="Scrape DGPT player stats from pdga.com"
    )
    ap.add_argument(
        "--reset", action="store_true",
        help="Ignore all caches and re-fetch from pdga.com"
    )
    ap.add_argument(
        "--batch", type=int, default=0,
        help="Max new page fetches per run (0 = unlimited)"
    )
    args = ap.parse_args()

    print("Scraping PDGA/DGPT player statistics …")
    print(f"Divisions: {DIVISIONS}    Years: {TARGET_YEARS}")
    print()

    player_data = scrape_all(reset=args.reset, batch=args.batch)
    print(f"\nTotal unique PDGA players found: {len(player_data)}")

    players, stats = build_outputs(player_data)
    years = sorted({s["_year"] for s in stats})
    print(f"Built {len(players)} DGPT player records")
    print(f"Built {len(stats)} DGPT stat rows  (years: {years})")

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats,   indent=2), encoding="utf-8")
    print(f"\nWrote {PLAYERS_FILE.name} and {STATS_FILE.name}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scrape_fcf.py — Fan Controlled Football stats backfill
Source: Wikipedia season articles

fcf.io is offline and was a React SPA with no Wayback snapshots.
Wikipedia has: 2021 full rosters + statistical leaders, 2022 rosters.

Outputs (all in pipeline/raw/):
  fcf_players.json   — synthetic player records (IDs starting at 700000)
  fcf_stats.json     — synthetic stat rows (season totals, leaders only)
  fcf_raw.json       — cache of parsed data

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

PLAYERS_FILE = RAW / "fcf_players.json"
STATS_FILE   = RAW / "fcf_stats.json"
CACHE_FILE   = RAW / "fcf_raw.json"

HEADERS = {
    "User-Agent": (
        "AltSportsArchive/1.0 (https://archive.altfantasysports.com; "
        "altfantasysports@gmail.com) Python/requests"
    )
}

SYNTHETIC_ID_START = 700_000

# Wikipedia pages to scrape per season
WIKI_PAGES = {
    2021: "2021_Fan_Controlled_Football_season",
    2022: "2022_Fan_Controlled_Football_season",
}

WIKI_API = "https://en.wikipedia.org/w/api.php"

# Map Wikipedia stat-leader row labels → our stat keys
STAT_LABEL_MAP = {
    "rushing yards":        "rushing_yards",
    "rushing touchdowns":   "rushing_tds",
    "passing yards":        "passing_yards",
    "passing touchdowns":   "passing_tds",
    "receptions":           "receptions",
    "receiving yards":      "receiving_yards",
    "receiving touchdowns": "receiving_tds",
    "interceptions":        "interceptions",
}

# FCF positional group → canonical position label
POSITION_GROUPS = {
    "quarterbacks": "QB",
    "running backs": "RB",
    "super backs": "SB",   # FCF hybrid RB/TE position
    "wide receivers": "WR",
    "tight ends": "TE",
    "offensive linemen": "OL",
    "defensive linemen": "DL",
    "linebackers": "LB",
    "cornerbacks": "CB",
    "safeties": "S",
    "defensive backs": "DB",
    "kickers": "K",
    "punters": "P",
    "specialists": "SPC",
}


def safe_float(v) -> float:
    if v is None or str(v).strip() in ("", "-", "—"):
        return 0.0
    try:
        f = float(str(v).replace(",", ""))
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def fetch_wiki_html(page_title: str, cache: dict, reset: bool) -> str | None:
    """Fetch rendered HTML for a Wikipedia article via the parse API."""
    cache_key = f"wiki:{page_title}"
    if cache_key in cache and not reset:
        return cache[cache_key]

    params = {
        "action": "parse",
        "page":   page_title,
        "prop":   "text",
        "format": "json",
    }
    try:
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            data = r.json()
            html = data.get("parse", {}).get("text", {}).get("*", "")
            if html:
                cache[cache_key] = html
                return html
        print(f"  Wikipedia HTTP {r.status_code} for {page_title}")
    except requests.RequestException as exc:
        print(f"  WARN: {exc} fetching {page_title}")
    return None


def parse_stat_leaders(soup: BeautifulSoup, year: int) -> list[dict]:
    """
    Extract the 'Statistical leaders' table from a FCF season page.
    Returns list of {name, team, stat, value, _year}.
    """
    results = []
    # Find the Statistical leaders section
    for heading in soup.find_all(["h2", "h3"]):
        if "statistical leaders" in heading.get_text(strip=True).lower():
            table = heading.find_next("table")
            if not table:
                break
            for tr in table.find_all("tr")[1:]:   # skip header
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if len(cells) < 4:
                    continue
                stat_label = cells[0].lower().strip()
                stat_key   = STAT_LABEL_MAP.get(stat_label)
                if not stat_key:
                    continue
                name  = cells[1].strip()
                team  = cells[2].strip()
                value = safe_float(cells[3])
                if name and value:
                    results.append({
                        "name":    name,
                        "team":    team,
                        "stat":    stat_key,
                        "value":   value,
                        "_year":   year,
                    })
            break
    return results


def parse_rosters(soup: BeautifulSoup, year: int) -> list[dict]:
    """
    Extract player names + positions from Table 6 (all-teams combined roster).
    Wikipedia renders this as: <b>Position Group</b> headers followed by <p><a>Name</a></p>
    nodes — use hyperlinks for clean, unambiguous name extraction.
    """
    players = []
    seen: set[str] = set()
    tables = soup.find_all("table")

    # Find the all-teams combined roster table (has ≥20 player links)
    roster_table = None
    for tbl in tables:
        player_links = [
            a for a in tbl.find_all("a")
            if ("/wiki/" in a.get("href", "") or "action=edit" in a.get("href", ""))
            and "Fan_Controlled" not in a.get("href", "")
            and len(a.get_text(strip=True)) > 2
        ]
        if len(player_links) >= 20:
            roster_table = tbl
            break

    if not roster_table:
        return players

    current_pos = ""

    def add(name: str, position: str):
        name = name.strip()
        if not name or len(name) < 3:
            return
        key = name.lower()
        if key not in seen:
            seen.add(key)
            players.append({"name": name, "team": "", "position": position, "_year": year})

    # Walk every element in the table; <b> nodes update position, <a> nodes add players
    for td in roster_table.find_all("td"):
        for el in td.descendants:
            if not hasattr(el, "name"):
                continue
            if el.name == "b":
                text = el.get_text(strip=True).lower()
                for group, abbr in POSITION_GROUPS.items():
                    if text.startswith(group.rstrip("s")) or group.startswith(text.rstrip("s")):
                        current_pos = abbr
                        break
            elif el.name == "a":
                href = el.get("href", "")
                if "/wiki/" in href or "action=edit" in href:
                    link_text = el.get_text(strip=True)
                    if (link_text
                            and len(link_text) > 2
                            and "Fan Controlled" not in link_text
                            and link_text not in ("Roster", "FCF")):
                        add(link_text, current_pos)

    return players


def fetch_all_data(cache: dict, reset: bool) -> dict:
    """
    Fetch FCF data from Wikipedia season pages.
    Returns {"players": [...], "stat_leaders": [...]}
    """
    if "fcf_rows" in cache and not reset:
        rows = cache["fcf_rows"]
        if isinstance(rows, dict):
            total = len(rows.get("players", [])) + len(rows.get("stat_leaders", []))
        else:
            total = len(rows)
        print(f"Using cached FCF data ({total} records)")
        return rows if isinstance(rows, dict) else {"players": [], "stat_leaders": []}

    all_players: list[dict]      = []
    all_stat_leaders: list[dict] = []

    for year, page_title in WIKI_PAGES.items():
        print(f"  Fetching Wikipedia: {page_title} …")
        html = fetch_wiki_html(page_title, cache, reset)
        if not html:
            print(f"  FCF {year}: no Wikipedia data")
            continue
        time.sleep(1.0)

        soup = BeautifulSoup(html, "html.parser")

        leaders = parse_stat_leaders(soup, year)
        if leaders:
            print(f"  FCF {year}: {len(leaders)} stat-leader rows")
            all_stat_leaders.extend(leaders)

        roster = parse_rosters(soup, year)
        if roster:
            print(f"  FCF {year}: {len(roster)} roster entries")
            all_players.extend(roster)

    result = {"players": all_players, "stat_leaders": all_stat_leaders}
    cache["fcf_rows"] = result
    return result


def build_outputs(data: dict) -> tuple[list[dict], list[dict]]:
    players_in = data.get("players", [])
    leaders_in = data.get("stat_leaders", [])

    out_players: list[dict] = []
    out_stats:   list[dict] = []
    synthetic_id = SYNTHETIC_ID_START
    # Use name.lower() keys for case-insensitive deduplication (Wikipedia is inconsistent)
    name_to_id: dict[str, int] = {}   # lower-case name → synthetic ID
    id_to_idx:  dict[int, int] = {}   # synthetic ID → index in out_players

    # ── Build player records from roster entries ──────────────────────────────
    for p in players_in:
        name = p["name"].strip()
        if not name:
            continue
        key = name.lower()
        if key not in name_to_id:
            parts = name.split()
            out_players.append({
                "id":           synthetic_id,
                "full_name":    name,
                "short_name":   name,
                "first_name":   parts[0] if parts else "",
                "last_name":    parts[-1] if len(parts) > 1 else "",
                "sport_id":     None,
                "league":       "FCF",
                "team":         p.get("team", ""),
                "position":     p.get("position", ""),
                "_fcf":         True,
                "_norm_name":   key,
                "sportradar_id": None,
                "college":      None,
                "jersey":       None,
                "height":       None,
                "weight":       None,
            })
            name_to_id[key]         = synthetic_id
            id_to_idx[synthetic_id] = len(out_players) - 1
            synthetic_id += 1
        elif p.get("team"):
            idx = id_to_idx[name_to_id[key]]
            if not out_players[idx]["team"]:
                out_players[idx]["team"] = p["team"]

    # ── Build stat rows from statistical leaders ──────────────────────────────
    player_year_stats: dict[tuple, dict] = {}

    for row in leaders_in:
        name  = row["name"].strip()
        year  = row["_year"]
        team  = row.get("team", "")
        stat  = row["stat"]
        value = row["value"]
        key   = name.lower()

        # Ensure player record exists
        if key not in name_to_id:
            parts = name.split()
            out_players.append({
                "id":           synthetic_id,
                "full_name":    name,
                "short_name":   name,
                "first_name":   parts[0] if parts else "",
                "last_name":    parts[-1] if len(parts) > 1 else "",
                "sport_id":     None,
                "league":       "FCF",
                "team":         team,
                "position":     "",
                "_fcf":         True,
                "_norm_name":   key,
                "sportradar_id": None,
                "college":      None,
                "jersey":       None,
                "height":       None,
                "weight":       None,
            })
            name_to_id[key]         = synthetic_id
            id_to_idx[synthetic_id] = len(out_players) - 1
            synthetic_id += 1

        stat_key = (name_to_id[key], year)
        if stat_key not in player_year_stats:
            player_year_stats[stat_key] = {}
        player_year_stats[stat_key][stat] = value

    for (pid, year), stats in player_year_stats.items():
        game_id = f"FOOTBALL_FCF_{year}_SEASON_TOTAL"
        for stat_name, value in stats.items():
            if value:
                out_stats.append({
                    "player_id": pid,
                    "week":      1,
                    "stat":      stat_name,
                    "value":     float(value),
                    "game_id":   game_id,
                    "_year":     year,
                })

    return out_players, out_stats


def main():
    ap = argparse.ArgumentParser(description="Scrape FCF player data from Wikipedia")
    ap.add_argument("--reset", action="store_true", help="Ignore cache and re-fetch")
    args = ap.parse_args()

    cache: dict = {}
    if CACHE_FILE.exists() and not args.reset:
        cache = json.loads(CACHE_FILE.read_text())

    data = fetch_all_data(cache, reset=args.reset)
    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")

    players, stats = build_outputs(data)
    years = sorted({s["_year"] for s in stats})
    print(f"\nBuilt {len(players)} FCF players,  {len(stats)} stat rows  (years: {years})")

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats,   indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name} and {STATS_FILE.name}")


if __name__ == "__main__":
    main()

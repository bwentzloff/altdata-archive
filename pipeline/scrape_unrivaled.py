#!/usr/bin/env python3
"""
scrape_unrivaled.py — Unrivaled Basketball (women's 3-on-3) data.

Source: https://www.unrivaled.basketball

Scope: lightweight (parity with NLL historical / PUL).
  * Players + per-season averages
  * Team affiliation (per season) + head coach

Seasons: 2025 (inaugural, 6 teams) + 2026 (8 teams).

Outputs (pipeline/raw/):
  unrivaled_players.json   — one record per (player, season)
  unrivaled_stats.json     — per-season averages turned into season-total stat rows
  unrivaled_state.json     — {last_run, season_summary}
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW = BASE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "unrivaled_players.json"
STATS_FILE   = RAW / "unrivaled_stats.json"
STATE_FILE   = RAW / "unrivaled_state.json"

SYNTHETIC_ID_START = 2_700_000
ROOT = "https://www.unrivaled.basketball"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SEASONS = [2025, 2026]

# 2026 team slugs (the route used on unrivaled.basketball)
TEAM_SLUGS_2026 = [
    "breeze", "hive", "laces", "lunar-owls",
    "mist", "phantom", "rose", "vinyl",
]

# 2025 had only 6 teams (no Breeze, no Hive).
TEAM_SLUGS_2025 = ["laces", "lunar-owls", "mist", "phantom", "rose", "vinyl"]

DISPLAY_TEAM = {
    "breeze":      "Breeze BC",
    "hive":        "Hive BC",
    "laces":       "Laces BC",
    "lunar-owls":  "Lunar Owls BC",
    "mist":        "Mist BC",
    "phantom":     "Phantom BC",
    "rose":        "Rose BC",
    "vinyl":       "Vinyl BC",
}

# Hardcoded 2025 rosters + coaches (from Wikipedia 2025 Unrivaled season).
# Source: https://en.wikipedia.org/wiki/2025_Unrivaled_season
ROSTERS_2025 = {
    "laces":      ("Andrew Wade", [
        "Jackie Young", "Tiffany Hayes", "Kayla McBride",
        "Kate Martin", "Alyssa Thomas", "Stefanie Dolson",
    ]),
    "lunar-owls": ("DJ Sackmann", [
        "Skylar Diggins", "Courtney Williams", "Allisha Gray",
        "Cameron Brink", "Napheesa Collier", "Shakira Austin",
    ]),
    "mist":       ("Phil Handy", [
        "Jewell Loyd", "Courtney Vandersloot", "DiJonai Carrington",
        "Rickea Jackson", "Breanna Stewart", "Aaliyah Edwards",
    ]),
    "phantom":    ("Adam Harrington", [
        "Sabrina Ionescu", "Natasha Cloud", "Marina Mabrey",
        "Katie Lou Samuelson", "Satou Sabally", "Brittney Griner",
    ]),
    "rose":       ("Nola Henry", [
        "Chelsea Gray", "Brittney Sykes", "Kahleah Copper",
        "Lexie Hull", "Angel Reese", "Azurá Stevens",
    ]),
    "vinyl":      ("Teresa Weatherspoon", [
        "Arike Ogunbowale", "Jordin Canada", "Rhyne Howard",
        "Rae Burrell", "Aliyah Boston", "Dearica Hamby",
    ]),
}

# 2025 relief players (in-season signings) → team(s) they played for.
# We use the first team in the list as their primary 2025 team.
RELIEF_2025 = {
    "Natisha Hiedeman":       "phantom",
    "NaLyssa Smith":          "mist",
    "Kiki Jefferson":         "laces",
    "Betnijah Laney-Hamilton":"laces",
    "Ariel Atkins":           "rose",
    "Naz Hillmon":            "rose",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", str(s)).strip().lower()
    return re.sub(r"[\s_]+", "-", s)


def safe_float(v) -> float:
    if v is None or str(v).strip() in ("", "-", "—"):
        return 0.0
    try:
        f = float(str(v).replace(",", "").replace("%", ""))
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def name_key(n: str) -> str:
    n = n or ""
    # drop diacritics for matching (NFKD-style ascii fold)
    import unicodedata
    n = unicodedata.normalize("NFKD", n)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z]", "", n.lower())
    return n


def fetch(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {url}")
            return None
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  ERR {url}: {e}")
        return None


# ── /players page → current (2026) roster with height + jersey + slug ─────

def scrape_players_index() -> dict[str, dict]:
    """Return {name_key: {name, slug, team_slug, position, height_str, jersey}}."""
    soup = fetch(f"{ROOT}/players")
    if not soup:
        return {}

    out: dict[str, dict] = {}
    # The page renders a single table with one row per player.
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 5:
            continue
        # First cell holds player link + name
        link = cells[0].find("a", href=re.compile(r"^/player/"))
        if not link:
            continue
        slug = link["href"].split("/player/", 1)[1].strip("/")
        name = link.get_text(" ", strip=True)
        # Second cell holds team link
        team_link = cells[1].find("a", href=re.compile(r"^/(breeze|hive|laces|lunar-owls|mist|phantom|rose|vinyl)$"))
        team_slug = team_link["href"].lstrip("/") if team_link else ""
        position = cells[2].get_text(strip=True)
        height = cells[3].get_text(strip=True)
        jersey = cells[4].get_text(strip=True)
        out[name_key(name)] = {
            "name":      name,
            "slug":      slug,
            "team_slug": team_slug,
            "position":  position,
            "height":    height,
            "jersey":    jersey,
        }
    return out


# ── /stats/player?season=YEAR → season averages ───────────────────────────

# Column order on the stats page:
#   Rank | Name | GP | MIN | PTS | FGM | FGA | FG% | 3PM | 3PA | 3P% |
#   FTM  | FTA  | FT% | OREB | DREB | REB | AST | STL | BLK | TO | PF
STAT_COLS = [
    "games_played", "minutes", "points",
    "fgm", "fga", "fg_pct",
    "fg3m", "fg3a", "fg3_pct",
    "ftm", "fta", "ft_pct",
    "off_rebounds", "def_rebounds", "rebounds",
    "assists", "steals", "blocks", "turnovers", "fouls",
]


def scrape_season_stats(season: int) -> list[dict]:
    """Return list of {name, slug?, stats:{col:val}}."""
    url = f"{ROOT}/stats/player?season={season}"
    soup = fetch(url)
    if not soup:
        return []

    out: list[dict] = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 1 + 1 + len(STAT_COLS):
            continue
        # cells[0] = rank, cells[1] = name link
        rank_txt = cells[0].get_text(strip=True)
        if not rank_txt.isdigit():
            continue
        link = cells[1].find("a", href=re.compile(r"^/player/"))
        if not link:
            continue
        slug = link["href"].split("/player/", 1)[1].strip("/")
        name = link.get_text(" ", strip=True)
        stats: dict[str, float] = {}
        for i, col in enumerate(STAT_COLS):
            stats[col] = safe_float(cells[2 + i].get_text(strip=True))
        out.append({"name": name, "slug": slug, "stats": stats})
    return out


# ── /{team_slug} → 2026 head coach (roster pulled via /players index) ─────

def scrape_team_head_coach(team_slug: str) -> str | None:
    soup = fetch(f"{ROOT}/{team_slug}")
    if not soup:
        return None
    text = soup.get_text(" ", strip=True)
    # First try concatenated form: "FirstLastHead Coach"
    m = re.search(r"([A-Z][a-zA-Z'\.\-]*[a-z])([A-Z][a-zA-Z'\.\-]+)Head Coach", text)
    if m:
        return f"{m.group(1)} {m.group(2)}".strip()
    # Otherwise grab the two capitalized words immediately before "Head Coach"
    # using greedy .* to anchor to the LAST occurrence.
    m = re.search(r".*\s([A-Z][a-zA-Z'\.\-]+)\s+([A-Z][a-zA-Z'\.\-]+)\s+Head Coach", text)
    return f"{m.group(1)} {m.group(2)}" if m else None


# ── orchestrate ──────────────────────────────────────────────────────────

def build_player_record(pid: int, name: str, season: int, team_slug: str,
                        slug: str | None, position: str, height: str,
                        jersey: str) -> dict:
    parts = name.split()
    return {
        "id":             pid,
        "full_name":      name,
        "short_name":     name,
        "first_name":     parts[0] if parts else name,
        "last_name":      " ".join(parts[1:]) if len(parts) > 1 else "",
        "sport_id":       None,
        "league":         "UNRIVALED",
        "team":           DISPLAY_TEAM.get(team_slug, ""),
        "position":       position or "",
        "_unrivaled":     True,
        "_unrivaled_slug": slug,
        "_year":          season,
        "_norm_name":     name.lower(),
        "sportradar_id":  None,
        "college":        None,
        "jersey":         jersey or None,
        "height":         height or None,
        "weight":         None,
    }


def main():
    print("Scraping Unrivaled players index (2026 roster) ...")
    players_index = scrape_players_index()
    print(f"  → {len(players_index)} players in 2026 index")

    print("Scraping 2026 head coaches ...")
    coach_2026: dict[str, str] = {}
    for slug in TEAM_SLUGS_2026:
        hc = scrape_team_head_coach(slug)
        if hc:
            coach_2026[slug] = hc
            print(f"  {slug}: {hc}")
        time.sleep(0.3)

    season_stats: dict[int, list[dict]] = {}
    for yr in SEASONS:
        print(f"Scraping season {yr} player stats ...")
        rows = scrape_season_stats(yr)
        season_stats[yr] = rows
        print(f"  → {len(rows)} player rows for {yr}")
        time.sleep(0.5)

    # ── Build flat player + stat records ──────────────────────────────────
    next_id = SYNTHETIC_ID_START
    players_out: list[dict] = []
    stats_out: list[dict] = []
    # Map (season, name_key) → id  so stats line up
    season_player_ids: dict[tuple[int, str], int] = {}

    # 2025 — hardcoded base rosters
    for team_slug, (hc_name, names) in ROSTERS_2025.items():
        for nm in names:
            pid = next_id; next_id += 1
            rec = build_player_record(pid, nm, 2025, team_slug, None, "", "", "")
            players_out.append(rec)
            season_player_ids[(2025, name_key(nm))] = pid
    # 2025 relief players
    for nm, team_slug in RELIEF_2025.items():
        pid = next_id; next_id += 1
        rec = build_player_record(pid, nm, 2025, team_slug, None, "", "", "")
        rec["_relief"] = True
        players_out.append(rec)
        season_player_ids[(2025, name_key(nm))] = pid

    # 2025 head coaches as synthetic players (coach role)
    for team_slug, (hc_name, _) in ROSTERS_2025.items():
        pid = next_id; next_id += 1
        rec = build_player_record(pid, hc_name, 2025, team_slug, None, "Head Coach", "", "")
        rec["_coach"] = True
        players_out.append(rec)

    # 2026 — from /players index
    for nk, info in players_index.items():
        pid = next_id; next_id += 1
        rec = build_player_record(
            pid, info["name"], 2026, info["team_slug"],
            info["slug"], info["position"], info["height"], info["jersey"],
        )
        players_out.append(rec)
        season_player_ids[(2026, nk)] = pid

    # 2026 head coaches
    for team_slug, hc_name in coach_2026.items():
        pid = next_id; next_id += 1
        rec = build_player_record(pid, hc_name, 2026, team_slug, None, "Head Coach", "", "")
        rec["_coach"] = True
        players_out.append(rec)

    # ── Stats rows from /stats/player ────────────────────────────────────
    for yr, rows in season_stats.items():
        for row in rows:
            nk = name_key(row["name"])
            pid = season_player_ids.get((yr, nk))
            if pid is None:
                # Player appeared in stats but not in our roster (e.g. 2025
                # relief player we missed) — create a stub record.
                pid = next_id; next_id += 1
                team_slug = ""
                # Try to find from 2026 index for team continuity
                idx = players_index.get(nk)
                if idx:
                    team_slug = idx["team_slug"]
                rec = build_player_record(pid, row["name"], yr, team_slug,
                                          row.get("slug"), "", "", "")
                players_out.append(rec)
                season_player_ids[(yr, nk)] = pid
            for stat_name, value in row["stats"].items():
                if value == 0.0 and stat_name not in {"games_played"}:
                    continue
                stats_out.append({
                    "player_id": pid,
                    "week":      1,
                    "stat":      stat_name,
                    "value":     value,
                    "game_id":   f"UNRIVALED_{yr}_SEASON_TOTAL",
                    "_year":     yr,
                })

    # ── write outputs ─────────────────────────────────────────────────────
    PLAYERS_FILE.write_text(json.dumps(players_out, indent=2))
    STATS_FILE.write_text(json.dumps(stats_out, indent=2))
    STATE_FILE.write_text(json.dumps({
        "last_run": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seasons":  SEASONS,
        "player_records": len(players_out),
        "stat_rows": len(stats_out),
        "coach_2026": coach_2026,
    }, indent=2))

    print()
    print(f"Wrote {len(players_out)} player records → {PLAYERS_FILE.name}")
    print(f"Wrote {len(stats_out)} stat rows → {STATS_FILE.name}")
    print(f"Seasons: {SEASONS}")


if __name__ == "__main__":
    main()

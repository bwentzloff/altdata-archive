#!/usr/bin/env python3
"""
scrape_cfl.py — Incremental CFL historical stats + gamelogs backfill.

Source: footballdb.com
  - Team season stat pages (player discovery): /teams/cfl/{team}/stats/{year}
  - Player game logs (stat ingestion):        /players/{slug}/gamelogs/{year}

This scraper is intentionally player-page driven (not leaderboard-driven) so it
captures broad roster coverage and per-game logs where available.

Outputs (all in pipeline/raw/):
  cfl_historical_players.json  — synthetic player records (IDs start at 200000)
  cfl_historical_stats.json    — synthetic per-game stat rows
  cfl_historical_state.json    — incremental scrape state
"""

import argparse
import json
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW = BASE / "raw"
RAW.mkdir(exist_ok=True)

STATE_FILE = RAW / "cfl_historical_state.json"
PLAYERS_FILE = RAW / "cfl_historical_players.json"
STATS_FILE = RAW / "cfl_historical_stats.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

BASE_URL = "https://www.footballdb.com"
SYNTHETIC_ID_START = 200000
STATE_VERSION = 2

# Current DB generally has modern seasons; historical backfill targets <= 2022.
BACKFILL_MAX_YEAR = 2022
BACKFILL_MIN_YEAR_FALLBACK = 2016

STANDINGS_URL_TMPL = BASE_URL + "/standings/index.html?lg=CFL&yr={year}"
TEAM_STATS_URL_TMPL = BASE_URL + "/teams/cfl/{team_slug}/stats/{year}"
PLAYER_URL_TMPL = BASE_URL + "/players/{player_slug}"
PLAYER_GAMELOG_URL_TMPL = BASE_URL + "/players/{player_slug}/gamelogs/{year}"
PASSING_YEAR_DISCOVERY_URL = BASE_URL + "/statistics/cfl/player-stats/passing/2024"


def _norm(name: str) -> str:
    nfkd = unicodedata.normalize("NFD", str(name))
    ascii_n = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", ascii_n).strip().lower()


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))


def _get_soup(url: str) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        return BeautifulSoup(resp.text, "lxml")
    except Exception:
        return None


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())


def _position_from_text(text: str) -> str:
    txt = (text or "").lower()
    if "quarterback" in txt:
        return "QB"
    if "running back" in txt or "fullback" in txt:
        return "RB"
    if "wide receiver" in txt:
        return "WR"
    if "tight end" in txt:
        return "TE"
    if "linebacker" in txt:
        return "LB"
    if "defensive" in txt or "cornerback" in txt or "safety" in txt:
        return "DB"
    if "kicker" in txt:
        return "K"
    if "punter" in txt:
        return "P"
    return ""


def _discover_backfill_years(max_year: int = BACKFILL_MAX_YEAR) -> list[int]:
    years: set[int] = set()
    soup = _get_soup(PASSING_YEAR_DISCOVERY_URL)
    if soup:
        for a in soup.find_all("a", href=True):
            m = re.search(r"/statistics/cfl/player-stats/passing/(\d{4})", a["href"])
            if not m:
                continue
            y = int(m.group(1))
            if y <= max_year:
                years.add(y)

    if not years:
        years = set(range(BACKFILL_MIN_YEAR_FALLBACK, max_year + 1))

    return sorted(years)


def _discover_teams_for_year(year: int) -> list[str]:
    url = STANDINGS_URL_TMPL.format(year=year)
    soup = _get_soup(url)
    if not soup:
        return []

    teams: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(rf"/teams/cfl/([^/]+)/results/{year}$", href)
        if m:
            teams.add(m.group(1))
    return sorted(teams)


def _discover_players_for_team_year(team_slug: str, year: int) -> set[str]:
    url = TEAM_STATS_URL_TMPL.format(team_slug=team_slug, year=year)
    soup = _get_soup(url)
    if not soup:
        return set()

    players: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.match(r"^/players/([^/?#]+)$", href)
        if m and m.group(1) != "index.html":
            players.add(m.group(1))
    return players


def _fetch_player_profile(player_slug: str) -> tuple[str, str]:
    url = PLAYER_URL_TMPL.format(player_slug=player_slug)
    soup = _get_soup(url)
    if not soup:
        return player_slug.replace("-", " ").title(), ""

    # Keep names clean and stable across runs.
    name = ""
    h1 = soup.find("h1")
    if h1:
        name = _clean_name(h1.get_text(" ", strip=True))
        name = re.sub(r"\s+career\s+stats$", "", name, flags=re.I).strip()
    if not name:
        name = player_slug.replace("-", " ").title()

    meta_desc = ""
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        meta_desc = md["content"]

    return name, _position_from_text(meta_desc)


def _prefix_for_table(group_header: str, headers: list[str]) -> str:
    gh = (group_header or "").lower()
    hs = " ".join((h or "") for h in headers).lower()
    blob = f"{gh} {hs}"

    if "passing" in blob:
        return "pass"
    if "rushing" in blob:
        return "rush"
    if "receiving" in blob:
        return "recv"
    if "interception" in blob or "tackle" in blob or "sack" in blob:
        return "def"
    if "fumble" in blob or "recover" in blob:
        return "misc"
    if "return" in blob:
        return "ret"
    if "kicking" in blob or "touchdown" in blob:
        return "score"
    return "stat"


def _norm_stat_col(header: str) -> str:
    h = (header or "").strip().lower()
    h = h.replace("%", "pct")
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")
    return h


def _should_skip_col(header: str) -> bool:
    h = _norm_stat_col(header)
    if not h:
        return True
    if h in {"date", "team", "opp", "result"}:
        return True
    if "pct" in h:
        return True
    if h in {"rate", "ypa", "avg", "yr", "ypc", "ypr"}:
        return True
    return False


def _parse_num(raw: str) -> float | None:
    txt = (raw or "").strip().replace(",", "")
    if txt in {"", "--", "-"}:
        return None
    try:
        return float(txt)
    except Exception:
        return None


def _parse_mmddyy(date_text: str) -> tuple[int, int, int] | None:
    txt = (date_text or "").strip()
    try:
        dt = datetime.strptime(txt, "%m/%d/%y")
        return dt.year, dt.month, dt.day
    except Exception:
        return None


def _build_game_id(year: int, month: int, day: int, team: str, opp: str) -> str:
    team_u = re.sub(r"[^A-Z0-9]", "", (team or "").upper())
    opp_u = re.sub(r"[^A-Z0-9]", "", (opp or "").upper())

    if opp_u.startswith("@"):
        home = opp_u[1:]
        away = team_u
    elif opp_u.startswith("VS"):
        away = opp_u[2:]
        home = team_u
    else:
        # Unknown format: keep deterministic but parseable.
        away = team_u
        home = opp_u

    if not away:
        away = "UNK"
    if not home:
        home = "UNK"

    return f"FOOTBALL_CFL_{year}_{month}_{day}_{away}@{home}"


def _scrape_player_year_gamelog(player_slug: str, year: int, player_id: int) -> list[dict]:
    url = PLAYER_GAMELOG_URL_TMPL.format(player_slug=player_slug, year=year)
    soup = _get_soup(url)
    if not soup:
        return []

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue

        group_header = " ".join(c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"]))
        header_cells = rows[1].find_all(["th", "td"])
        headers = [c.get_text(" ", strip=True) for c in header_cells]
        if len(headers) < 4:
            continue

        prefix = _prefix_for_table(group_header, headers)

        for row in rows[2:]:
            cells = row.find_all("td")
            if len(cells) < len(headers):
                continue

            date_txt = cells[0].get_text(" ", strip=True)
            team_txt = cells[1].get_text(" ", strip=True)
            opp_txt = cells[2].get_text(" ", strip=True)

            parsed = _parse_mmddyy(date_txt)
            if not parsed:
                continue
            gy, gm, gd = parsed
            if gy != year:
                continue

            game_id = _build_game_id(gy, gm, gd, team_txt, opp_txt)

            for i, col_name in enumerate(headers[3:], 3):
                if i >= len(cells):
                    continue
                if _should_skip_col(col_name):
                    continue

                stat_col = _norm_stat_col(col_name)
                if not stat_col:
                    continue

                val = _parse_num(cells[i].get_text(" ", strip=True))
                if val is None:
                    continue
                if val == 0:
                    continue

                stat_name = f"{prefix}_{stat_col}"
                skey = (game_id, stat_name)
                if skey in seen:
                    continue
                seen.add(skey)

                out.append(
                    {
                        "player_id": player_id,
                        "week": 1,
                        "stat": stat_name,
                        "value": val,
                        "game_id": game_id,
                        "_year": year,
                    }
                )

    return out


def _new_state() -> dict:
    return {
        "version": STATE_VERSION,
        "next_id": SYNTHETIC_ID_START,
        "done_team_year": [],
        "done_player_year": [],
    }


def main():
    parser = argparse.ArgumentParser(description="CFL historical backfill scraper")
    parser.add_argument("--batch", type=int, default=5, help="Number of team/year units to scrape per run")
    parser.add_argument("--year", type=int, help="Scrape only this season")
    parser.add_argument("--status", action="store_true", help="Print progress and exit")
    parser.add_argument("--reset", action="store_true", help="Reset CFL historical files and state")
    parser.add_argument("--max-year", type=int, default=BACKFILL_MAX_YEAR, help="Upper year bound for backfill")
    args = parser.parse_args()

    if args.reset:
        state = _new_state()
        players_out = []
        stats_out = []
    else:
        state = load_json(STATE_FILE, _new_state())
        players_out = load_json(PLAYERS_FILE, [])
        stats_out = load_json(STATS_FILE, [])

    # State shape changed from leaderboard-mode to player-gamelog mode.
    if state.get("version") != STATE_VERSION:
        print("State version mismatch; resetting CFL historical state for new crawler mode.")
        state = _new_state()
        players_out = []
        stats_out = []

    next_id = int(state.get("next_id", SYNTHETIC_ID_START))
    done_team_year = set(state.get("done_team_year", []))
    done_player_year = set(state.get("done_player_year", []))

    # Existing-player maps
    slug_to_id: dict[str, int] = {}
    name_to_id: dict[str, int] = {}
    players_by_id: dict[int, dict] = {}
    for p in players_out:
        pid = int(p.get("id", 0))
        if pid:
            players_by_id[pid] = p
            if p.get("full_name"):
                name_to_id[p["full_name"]] = pid
            if p.get("_fdb_slug"):
                slug_to_id[p["_fdb_slug"]] = pid

    # Existing stats key set prevents duplicate appends across reruns.
    stat_keys: set[tuple[int, str, str, int]] = set()
    for r in stats_out:
        pid = int(r.get("player_id", 0))
        gid = str(r.get("game_id", ""))
        stat = str(r.get("stat", ""))
        wk = int(r.get("week", 1) or 1)
        if pid and gid and stat:
            stat_keys.add((pid, gid, stat, wk))

    years = [args.year] if args.year else _discover_backfill_years(max_year=args.max_year)
    work: list[tuple[str, int]] = []
    for year in years:
        for team_slug in _discover_teams_for_year(year):
            key = f"{team_slug}/{year}"
            if key not in done_team_year:
                work.append((team_slug, year))

    if args.status:
        total = 0
        for year in years:
            total += len(_discover_teams_for_year(year))
        print(f"CFL historical teams: {len(done_team_year)}/{total} team-years done")
        print(f"CFL historical players done: {len(done_player_year)} player-years")
        print(f"Players: {len(players_out)}  Stat rows: {len(stats_out)}")
        return

    if not work:
        print("All CFL historical team-years already scraped.")
        return

    batch = work[: args.batch]
    for team_slug, year in batch:
        team_key = f"{team_slug}/{year}"
        print(f"  Scraping CFL team-year {team_key} ...")
        player_slugs = sorted(_discover_players_for_team_year(team_slug, year))
        print(f"    discovered {len(player_slugs)} players")

        for player_slug in player_slugs:
            py_key = f"{player_slug}/{year}"
            if py_key in done_player_year:
                continue

            if player_slug in slug_to_id:
                pid = slug_to_id[player_slug]
            else:
                full_name, position = _fetch_player_profile(player_slug)
                pid = name_to_id.get(full_name)
                if not pid:
                    pid = next_id
                    next_id += 1
                    record = {
                        "id": pid,
                        "full_name": full_name,
                        "short_name": full_name,
                        "first_name": full_name.split()[0] if full_name else "",
                        "last_name": " ".join(full_name.split()[1:]) if full_name else "",
                        "sport_id": None,
                        "league": "CFL",
                        "team": "",
                        "position": position,
                        "_cfl_historical": True,
                        "_norm_name": _norm(full_name),
                        "_fdb_slug": player_slug,
                        "sportradar_id": None,
                        "college": None,
                        "jersey": None,
                        "height": None,
                        "weight": None,
                    }
                    players_out.append(record)
                    players_by_id[pid] = record
                    name_to_id[full_name] = pid
                else:
                    rec = players_by_id.get(pid)
                    if rec and not rec.get("_fdb_slug"):
                        rec["_fdb_slug"] = player_slug
                slug_to_id[player_slug] = pid

            rows = _scrape_player_year_gamelog(player_slug, year, pid)
            added = 0
            for row in rows:
                key = (
                    int(row["player_id"]),
                    str(row["game_id"]),
                    str(row["stat"]),
                    int(row.get("week", 1) or 1),
                )
                if key in stat_keys:
                    continue
                stat_keys.add(key)
                stats_out.append(row)
                added += 1

            done_player_year.add(py_key)
            if added > 0:
                print(f"      {player_slug}/{year}: +{added} stat rows")

            # Write progress incrementally so interrupted runs can resume cleanly.
            state["version"] = STATE_VERSION
            state["next_id"] = next_id
            state["done_team_year"] = sorted(done_team_year)
            state["done_player_year"] = sorted(done_player_year)
            save_json(STATE_FILE, state)
            save_json(PLAYERS_FILE, players_out)
            save_json(STATS_FILE, stats_out)
            time.sleep(0.1)

        done_team_year.add(team_key)
        state["version"] = STATE_VERSION
        state["next_id"] = next_id
        state["done_team_year"] = sorted(done_team_year)
        state["done_player_year"] = sorted(done_player_year)
        save_json(STATE_FILE, state)
        save_json(PLAYERS_FILE, players_out)
        save_json(STATS_FILE, stats_out)

    remaining = len(work) - len(batch)
    print(f"Done. {remaining} team-years remaining.")
    print(f"Total players: {len(players_out)}, stat rows: {len(stats_out)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scrape_curling.py - International curling data from World Curling Results.

Primary source:
  https://results.worldcurling.org/

This scraper collects championship results, match scores, and player rosters
from World Curling's historical results pages.

Outputs (pipeline/raw/):
  curling_players.json
  curling_stats.json
  curling_games.json
  curling_state.json

Notes:
- Focuses on international championships (World Curling data source).
- U.S. teams are included whenever present in those championships.
"""

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW = BASE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "curling_players.json"
STATS_FILE = RAW / "curling_stats.json"
GAMES_FILE = RAW / "curling_games.json"
STATE_FILE = RAW / "curling_state.json"

SYNTHETIC_ID_START = 1_700_000
ROOT = "https://results.worldcurling.org"
CZ_ROOT = "https://www.curlingzone.com"

# Championship type routes that reliably list historical events.
TYPE_IDS = [1, 2, 4, 5, 7, 8, 16, 22, 27, 30, 33, 36, 37, 38, 39]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://results.worldcurling.org/",
}


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", str(s)).strip().lower()
    return re.sub(r"[\s_]+", "-", s)


def fetch_soup(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {url}")
            return None
        return BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        print(f"  Error fetching {url}: {exc}")
        return None


def championship_ids_for_year(year: int) -> list[int]:
    soup = fetch_soup(f"{ROOT}/Championship/Year/{year}")
    if not soup:
        return []
    ids = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"/Championship/Details/(\d+)", a["href"])
        if m:
            ids.add(int(m.group(1)))
    return sorted(ids)


def championship_ids_from_types(type_ids: list[int] | None = None) -> list[int]:
    ids = set()
    for t in (type_ids or TYPE_IDS):
        soup = fetch_soup(f"{ROOT}/Championship/Type/{t}")
        if not soup:
            continue
        for a in soup.find_all("a", href=True):
            m = re.search(r"/Championship/Details/(\d+)", a["href"])
            if m:
                ids.add(int(m.group(1)))
    return sorted(ids)


def league_from_title(title: str) -> str:
    t = (title or "").lower()
    if "world" in t:
        return "WCF-WORLD"
    if "european" in t:
        return "WCF-EUROPE"
    if "pan continental" in t:
        return "WCF-PANCONT"
    if "olympic" in t or "paralympic" in t:
        return "WCF-OLYMPIC"
    if "qualification" in t or "pre-qualifier" in t:
        return "WCF-QUAL"
    return "WCF-OTHER"


def parse_title_and_year(details_soup: BeautifulSoup, fallback_year: int) -> tuple[str, int]:
    h1 = details_soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else "World Curling Championship"
    m = re.search(r"(20\d{2})", title)
    if m:
        year = int(m.group(1))
    else:
        # Fallback: derive season year from visible date ranges on details page.
        txt = details_soup.get_text(" ", strip=True)
        years = [int(y) for y in re.findall(r"\b(20\d{2})\b", txt)]
        year = max(years) if years else fallback_year
    return title, year


def extract_score(val: str) -> int | None:
    v = (val or "").strip()
    if v.isdigit():
        return int(v)
    return None


def parse_date(text: str, year_hint: int) -> str:
    if not text:
        return f"{year_hint}-01-01"
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)", text)
    if not m:
        m2 = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
        if m2:
            dt = datetime.strptime(m2.group(1), "%m/%d/%Y")
            return dt.strftime("%Y-%m-%d")
        return f"{year_hint}-01-01"
    dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%m/%d/%Y %I:%M %p")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def players_for_h5_block(h5_tag) -> list[tuple[str, str]]:
    """Return list of (person_key, person_name) until next h5/table/h3."""
    out: list[tuple[str, str]] = []
    seen = set()

    for el in h5_tag.next_elements:
        if getattr(el, "name", None) in {"h5", "table", "h3"}:
            break
        if getattr(el, "name", None) == "a":
            href = el.get("href", "")
            m = re.search(r"/Person/Details/(\d+)", href)
            if not m:
                continue
            pid = m.group(1)
            key = f"wcf-{pid}"
            name = el.get_text(" ", strip=True)
            pair = (key, name)
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
    return out


def parse_display_results(
    champ_id: int,
    champ_title: str,
    season_year: int,
    league_label: str,
):
    """
    Parse /Championship/DisplayResults and return:
      games, roster_by_game
    where roster_by_game[game_id][team_name] = [(person_key, person_name), ...]
    """
    url = (
        f"{ROOT}/Championship/DisplayResults"
        f"?tournamentId={champ_id}&associationId=0&teamNumber=0&drawNumber=0"
    )
    soup = fetch_soup(url)
    if not soup:
        return [], {}

    games = []
    roster_by_game = {}

    def _extract_team_name(cols: list[str]) -> str:
        for c in cols[:3]:
            v = (c or "").strip()
            if not v or v == "*":
                continue
            # Skip draw-letter column like "A", "B", etc.
            if re.fullmatch(r"[A-Z]", v):
                continue
            return v
        return ""

    draw_idx = 0
    h3s = soup.find_all("h3")
    for h3 in h3s:
        draw_label = h3.get_text(" ", strip=True)
        if not draw_label:
            continue
        draw_idx += 1

        section_nodes = []
        for el in h3.next_elements:
            if el is h3:
                continue
            if getattr(el, "name", None) == "h3":
                break
            if getattr(el, "name", None) is not None:
                section_nodes.append(el)

        section_text = " ".join(n.get_text(" ", strip=True) for n in section_nodes)
        date_str = parse_date(section_text, season_year)

        tables = [n for n in section_nodes if getattr(n, "name", None) == "table"]
        game_idx = 0

        for table in tables:
            rows = []
            for tr in table.find_all("tr"):
                cols = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
                if len(cols) >= 3 and cols[1].strip():
                    rows.append(cols)
            if len(rows) < 2:
                continue

            team_a = _extract_team_name(rows[0])
            team_b = _extract_team_name(rows[1])
            if not team_a or not team_b:
                continue
            score_a = extract_score(rows[0][-1])
            score_b = extract_score(rows[1][-1])

            game_idx += 1
            game_id = f"curling-{slugify(league_label)}-{season_year}-{champ_id}-{draw_idx}-{game_idx}"

            # Collect the next two h5 blocks before next table/h3
            team_blocks = []
            for el in table.next_elements:
                n = getattr(el, "name", None)
                if n in {"table", "h3"}:
                    break
                if n == "h5":
                    team_blocks.append(el)

            team_rosters = {}
            for h5 in team_blocks[:2]:
                team_name = h5.get_text(" ", strip=True)
                if not team_name:
                    continue
                team_rosters[team_name] = players_for_h5_block(h5)

            winner = ""
            if score_a is not None and score_b is not None:
                if score_a > score_b:
                    winner = team_a
                elif score_b > score_a:
                    winner = team_b

            status_line = "Final"
            if winner:
                status_line = f"{winner} win"

            games.append({
                "id": f"curling-{champ_id}-{draw_idx}-{game_idx}",
                "sport_id": None,
                "status": "Final",
                "start_time": date_str,
                "team_home": team_b,
                "team_away": team_a,
                "created_at": None,
                "updated_at": None,
                "week": draw_idx,
                "game_id": game_id,
                "score_home": score_b,
                "score_away": score_a,
                "channel": "",
                "streaming_link": None,
                "period": None,
                "time_left": None,
                "active": 0,
                "possession_home": None,
                "possession_away": None,
                "record_home": None,
                "record_away": None,
                "status_line": status_line,
                "spread_home": None,
                "spread_away": None,
                "moneyline_home": None,
                "moneyline_away": None,
                "total_home": None,
                "total_away": None,
                "league": league_label,
                "championship": champ_title,
                "draw": draw_label,
            })
            roster_by_game[game_id] = team_rosters

    return games, roster_by_game


def _clean_cz_team_name(name: str) -> str:
    # Example: "Canada (Ide/Hen)" -> "Canada"
    n = re.sub(r"\s*\([^)]*\)\s*$", "", (name or "").strip())
    # Remove occasional leading time-zone artifacts captured from score rows.
    n = re.sub(r"^(?:am|pm)\s+(?:MT|ET|CT|PT)\s+", "", n, flags=re.IGNORECASE)
    n = re.sub(r"^(?:\d{1,2}:\d{2}\s*(?:am|pm)\s+)?(?:MT|ET|CT|PT)\s+", "", n, flags=re.IGNORECASE)
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def _add_cz_roster_aliases(rosters: dict[str, list[tuple[str, str]]], team_name: str, players: list[tuple[str, str]]):
    """Store roster under full team name and common short aliases."""
    words = team_name.split()
    aliases = {team_name}
    if words:
        aliases.add(words[-1])
    if len(words) >= 2:
        aliases.add(" ".join(words[-2:]))
    if len(words) >= 3:
        aliases.add(" ".join(words[-3:]))

    for alias in aliases:
        a = _clean_cz_team_name(alias)
        if a and a not in rosters:
            rosters[a] = players


def discover_curlingzone_event_ids(limit: int = 10) -> list[int]:
    """Discover recent event IDs from CurlingZone home pages."""
    ids = []
    seen = set()
    urls = [
        "https://home.curlingzone.com/",
        "https://www.curlingzone.com/",
    ]
    for url in urls:
        soup = fetch_soup(url)
        if not soup:
            continue
        for a in soup.find_all("a", href=True):
            h = a["href"]
            m = re.search(r"eventid=(\d+)", h)
            if not m:
                continue
            eid = int(m.group(1))
            if eid in seen:
                continue
            seen.add(eid)
            ids.append(eid)
            if len(ids) >= limit:
                return ids
    return ids


def parse_curlingzone_event_main(event_id: int) -> tuple[str, int, str, str] | None:
    """Return (title, year, location, date_range_text) for a CurlingZone event."""
    url = f"{CZ_ROOT}/event.php?task=Main&eventid={event_id}"
    soup = fetch_soup(url)
    if not soup:
        return None

    text = soup.get_text(" ", strip=True)
    title = "CurlingZone Event"
    t = soup.title.get_text(" ", strip=True) if soup.title else ""
    if t:
        title = t.split("-")[0].strip()

    # Capture date range such as: "May 5 - 10, 2026"
    date_range = ""
    m = re.search(
        r"([A-Z][a-z]+\s+\d{1,2}\s*-\s*\d{1,2},\s*20\d{2})",
        text,
    )
    if m:
        date_range = m.group(1)

    year_match = re.search(r"(20\d{2})", date_range or text)
    year = int(year_match.group(1)) if year_match else datetime.now(UTC).year

    location = ""
    mloc = re.search(r"\b([A-Za-z .'-]+,\s*[A-Z]{2})\b", text)
    if mloc:
        location = mloc.group(1).strip()

    return title, year, location, date_range


def parse_curlingzone_team_rosters(event_id: int) -> dict[str, list[tuple[str, str]]]:
    """Parse event team pages and return team name -> [(person_key, person_name), ...]."""
    rosters: dict[str, list[tuple[str, str]]] = {}
    teams_url = f"{CZ_ROOT}/event.php?view=Teams&eventid={event_id}"
    soup = fetch_soup(teams_url)
    if not soup:
        return rosters

    team_links = []
    team_name_by_id: dict[str, str] = {}
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "view=Team" not in href or "teamid=" not in href:
            continue
        tm = re.search(r"teamid=(\d+)", href)
        if tm:
            display = _clean_cz_team_name(a.get_text(" ", strip=True))
            if display:
                team_name_by_id[tm.group(1)] = display
        full = href if href.startswith("http") else f"{CZ_ROOT}/{href.lstrip('/')}"
        if full in seen:
            continue
        seen.add(full)
        team_links.append(full)

    for turl in team_links:
        tsoup = fetch_soup(turl)
        if not tsoup:
            continue

        # Team name is usually in "Team: <name>" block on the page text.
        ttext = tsoup.get_text(" ", strip=True)
        team_name = ""
        mteam = re.search(r"\bTeam:\s*([A-Za-z0-9 .,'&()/-]+?)\s+(?:Game|Watch|Statistics|Scores|Draw)\b", ttext)
        if mteam:
            team_name = _clean_cz_team_name(mteam.group(1))

        players = []
        pseen = set()
        for a in tsoup.find_all("a", href=True):
            pm = re.search(r"player\.php\?playerid=(\d+)", a["href"])
            if not pm:
                continue
            pname = a.get_text(" ", strip=True)
            if not pname:
                continue
            pkey = f"cz-{pm.group(1)}"
            if (pkey, pname) in pseen:
                continue
            pseen.add((pkey, pname))
            players.append((pkey, pname))

        tid_m = re.search(r"teamid=(\d+)", turl)
        if (not team_name) and tid_m:
            team_name = team_name_by_id.get(tid_m.group(1), "")

        if not team_name and players:
            # Last fallback: keep a stable synthetic team key.
            team_name = f"Team {tid_m.group(1)}" if tid_m else "Unknown Team"

        if team_name and players:
            _add_cz_roster_aliases(rosters, team_name, players)

    return rosters


def parse_curlingzone_games(event_id: int, event_year: int, league_label: str):
    """
    Parse CurlingZone scores and playoffs pages.

    Returns list of game dicts compatible with pipeline raw games format.
    """
    league_slug = slugify(league_label)
    pages = [
        f"{CZ_ROOT}/event.php?view=Scores&eventid={event_id}",
        f"{CZ_ROOT}/event.php?view=Playoffs&eventid={event_id}",
    ]
    games = []
    seen_ids = set()

    for url in pages:
        soup = fetch_soup(url)
        if not soup:
            continue

        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if not cells or "Final" not in cells:
                continue

            row_text = " ".join(cells)
            gm_ids = re.findall(r"showgameid=(\d+)", str(tr))

            matches = re.findall(
                r"([A-Za-z][A-Za-z0-9 .,'&()/-]+?)\s+(\d+)\s+Final\s+([A-Za-z][A-Za-z0-9 .,'&()/-]+?)\s+(\d+)",
                row_text,
            )
            if not matches:
                continue

            for idx, (away_raw, sa, home_raw, sh) in enumerate(matches, start=1):
                away = _clean_cz_team_name(away_raw)
                home = _clean_cz_team_name(home_raw)
                if not away or not home:
                    continue

                gid_src = gm_ids[idx - 1] if idx - 1 < len(gm_ids) else f"{event_id}-{idx}"
                game_id = f"curling-{league_slug}-{event_year}-{event_id}-{gid_src}"
                if game_id in seen_ids:
                    continue
                seen_ids.add(game_id)

                score_away = int(sa)
                score_home = int(sh)
                winner = away if score_away > score_home else (home if score_home > score_away else "")
                status_line = f"{winner} win" if winner else "Final"

                games.append({
                    "id": f"curling-event-{event_id}-{gid_src}",
                    "sport_id": None,
                    "status": "Final",
                    "start_time": f"{event_year}-01-01",
                    "team_home": home,
                    "team_away": away,
                    "created_at": None,
                    "updated_at": None,
                    "week": 1,
                    "game_id": game_id,
                    "score_home": score_home,
                    "score_away": score_away,
                    "channel": "",
                    "streaming_link": None,
                    "period": None,
                    "time_left": None,
                    "active": 0,
                    "possession_home": None,
                    "possession_away": None,
                    "record_home": None,
                    "record_away": None,
                    "status_line": status_line,
                    "spread_home": None,
                    "spread_away": None,
                    "moneyline_home": None,
                    "moneyline_away": None,
                    "total_home": None,
                    "total_away": None,
                    "league": league_label,
                    "championship": f"CurlingZone Event {event_id}",
                    "draw": "",
                })

    return games


def main():
    ap = argparse.ArgumentParser(description="Scrape curling data from World Curling")
    ap.add_argument("--reset", action="store_true", help="Reset outputs and state")
    ap.add_argument("--start-year", type=int, default=2024)
    ap.add_argument("--end-year", type=int, default=datetime.now(UTC).year)
    ap.add_argument("--force", action="store_true", help="Reprocess championships in state")
    ap.add_argument("--include-curlingzone", action="store_true", help="Include CurlingZone events")
    ap.add_argument("--cz-event-limit", type=int, default=8, help="Max recent CurlingZone events to process")
    ap.add_argument("--status", action="store_true", help="Print status and exit")
    args = ap.parse_args()

    state = load_json(STATE_FILE, {}) if not args.reset else {}
    done = set(state.get("done_championships", []))
    done_cz = set(state.get("done_cz_events", []))
    id_map = state.get("id_map", {})
    next_id = state.get("next_id", SYNTHETIC_ID_START)

    players_out = [] if args.reset else load_json(PLAYERS_FILE, [])
    stats_out = [] if args.reset else load_json(STATS_FILE, [])
    games_out = [] if args.reset else load_json(GAMES_FILE, [])

    if args.status:
        print("Curling scraper status:")
        print(f"  Done championships: {len(done)}")
        print(f"  Players           : {len(players_out)}")
        print(f"  Stat rows         : {len(stats_out)}")
        print(f"  Games             : {len(games_out)}")
        print(f"  Next ID           : {next_id}")
        return

    player_index = {p["id"]: p for p in players_out}
    stored_stat_keys = {(r["player_id"], r["game_id"], r["stat"]) for r in stats_out}
    stored_game_ids = {str(g.get("game_id", "")) for g in games_out if g.get("game_id")}

    def ensure_id(pid_key: str) -> int:
        nonlocal next_id
        if pid_key not in id_map:
            id_map[pid_key] = next_id
            next_id += 1
        return id_map[pid_key]

    def flush():
        save_json(PLAYERS_FILE, players_out)
        save_json(STATS_FILE, stats_out)
        save_json(GAMES_FILE, games_out)
        state["done_championships"] = sorted(done)
        state["done_cz_events"] = sorted(done_cz)
        state["id_map"] = id_map
        state["next_id"] = next_id
        save_json(STATE_FILE, state)

    years = list(range(args.start_year, args.end_year + 1))
    print(f"Collecting championships for years: {years}")

    # Year pages are incomplete on some seasons; always combine with type pages.
    ids_from_year = set()
    for year in years:
        yids = championship_ids_for_year(year)
        ids_from_year.update(yids)
        print(f"  {year}: {len(yids)} championships from year route")

    ids_from_type = set(championship_ids_from_types())
    print(f"  Type routes: {len(ids_from_type)} championships")

    all_ids = sorted(ids_from_year | ids_from_type)
    print(f"Total unique championship IDs discovered: {len(all_ids)}")

    for champ_id in all_ids:
        champ_key = str(champ_id)
        if champ_key in done and not args.force:
            continue

        details = fetch_soup(f"{ROOT}/Championship/Details/{champ_id}")
        if not details:
            continue

        title, season_year = parse_title_and_year(details, args.start_year)
        if season_year < args.start_year or season_year > args.end_year:
            continue
        league_label = league_from_title(title)

        print(f"\n[{champ_id}] {title} -> {league_label}")
        games, roster_by_game = parse_display_results(champ_id, title, season_year, league_label)
        if not games:
            print("  No games parsed")
            done.add(champ_key)
            flush()
            continue

        add_games = 0
        add_players = 0
        add_stats = 0

        for g in games:
            game_id = g["game_id"]
            if game_id in stored_game_ids and not args.force:
                continue

            roster_map = roster_by_game.get(game_id, {})
            away = g.get("team_away", "")
            home = g.get("team_home", "")
            score_away = g.get("score_away")
            score_home = g.get("score_home")

            if game_id not in stored_game_ids or args.force:
                games_out.append(g)
                stored_game_ids.add(game_id)
                add_games += 1

            for team_name in (away, home):
                players = roster_map.get(team_name, [])
                points_for = score_away if team_name == away else score_home
                points_against = score_home if team_name == away else score_away
                win = 0.0
                loss = 0.0
                if score_away is not None and score_home is not None:
                    if team_name == away:
                        win = 1.0 if score_away > score_home else 0.0
                        loss = 1.0 if score_away < score_home else 0.0
                    else:
                        win = 1.0 if score_home > score_away else 0.0
                        loss = 1.0 if score_home < score_away else 0.0

                for pid_key, person_name in players:
                    num_id = ensure_id(pid_key)
                    if num_id not in player_index:
                        rec = {
                            "id": num_id,
                            "full_name": person_name,
                            "team": team_name,
                            "position": "",
                            "sport_id": None,
                            "league": league_label,
                            "jersey": None,
                            "college": None,
                            "college_stats": None,
                            "height": None,
                            "weight": None,
                            "birth_date": "",
                            "nationality": "",
                            "gender": "",
                            "_wcf_id": pid_key,
                            "_leagues": [league_label],
                        }
                        players_out.append(rec)
                        player_index[num_id] = rec
                        add_players += 1
                    else:
                        ex = player_index[num_id]
                        ex.setdefault("_leagues", [])
                        if league_label not in ex["_leagues"]:
                            ex["_leagues"].append(league_label)

                    stat_rows = [
                        ("match_played", 1.0),
                        ("match_win", win),
                        ("match_loss", loss),
                    ]
                    if points_for is not None:
                        stat_rows.append(("team_points_for", float(points_for)))
                    if points_against is not None:
                        stat_rows.append(("team_points_against", float(points_against)))

                    for stat_name, val in stat_rows:
                        key = (num_id, game_id, stat_name)
                        if key in stored_stat_keys and not args.force:
                            continue
                        stats_out.append({
                            "player_id": num_id,
                            "week": 1,
                            "stat": stat_name,
                            "value": val,
                            "game_id": game_id,
                            "_year": season_year,
                            "_league": league_label,
                            "_team_id": slugify(team_name),
                        })
                        stored_stat_keys.add(key)
                        add_stats += 1

        done.add(champ_key)
        flush()
        print(
            f"  Added {add_games} games, {add_players} players, {add_stats} stat rows"
        )

    print(
        f"\nDone. {len(players_out)} players, {len(stats_out)} stat rows, {len(games_out)} games"
    )

    # Optional: CurlingZone ingestion for additional tours/events.
    if args.include_curlingzone:
        print("\nProcessing CurlingZone events …")
        cz_event_ids = discover_curlingzone_event_ids(limit=args.cz_event_limit)
        print(f"  Discovered {len(cz_event_ids)} recent events")

        for eid in cz_event_ids:
            ekey = str(eid)
            if ekey in done_cz and not args.force:
                continue

            info = parse_curlingzone_event_main(eid)
            if not info:
                continue
            title, event_year, location, date_range = info
            if event_year < args.start_year or event_year > args.end_year:
                continue

            league_label = "CURLING-EVENTS"
            print(f"  [CZ {eid}] {title} ({event_year})")

            rosters = parse_curlingzone_team_rosters(eid)
            games = parse_curlingzone_games(eid, event_year, league_label)

            add_games = 0
            add_players = 0
            add_stats = 0

            for g in games:
                game_id = g["game_id"]
                if game_id in stored_game_ids and not args.force:
                    continue

                away = g.get("team_away", "")
                home = g.get("team_home", "")
                score_away = g.get("score_away")
                score_home = g.get("score_home")

                if game_id not in stored_game_ids or args.force:
                    g["championship"] = title
                    if location:
                        g["venue"] = location
                    if date_range:
                        g["date_range"] = date_range
                    games_out.append(g)
                    stored_game_ids.add(game_id)
                    add_games += 1

                for team_name in (away, home):
                    players = rosters.get(team_name, [])
                    points_for = score_away if team_name == away else score_home
                    points_against = score_home if team_name == away else score_away
                    win = 0.0
                    loss = 0.0
                    if score_away is not None and score_home is not None:
                        if team_name == away:
                            win = 1.0 if score_away > score_home else 0.0
                            loss = 1.0 if score_away < score_home else 0.0
                        else:
                            win = 1.0 if score_home > score_away else 0.0
                            loss = 1.0 if score_home < score_away else 0.0

                    for pid_key, person_name in players:
                        num_id = ensure_id(pid_key)
                        if num_id not in player_index:
                            rec = {
                                "id": num_id,
                                "full_name": person_name,
                                "team": team_name,
                                "position": "",
                                "sport_id": None,
                                "league": league_label,
                                "jersey": None,
                                "college": None,
                                "college_stats": None,
                                "height": None,
                                "weight": None,
                                "birth_date": "",
                                "nationality": "",
                                "gender": "",
                                "_cz_id": pid_key,
                                "_leagues": [league_label],
                            }
                            players_out.append(rec)
                            player_index[num_id] = rec
                            add_players += 1
                        else:
                            ex = player_index[num_id]
                            ex.setdefault("_leagues", [])
                            if league_label not in ex["_leagues"]:
                                ex["_leagues"].append(league_label)

                        stat_rows = [
                            ("match_played", 1.0),
                            ("match_win", win),
                            ("match_loss", loss),
                        ]
                        if points_for is not None:
                            stat_rows.append(("team_points_for", float(points_for)))
                        if points_against is not None:
                            stat_rows.append(("team_points_against", float(points_against)))

                        for stat_name, val in stat_rows:
                            key = (num_id, game_id, stat_name)
                            if key in stored_stat_keys and not args.force:
                                continue
                            stats_out.append({
                                "player_id": num_id,
                                "week": 1,
                                "stat": stat_name,
                                "value": val,
                                "game_id": game_id,
                                "_year": event_year,
                                "_league": league_label,
                                "_team_id": slugify(team_name),
                            })
                            stored_stat_keys.add(key)
                            add_stats += 1

            done_cz.add(ekey)
            flush()
            print(f"    Added {add_games} games, {add_players} players, {add_stats} stat rows")

        print(
            f"\nDone with CurlingZone. {len(players_out)} players, {len(stats_out)} stat rows, {len(games_out)} games"
        )


if __name__ == "__main__":
    main()

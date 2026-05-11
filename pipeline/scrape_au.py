#!/usr/bin/env python3
"""
scrape_au.py — Athletes Unlimited stats backfill
Source: auprosports.com (WordPress site, stats loaded dynamically)
        Wayback Machine fallback for historical seasons

Athletes Unlimited sports:
  - Softball  (since 2020)
  - Lacrosse  (since 2021)
  - Basketball (since 2022)
  - Volleyball (since 2022)

Because the AU site loads stats via JavaScript (WordPress + AJAX), this
scraper tries multiple approaches:
  1. AU WordPress admin-ajax.php endpoint with known action names
  2. Wayback Machine snapshots of the stats pages
  3. Graceful empty-output fallback if neither works

Outputs (all in pipeline/raw/):
  au_players.json   — synthetic player records (IDs starting at 800000)
  au_stats.json     — synthetic stat rows (season totals)
  au_raw.json       — cache of parsed data

Injection: build_data.py reads these and extends raw_players + raw_stats.
"""

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
RAW  = BASE / "raw"
RAW.mkdir(exist_ok=True)

PLAYERS_FILE = RAW / "au_players.json"
STATS_FILE   = RAW / "au_stats.json"
CACHE_FILE   = RAW / "au_raw.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

SYNTHETIC_ID_START = 800_000

AU_BASE = "https://auprosports.com"

# Sport-specific stats page paths and their year ranges
AU_SPORTS = [
    {
        "sport":  "Softball",
        "league": "AU Softball",
        "paths":  [
            "/softball/stats/",
            "/softball/statistics/",
            "/softball/leaderboard/",
            "/softball/athletes/",
            "/aux-softball/leaderboard/",
            "/aux-softball/athletes/",
        ],
        "years":  [2020, 2021, 2022, 2023, 2024],
        "stat_fields": {
            "AVG": "batting_avg", "HR": "home_runs", "RBI": "rbi",
            "H": "hits", "AB": "at_bats", "OPS": "ops",
        },
        "api_sport": "softball",
        "api_stat_types": "batting%26statTypes=pitching%26statTypes=fielding",
    },
    {
        "sport":  "Lacrosse",
        "league": "AU Lacrosse",
        "paths":  [
            "/lacrosse/stats/",
            "/lacrosse/statistics/",
            "/lacrosse/leaderboard/",
            "/lacrosse/athletes/",
        ],
        "years":  [2021, 2022, 2023, 2024],
        "stat_fields": {
            "G": "goals", "A": "assists", "PTS": "points",
            "SOG": "shots", "GB": "groundballs", "TO": "turnovers",
        },
        "api_sport": "lacrosse",
        "api_stat_types": "lacrosse_player%26statTypes=lacrosse_goalie",
    },
    {
        "sport":  "Basketball",
        "league": "AU Basketball",
        "paths":  [
            "/basketball/stats/",
            "/basketball/statistics/",
            "/basketball/leaderboard/",
            "/basketball/athletes/",
        ],
        "years":  [2022, 2023, 2024],
        "stat_fields": {
            "PPG": "points_per_game", "RPG": "rebounds_per_game",
            "APG": "assists_per_game", "PTS": "points",
            "REB": "rebounds", "AST": "assists",
        },
        "api_sport": "basketball",
        "api_stat_types": "basketball",
    },
    {
        "sport":  "Volleyball",
        "league": "AU Volleyball",
        "paths":  [
            "/volleyball/stats/",
            "/volleyball/statistics/",
            "/volleyball/leaderboard/",
            "/volleyball/athletes/",
        ],
        "years":  [2022, 2023, 2024],
        "stat_fields": {
            "Kills": "kills", "Aces": "aces", "Digs": "digs",
            "Blocks": "blocks", "Assists": "assists",
        },
        "api_sport": "volleyball",
        "api_stat_types": "volleyball",
    },
]

# Wayback timestamps to try per year (end of AU season, ~November)
YEAR_TIMESTAMPS = {
    2024: ["20241101", "20241201"],
    2023: ["20231101", "20231201"],
    2022: ["20221101", "20221201"],
    2021: ["20211101", "20211201"],
    2020: ["20201101", "20201201"],
}


def safe_float(v) -> float:
    if v is None or str(v).strip() in ("", "-", "—", ".000", ".---"):
        return 0.0
    try:
        f = float(str(v).replace(",", ""))
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def try_ajax_endpoint(sport_slug: str, action: str) -> list[dict]:
    """Try WordPress admin-ajax with a known action name."""
    _ = sport_slug
    url = f"{AU_BASE}/wp-admin/admin-ajax.php"
    payload = {"action": action}
    try:
        r = requests.post(url, data=payload, headers=HEADERS, timeout=15)
        if r.status_code == 200 and (r.text.startswith("[") or r.text.startswith("{")):
            data = r.json()
            if isinstance(data, list) and data:
                return data
            if isinstance(data, dict) and data.get("data"):
                return data["data"]
    except Exception:
        pass
    return []


def fetch_wayback(timestamp: str, url: str) -> str | None:
    wb_url = f"https://web.archive.org/web/{timestamp}000000/{url}"
    try:
        r = requests.get(wb_url, headers=HEADERS, timeout=45)
        if r.status_code == 200 and len(r.text) > 2000:
            return r.text
    except Exception:
        pass
    return None


def fetch_wayback_raw(timestamp: str, url: str) -> str | None:
    """Fetch raw archived response bytes rendered as text via Wayback id_ mode."""
    wb_url = f"https://web.archive.org/web/{timestamp}id_/{url}"
    try:
        r = requests.get(wb_url, headers=HEADERS, timeout=45)
        if r.status_code in (200, 211) and len(r.text) > 2:
            return r.text
    except Exception:
        pass
    return None


def find_cdx_snapshots(url: str, year: int) -> list[str]:
    cdx = (
        f"https://web.archive.org/cdx/search/cdx"
        f"?url={url}&output=json&limit=3&fl=timestamp"
        f"&filter=statuscode:200&from={year}0101&to={year}1231"
    )
    try:
        r = requests.get(cdx, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            rows = r.json()
            return [row[0] for row in rows[1:] if row]
    except Exception:
        pass
    return []


def is_cloudflare_block(html: str) -> bool:
    lower = (html or "").lower()
    return (
        "just a moment" in lower
        or "cf-browser-verification" in lower
        or "cf-chl-" in lower
        or "cloudflare" in lower and "challenge" in lower
    )


def extract_block_table_metadata(html: str) -> tuple[str, int | None]:
    """Extract sport slug and season id from AU block table root when present."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(id="block-leaderboard-root") or soup.find(id="block-stats-root")
    if not root:
        return "", None
    sport = (root.get("data-sport") or "").strip().lower()
    season_raw = (root.get("data-season") or "").strip()
    season_id = int(season_raw) if season_raw.isdigit() else None
    return sport, season_id


def fetch_wayback_proxy_json(timestamp: str, request_path: str) -> Any | None:
    """Fetch AU proxy JSON from Wayback id_ if captured."""
    request_url = f"{AU_BASE}/proxy.php?request={request_path}"
    text = fetch_wayback_raw(timestamp, request_url)
    if not text:
        return None
    body = text.strip()
    if not (body.startswith("{") or body.startswith("[")):
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def parse_proxy_stats_payload(payload: Any, sport_info: dict, year: int) -> list[dict]:
    """Normalize AU proxy stats payload into scraper row format."""
    if isinstance(payload, dict):
        data = payload.get("data", [])
    elif isinstance(payload, list):
        data = payload
    else:
        data = []
    return _extract_player_list_from_nd({"data": data}, sport_info["stat_fields"], sport_info["league"], year)


def fetch_rows_from_wayback_proxy(timestamps: list[str], sport_info: dict, year: int, season_hint: int | None) -> list[dict]:
    """Try AU proxy season/stats endpoints from Wayback captures."""
    sport_slug = sport_info.get("api_sport", "").lower()
    stat_types = sport_info.get("api_stat_types", "")
    if not sport_slug:
        return []

    season_candidates: set[int] = set()
    if season_hint:
        season_candidates.add(season_hint)

    # Pull known seasons first (this endpoint is commonly archived even when stats are sparse).
    for ts in timestamps[:4]:
        seasons_payload = fetch_wayback_proxy_json(ts, f"/api/seasons/v2/{sport_slug}")
        if isinstance(seasons_payload, dict):
            for row in seasons_payload.get("data", []):
                sid = row.get("seasonId")
                if isinstance(sid, int):
                    season_candidates.add(sid)

    for season_id in sorted(season_candidates, reverse=True):
        if stat_types:
            req = f"/api/stats/v2/{sport_slug}/season/{season_id}?statTypes={stat_types}"
        else:
            req = f"/api/stats/v2/{sport_slug}/season/{season_id}"
        for ts in timestamps[:5]:
            payload = fetch_wayback_proxy_json(ts, req)
            if not payload:
                continue
            rows = parse_proxy_stats_payload(payload, sport_info, year)
            if rows:
                return rows
    return []


def parse_stats_page(html: str, sport_info: dict, year: int) -> list[dict]:
    """Parse an AU stats page for a specific sport and year."""
    soup = BeautifulSoup(html, "html.parser")
    stat_fields = sport_info["stat_fields"]
    league = sport_info["league"]
    sport = sport_info["sport"]

    # Try __NEXT_DATA__
    m = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(\{.*?\})\s*</script>',
        html, re.DOTALL
    )
    if m:
        try:
            nd = json.loads(m.group(1))
            props = nd.get("props", {}).get("pageProps", {})
            raw_str = json.dumps(nd)
            if any(v.lower() in raw_str.lower() for v in stat_fields.keys()):
                rows = _extract_player_list_from_nd(nd, stat_fields, league, year)
                if rows:
                    return rows
        except (json.JSONDecodeError, KeyError):
            pass

    # Try WP JSON embedded in page
    for script in soup.find_all("script"):
        txt = script.string or ""
        if "window.auStatsData" in txt or "window._AU_STATS" in txt:
            m2 = re.search(r'(?:window\.auStatsData|window\._AU_STATS)\s*=\s*(\[.+?\]);', txt, re.DOTALL)
            if m2:
                try:
                    data = json.loads(m2.group(1))
                    rows = _extract_player_list_from_nd({"data": data}, stat_fields, league, year)
                    if rows:
                        return rows
                except json.JSONDecodeError:
                    pass

    # Fall back to HTML table
    tables = soup.find_all("table")
    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            continue
        has_name = any(h.lower() in ("player", "name", "athlete") for h in headers)
        has_stat = any(h in headers for h in stat_fields)
        if not (has_name and has_stat):
            continue

        rows = []
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if cells:
                row_dict = dict(zip(headers, cells))
                player = _normalize_au_row(row_dict, stat_fields, league, sport, year)
                if player:
                    rows.append(player)
        if rows:
            return rows

    return []


def _extract_player_list_from_nd(nd: dict, stat_fields: dict, league: str, year: int) -> list[dict]:
    """Search nested dict for player list and extract stats."""
    def find_lists(obj, depth=0):
        if depth > 5:
            return []
        if isinstance(obj, list) and len(obj) > 3:
            if all(isinstance(x, dict) for x in obj[:3]):
                first = obj[0]
                has_name = any(k in first for k in ("name", "fullName", "firstName", "playerName"))
                has_stat = any(k in first for k in stat_fields)
                if has_name or has_stat:
                    return [obj]
        if isinstance(obj, dict):
            results = []
            for v in obj.values():
                results.extend(find_lists(v, depth + 1))
            return results
        return []

    for candidate_list in find_lists(nd):
        rows = []
        for p in candidate_list:
            name = (
                p.get("playerName") or p.get("fullName") or p.get("name") or
                f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
            )
            if not name or len(name) < 3:
                continue
            row = {
                "name": name.strip(),
                "team": p.get("teamName") or p.get("team") or "",
                "position": p.get("position") or p.get("pos") or "",
                "_year": year,
                "_league": league,
            }
            for col, stat_key in stat_fields.items():
                row[stat_key] = safe_float(p.get(col) or p.get(stat_key) or p.get(col.lower()))
            rows.append(row)
        if rows:
            return rows

    return []


def _normalize_au_row(row_dict: dict, stat_fields: dict, league: str, sport: str, year: int) -> dict | None:
    name = ""
    for k in ("Player", "Name", "Athlete", "PLAYER"):
        if k in row_dict:
            name = row_dict[k]
            break
    if not name:
        return None

    out = {
        "name": name.strip(),
        "team": row_dict.get("Team") or row_dict.get("TEAM") or "",
        "position": row_dict.get("Pos") or row_dict.get("Position") or "",
        "_year": year,
        "_league": league,
    }
    for col, stat_key in stat_fields.items():
        out[stat_key] = safe_float(row_dict.get(col))
    return out


def fetch_all_data(cache: dict, reset: bool) -> list[dict]:
    if "au_rows" in cache and not reset:
        print(f"Using cached AU data ({len(cache['au_rows'])} rows)")
        return cache["au_rows"]

    all_rows: list[dict] = []

    for sport_info in AU_SPORTS:
        sport = sport_info["sport"]
        print(f"\n── AU {sport} ──────────────────────────")

        for year in reversed(sport_info["years"]):
            year_found = False

            for path in sport_info["paths"]:
                url = f"{AU_BASE}{path}"

                # Try CDX snapshots
                cdx_ts = find_cdx_snapshots(url, year)
                fallback_ts = YEAR_TIMESTAMPS.get(year, [])
                all_ts = cdx_ts + [t for t in fallback_ts if t not in cdx_ts]
                inferred_sport = ""
                inferred_season_id = None

                for ts in all_ts[:3]:
                    html = fetch_wayback(ts, url)
                    if not html:
                        time.sleep(0.5)
                        continue

                    inferred_sport, inferred_season_id = extract_block_table_metadata(html)

                    rows = parse_stats_page(html, sport_info, year)
                    if rows:
                        print(f"  ✓ {sport} {year}: {len(rows)} players (snapshot {ts})")
                        all_rows.extend(rows)
                        year_found = True
                        break
                    time.sleep(0.5)

                # Try Wayback archived API directly (id_ captures).
                if not year_found and all_ts:
                    rows = fetch_rows_from_wayback_proxy(all_ts, sport_info, year, inferred_season_id)
                    if rows:
                        print(f"  ✓ {sport} {year}: {len(rows)} players (Wayback API)")
                        all_rows.extend(rows)
                        year_found = True

                if year_found:
                    break

            if not year_found:
                # Try live site (may work for recent seasons)
                for path in sport_info["paths"]:
                    url = f"{AU_BASE}{path}"
                    try:
                        r = requests.get(url, headers=HEADERS, timeout=15)
                        if r.status_code == 200 and not is_cloudflare_block(r.text):
                            rows = parse_stats_page(r.text, sport_info, year)
                            if rows:
                                print(f"  ✓ {sport} {year}: {len(rows)} players (live site)")
                                all_rows.extend(rows)
                                year_found = True
                                break
                        elif r.status_code == 403 or is_cloudflare_block(r.text):
                            print(f"  · {sport} {year}: live site blocked (Cloudflare)")
                    except Exception:
                        pass

            if not year_found:
                print(f"  ✗ {sport} {year}: no data found")

    if not all_rows:
        print("\nNOTE: Athletes Unlimited stats are loaded via JavaScript.")
        print("No accessible snapshots were found in this run.")
        print("Re-run when Wayback Machine has indexed the AU stats pages.")

    cache["au_rows"] = all_rows
    return all_rows


def build_outputs(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    out_players: list[dict] = []
    out_stats:   list[dict] = []
    synthetic_id = SYNTHETIC_ID_START

    # De-duplicate by (name, league)
    key_to_id: dict[tuple, int] = {}

    for row in rows:
        name   = row.get("name", "").strip()
        league = row.get("_league", "AU")
        if not name:
            continue

        key = (name.lower(), league)
        if key not in key_to_id:
            parts = name.split()
            first = parts[0] if parts else ""
            last  = parts[-1] if len(parts) > 1 else ""
            out_players.append({
                "id":         synthetic_id,
                "full_name":  name,
                "short_name": name,
                "first_name": first,
                "last_name":  last,
                "sport_id":   None,
                "league":     league,
                "team":       row.get("team", ""),
                "position":   row.get("position", ""),
                "_au":        True,
                "_norm_name": name.lower(),
                "sportradar_id": None,
                "college":    None,
                "jersey":     None,
                "height":     None,
                "weight":     None,
            })
            key_to_id[key] = synthetic_id
            synthetic_id += 1

        pid     = key_to_id[key]
        year    = row.get("_year", 0)
        game_id = f"AU_{league.upper().replace(' ', '_')}_{year}_SEASON_TOTAL"

        # All numeric fields in the row (excluding meta fields)
        meta_fields = {"name", "team", "position", "_year", "_league"}
        for stat_name, value in row.items():
            if stat_name in meta_fields or stat_name.startswith("_"):
                continue
            v = safe_float(value)
            if v:
                out_stats.append({
                    "player_id": pid,
                    "week":      1,
                    "stat":      stat_name,
                    "value":     v,
                    "game_id":   game_id,
                    "_year":     year,
                })

    return out_players, out_stats


def main():
    ap = argparse.ArgumentParser(description="Scrape Athletes Unlimited player stats")
    ap.add_argument("--reset", action="store_true", help="Ignore cache and re-fetch")
    args = ap.parse_args()

    cache: dict = {}
    if CACHE_FILE.exists() and not args.reset:
        cache = json.loads(CACHE_FILE.read_text())

    rows = fetch_all_data(cache, reset=args.reset)
    CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")

    # Never clobber previously valid AU outputs with empty data from transient blocking.
    if not rows and PLAYERS_FILE.exists() and STATS_FILE.exists():
        try:
            prev_players = json.loads(PLAYERS_FILE.read_text())
            prev_stats = json.loads(STATS_FILE.read_text())
        except json.JSONDecodeError:
            prev_players, prev_stats = [], []
        if prev_players and prev_stats:
            print("\nNo AU rows fetched this run; preserving existing AU output files.")
            print(f"Existing files contain {len(prev_players)} players and {len(prev_stats)} stat rows.")
            return

    players, stats = build_outputs(rows)
    years = sorted({s["_year"] for s in stats})
    leagues = sorted({s.get("_league", "") for r in rows for k, v in [("_league", r.get("_league", ""))]})
    print(f"\nBuilt {len(players)} AU players,  {len(stats)} stat rows  (years: {years})")

    PLAYERS_FILE.write_text(json.dumps(players, indent=2), encoding="utf-8")
    STATS_FILE.write_text(json.dumps(stats,   indent=2), encoding="utf-8")
    print(f"Wrote {PLAYERS_FILE.name} and {STATS_FILE.name}")


if __name__ == "__main__":
    main()

"""
build_data.py
Aggregates player_stats by canonical player, sport/season, and game,
then writes all static JSON, XML, and CSV data files used by the site.

Output structure (under site/data/):
  players/<canonical_id>.json / .xml / .csv
  leagues/<sport_slug>.json / .xml / .csv
  games/<game_slug>.json / .xml / .csv
  games/index.json
  hof/passing.json / .csv  hof/rushing.json / .csv  etc.
  search_index.json   (includes career stat totals for client-side filtering)
  sports.json / .xml
"""

import csv
import io
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

RAW = Path(__file__).parent / "raw"
MERGED = Path(__file__).parent / "merged"
SITE_DATA = Path(__file__).parent.parent / "docs" / "data"

for d in ["players", "leagues", "hof", "games"]:
    (SITE_DATA / d).mkdir(parents=True, exist_ok=True)


# ─── helpers ────────────────────────────────────────────────────────────────

def slugify(s):
    s = re.sub(r"[^\w\s-]", "", str(s)).strip().lower()
    return re.sub(r"[\s_]+", "-", s)


def game_id_slug(game_id):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(game_id)).strip("-").lower()
    return s


def to_xml(tag, data, parent=None):
    if parent is None:
        el = ET.Element(tag)
    else:
        el = ET.SubElement(parent, tag)
    if isinstance(data, dict):
        for k, v in data.items():
            safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", str(k))
            to_xml(safe_key, v, el)
    elif isinstance(data, list):
        for item in data:
            to_xml("item", item, el)
    else:
        el.text = "" if data is None else str(data)
    return el


def write_json_xml(path_no_ext, data, root_tag="data"):
    p = Path(str(path_no_ext))
    p.parent.mkdir(parents=True, exist_ok=True)
    (Path(str(path_no_ext) + ".json")).write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
    root = to_xml(root_tag, data)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(path_no_ext) + ".xml", encoding="unicode", xml_declaration=True)


def write_csv(path, rows, fieldnames):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    Path(str(path) + ".csv").write_text(buf.getvalue(), encoding="utf-8")


def parse_game_meta(game_id):
    """
    Parse various game_id formats into structured metadata.

    Formats:
      FOOTBALL_XFL_2023_3_16_HOU@SEA  -> football league games
      2022-07-23-MIN-IND              -> date-based (AUDL/UFA)
      SWKANSAS-NASHVILLE-0426         -> team-date (50Yard, AF1, etc.)
      2463420                         -> numeric (BIG3)
    """
    gid = str(game_id)
    result = {"game_id": gid, "slug": game_id_slug(gid)}

    m = re.match(r"FOOTBALL_([A-Z0-9]+)_(\d{4})_(\d+)_(\d+)_([A-Z0-9]+)@([A-Z0-9]+)", gid)
    if m:
        result.update({
            "sport_type": "football",
            "league": m.group(1),
            "season": int(m.group(2)),
            "month": int(m.group(3)),
            "day": int(m.group(4)),
            "away_team": m.group(5),
            "home_team": m.group(6),
            "display": f"{m.group(5)} @ {m.group(6)} — {m.group(1)} {m.group(2)}",
            "date_str": f"{m.group(2)}-{int(m.group(3)):02d}-{int(m.group(4)):02d}",
        })
        return result

    m = re.match(r"(\d{4})-(\d{2})-(\d{2})-([A-Z]+)-([A-Z]+)$", gid)
    if m:
        result.update({
            "sport_type": "disc",
            "season": int(m.group(1)),
            "date_str": f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
            "away_team": m.group(4),
            "home_team": m.group(5),
            "display": f"{m.group(4)} @ {m.group(5)} ({m.group(1)}-{m.group(2)}-{m.group(3)})",
        })
        return result

    m = re.match(r"([A-Z][A-Z0-9]+)-([A-Z][A-Z0-9]+)-(\d{4})$", gid, re.I)
    if m:
        result.update({
            "sport_type": "other",
            "away_team": m.group(1).upper(),
            "home_team": m.group(2).upper(),
            "date_str": m.group(3),
            "display": f"{m.group(1).upper()} @ {m.group(2).upper()} ({m.group(3)})",
        })
        return result

    if re.match(r"^\d+$", gid):
        result.update({"sport_type": "basketball", "display": f"Game #{gid}"})
        return result

    # Synthetic key: SYNTHETIC_{LEAGUE}_{SEASON}_W{week}_{TEAM}
    # Generated for rows where the source DB had no game_id — one game per week per team
    m = re.match(r"SYNTHETIC_([A-Z0-9]+)_(\d{4})_W(\d+)_(.+)$", gid)
    if m:
        league_name = m.group(1)
        season = int(m.group(2))
        week = int(m.group(3))
        team = m.group(4)
        result.update({
            "sport_type": "football",
            "league": league_name,
            "season": season,
            "week": week,
            "team": team,
            "synthetic": True,
            "display": f"{league_name} {season} — Wk {week} ({team})",
        })
        return result

    # Synthetic key without season: SYNTHETIC_{LEAGUE}_W{week}_{TEAM}
    m = re.match(r"SYNTHETIC_([A-Z0-9]+)_W(\d+)_(.+)$", gid)
    if m:
        league_name = m.group(1)
        week = int(m.group(2))
        team = m.group(3)
        result.update({
            "sport_type": "other",
            "league": league_name,
            "week": week,
            "team": team,
            "synthetic": True,
            "display": f"{league_name} — Wk {week} ({team})",
        })
        return result

    # Season-total key from backfill scrapers: FOOTBALL_{LEAGUE}_{SEASON}_SEASON_TOTAL
    m = re.match(r"FOOTBALL_([A-Z0-9]+)_(\d{4})_SEASON_TOTAL$", gid)
    if m:
        league_name = m.group(1)
        season = int(m.group(2))
        result.update({
            "sport_type": "football",
            "league": league_name,
            "season": season,
            "synthetic": True,
            "display": f"{league_name} {season} Season",
        })
        return result

    result["display"] = gid
    return result


def enrich_from_db(meta, db_game):
    """Overlay a games-table row onto a parsed game-meta dict."""
    home = (db_game.get("team_home") or "").strip()
    away = (db_game.get("team_away") or "").strip()
    if home:
        meta["home_team"] = home
    if away:
        meta["away_team"] = away
    sh = db_game.get("score_home")
    sa = db_game.get("score_away")
    if sh is not None:
        meta["score_home"] = sh
    if sa is not None:
        meta["score_away"] = sa
    start_time = db_game.get("start_time")
    if start_time and isinstance(start_time, str):
        meta["date_str"] = start_time[:10]
        meta["start_time"] = start_time
    channel = db_game.get("channel")
    if channel and str(channel).strip():
        meta["channel"] = str(channel).strip()
    if db_game.get("record_home"):
        meta["record_home"] = db_game["record_home"]
    if db_game.get("record_away"):
        meta["record_away"] = db_game["record_away"]
    # Rebuild display: "AWAY @ HOME" + score if game is final (both scores > 0 or one > 0)
    if home and away:
        sh_v = meta.get("score_home")
        sa_v = meta.get("score_away")
        if sh_v is not None and sa_v is not None and (sh_v > 0 or sa_v > 0):
            meta["display"] = f"{away} @ {home} ({sa_v}–{sh_v})"
        else:
            meta["display"] = f"{away} @ {home}"
    return meta


# ─── College stats helpers ──────────────────────────────────────────────────

def _norm_cname(name: str) -> str:
    """Normalise a name for college stats lookup: lowercase, strip diacritics."""
    nfkd = unicodedata.normalize("NFD", str(name))
    ascii_n = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", ascii_n).strip().lower()


def _match_college(cp, college_name_index, college_stats_data):
    """
    Find college stats for a canonical player.

    Strategy:
      1. Look up normalised canonical name in the name index.
      2. If exactly one URL found, use it.
      3. If multiple URLs found, try to disambiguate using any college name
         stored in the player's appearances.  Skip if still ambiguous.

    Returns a college data dict or None.
    """
    if not college_name_index or not college_stats_data:
        return None

    norm = _norm_cname(cp["canonical_name"])
    candidates = college_name_index.get(norm, [])
    if not candidates:
        return None

    url = None
    if len(candidates) == 1:
        url = candidates[0]
    else:
        # Disambiguate using college names stored in roster appearances.
        known_colleges = {
            a.get("college", "").lower()
            for a in cp.get("appearances", [])
            if a.get("college")
        }
        if known_colleges:
            for cand_url in candidates:
                entry = college_stats_data.get(cand_url, {})
                school_abbr = entry.get("school", "").lower()
                for college in known_colleges:
                    if school_abbr and (
                        school_abbr in college or college.startswith(school_abbr)
                    ):
                        url = cand_url
                        break
                if url:
                    break
        # If still ambiguous, skip — wrong data is worse than missing data.

    if not url:
        return None
    entry = college_stats_data.get(url, {})
    if not entry:
        return None

    seasons = entry.get("seasons", {})
    career: dict = {}
    for yr_stats in seasons.values():
        for stat, val in yr_stats.items():
            career[stat] = career.get(stat, 0) + val

    return {
        "school": entry.get("school", ""),
        "fdb_url": url,
        "seasons": seasons,
        "career": career,
    }


# ─── main ───────────────────────────────────────────────────────────────────

def main():
    print("Loading data ...")
    players_merged = json.loads((MERGED / "players_merged.json").read_text())
    id_lookup = json.loads((MERGED / "id_to_canonical.json").read_text())
    raw_stats = json.loads((RAW / "player_stats.json").read_text())
    sports = json.loads((RAW / "sports.json").read_text())
    raw_players = json.loads((RAW / "players.json").read_text())

    # ── Inject AAF 2019 players and box-score stats ───────────────────────
    _AAF_MONTH = {
        "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
        "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    }
    _AAF_ABBR = {
        "atlanta":"ATL","birmingham":"BIR","memphis":"MEM","orlando":"ORL",
        "arizona":"ARI","san antonio":"SA","san diego":"SD","salt lake":"SL",
    }
    # Stats to include from box scores (skip derived/rate stats and scraper artefacts)
    _AAF_SKIP = {
        "completion_pct","yards_per_attempt","pass_td_pct","int_pct","passer_rating",
        "rush_avg","rush_avg_game","rush_long","rec_avg","rec_avg_game","rec_long",
        "pass_long","games_played",
        # kick/punt return columns for QBs are a known scraper duplication bug
        "kick_returns","kr_yards","kr_avg","kr_long","kr_tds",
        "punt_returns","pr_yards","pr_avg","pr_long","pr_tds",
    }
    _AAF_RENAME = {"interceptions_thrown": "interceptions_lost"}

    aaf_players_file = RAW / "aaf_players.json"
    aaf_bs_file      = RAW / "aaf_2019_boxscores.json"
    if aaf_players_file.exists() and aaf_bs_file.exists():
        aaf_raw_players = json.loads(aaf_players_file.read_text())
        raw_players.extend(aaf_raw_players)           # extend before pid maps are built

        aaf_url_to_id = {p["_aaf_url"]: p["id"] for p in aaf_raw_players}

        boxscores = json.loads(aaf_bs_file.read_text())
        aaf_stat_rows = []
        for bs in boxscores:
            week = bs.get("week", 1)
            date_str = bs.get("date", "")
            # Parse "Saturday, February 9, 2019" → month, day
            parts = date_str.replace(",", "").split()
            try:
                month = _AAF_MONTH[parts[1].lower()]
                day   = int(parts[2])
            except (IndexError, KeyError, ValueError):
                month, day = 1, 1
            # Build FOOTBALL_AAF_2019_M_D_AWAY@HOME game id
            away_abbr = _AAF_ABBR.get(bs.get("away_team","").lower(), bs.get("away_team","UNK").upper()[:3])
            home_abbr = _AAF_ABBR.get(bs.get("home_team","").lower(), bs.get("home_team","UNK").upper()[:3])
            game_id = f"FOOTBALL_AAF_2019_{month}_{day}_{away_abbr}@{home_abbr}"

            for player in bs.get("players", []):
                url = player.get("url", "")
                pid = aaf_url_to_id.get(url)
                if pid is None:
                    continue
                for stat, val in player.get("stats", {}).items():
                    if stat in _AAF_SKIP:
                        continue
                    stat = _AAF_RENAME.get(stat, stat)
                    aaf_stat_rows.append({
                        "player_id": pid,
                        "week":      week,
                        "stat":      stat,
                        "value":     float(val or 0),
                        "game_id":   game_id,
                    })
        raw_stats.extend(aaf_stat_rows)
        print(f"Injected {len(aaf_raw_players)} AAF players, {len(aaf_stat_rows)} AAF stat rows")

    # ── Inject CFL historical season totals (built by scrape_cfl.py) ─────────
    _cfl_hist_players_file = RAW / "cfl_historical_players.json"
    _cfl_hist_stats_file   = RAW / "cfl_historical_stats.json"
    if _cfl_hist_players_file.exists() and _cfl_hist_stats_file.exists():
        cfl_hist_players = json.loads(_cfl_hist_players_file.read_text())
        cfl_hist_stats   = json.loads(_cfl_hist_stats_file.read_text())
        raw_players.extend(cfl_hist_players)
        raw_stats.extend(cfl_hist_stats)
        years_seen = sorted({r.get("_year") for r in cfl_hist_stats if r.get("_year")})
        print(f"Injected {len(cfl_hist_players)} CFL historical players, "
              f"{len(cfl_hist_stats)} stat rows (years: {years_seen})")

    # ── Inject ELF historical season totals (built by scrape_elf.py) ──────────
    _elf_hist_players_file = RAW / "elf_historical_players.json"
    _elf_hist_stats_file   = RAW / "elf_historical_stats.json"
    if _elf_hist_players_file.exists() and _elf_hist_stats_file.exists():
        elf_hist_players = json.loads(_elf_hist_players_file.read_text())
        elf_hist_stats   = json.loads(_elf_hist_stats_file.read_text())
        raw_players.extend(elf_hist_players)
        raw_stats.extend(elf_hist_stats)
        years_seen = sorted({r.get("_year") for r in elf_hist_stats if r.get("_year")})
        print(f"Injected {len(elf_hist_players)} ELF historical players, "
              f"{len(elf_hist_stats)} stat rows (years: {years_seen})")

    # Load games table if available
    raw_games = json.loads((RAW / "games.json").read_text()) if (RAW / "games.json").exists() else []
    # Build lookups: direct by game_id string, and by (sport_id, week, team_upper) for synthetic matching
    db_game_by_id = {}
    db_game_by_sport_week_team = {}
    for g in raw_games:
        gid = g.get("game_id")
        if gid is not None:
            db_game_by_id[str(gid)] = g
        sid = g.get("sport_id")
        wk = g.get("week")
        if sid and wk:
            for team_field in ("team_home", "team_away"):
                t = (g.get(team_field) or "").upper().replace(" ", "")
                if t:
                    key = (sid, wk, t)
                    if key not in db_game_by_sport_week_team:
                        db_game_by_sport_week_team[key] = g
    print(f"Loaded {len(raw_games)} games, {len(db_game_by_id)} with direct game_id")

    # ── Load college stats index if available (built by scrape_college.py) ──
    _college_stats_file = RAW / "college_stats_raw.json"
    _college_index_file = RAW / "college_name_index.json"
    college_stats_data   = json.loads(_college_stats_file.read_text()) if _college_stats_file.exists() else {}
    college_name_index   = json.loads(_college_index_file.read_text()) if _college_index_file.exists() else {}
    if college_stats_data:
        print(f"Loaded college index: {len(college_stats_data)} players, {len(college_name_index)} name entries")

    # ── Load NFL stats if available (built by scrape_nfl.py) ─────────────────
    _nfl_stats_file = RAW / "nfl_stats_raw.json"
    nfl_stats_data  = json.loads(_nfl_stats_file.read_text()) if _nfl_stats_file.exists() else {}
    if nfl_stats_data:
        print(f"Loaded NFL stats: {len(nfl_stats_data)} players")

    sport_map = {s["id"]: s for s in sports}
    canonical_map = {cp["canonical_id"]: cp for cp in players_merged}
    pid_sport_map = {p["id"]: p.get("sport_id") for p in raw_players}
    pid_team_map = {p["id"]: (p.get("team") or "").upper().replace(" ", "") for p in raw_players}

    # ── Dedup raw_stats: take MAX per (player_id, game_id, stat, week) ────────
    # The source DB stores running cumulative totals — the live tracker inserts a
    # new row every time a stat updates during a game, so the same (player/game/stat)
    # can appear hundreds of times with increasing values. MAX = final game total.
    _orig_count = len(raw_stats)
    _dedup: dict = {}
    for _r in raw_stats:
        _k = (_r.get("player_id"), _r.get("game_id"), _r.get("stat", ""), _r.get("week"))
        _v = float(_r.get("value") or 0)
        if _k not in _dedup or _v > _dedup[_k][0]:
            _dedup[_k] = (_v, _r)
    raw_stats = [{**_row, "value": _val} for _val, _row in _dedup.values()]
    print(f"Deduped stat rows: {_orig_count:,} → {len(raw_stats):,} (removed {_orig_count - len(raw_stats):,})")
    del _dedup, _orig_count

    print(f"Aggregating {len(raw_stats)} stat rows ...")

    player_stat_totals = defaultdict(lambda: defaultdict(float))
    player_game_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    player_game_meta_store = {}

    league_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    game_player_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    game_meta = {}
    game_sport_slug = {}
    game_players_seen = defaultdict(set)

    for row in raw_stats:
        pid = str(row.get("player_id", ""))
        canonical_id = id_lookup.get(pid)
        if not canonical_id:
            continue

        stat = row.get("stat", "")
        if not stat:
            continue
        value = float(row.get("value") or 0)
        week = row.get("week")
        raw_game_id = row.get("game_id")
        gid_str = str(raw_game_id) if raw_game_id is not None else None

        # Synthesize a game key for rows with no game_id using sport+season+week+team.
        # Football leagues play once per week so (league, season, week, team) is unique.
        # Disc leagues (AUDL) can play multiple times per week — synthetic key is a week aggregate.
        if not gid_str or gid_str == "None":
            pid_int = int(pid) if pid.isdigit() else -1
            sport_id = pid_sport_map.get(pid_int)
            team = pid_team_map.get(pid_int, "")
            if sport_id and sport_id in sport_map and week is not None and team:
                s = sport_map[sport_id]
                sname = re.sub(r"[^A-Z0-9]", "", (s.get("name") or "").upper())
                sse = s.get("season") or ""
                if sse:
                    gid_str = f"SYNTHETIC_{sname}_{sse}_W{week}_{team}"
                else:
                    gid_str = f"SYNTHETIC_{sname}_W{week}_{team}"
            else:
                gid_str = None

        if gid_str and gid_str not in player_game_meta_store:
            player_game_meta_store[gid_str] = parse_game_meta(gid_str)
            # Enrich with DB game data: try direct match first, then sport+week+team
            meta = player_game_meta_store[gid_str]
            db_game = db_game_by_id.get(gid_str)
            if db_game is None and meta.get("synthetic"):
                pid_int = int(pid) if pid.isdigit() else -1
                sport_id_val = pid_sport_map.get(pid_int)
                team_val = pid_team_map.get(pid_int, "")
                if sport_id_val and week is not None and team_val:
                    db_game = db_game_by_sport_week_team.get((sport_id_val, week, team_val))
            if db_game:
                enrich_from_db(meta, db_game)
                # Fill in league/season from sports map if missing (e.g. numeric game_ids)
                if not meta.get("league") or not meta.get("season"):
                    sid_val = db_game.get("sport_id")
                    if sid_val and sid_val in sport_map:
                        s = sport_map[sid_val]
                        if not meta.get("league"):
                            meta["league"] = s.get("name", "")
                        if not meta.get("season"):
                            meta["season"] = s.get("season") or ""
                        if not meta.get("week") and db_game.get("week") is not None:
                            meta["week"] = db_game["week"]
                        # Rebuild display if we now have better info
                        away = meta.get("away_team", "")
                        home = meta.get("home_team", "")
                        if away and home:
                            sh_v = meta.get("score_home")
                            sa_v = meta.get("score_away")
                            if sh_v is not None and sa_v is not None and (sh_v > 0 or sa_v > 0):
                                meta["display"] = f"{away} @ {home} ({sa_v}–{sh_v})"
                            else:
                                meta["display"] = f"{away} @ {home}"

        meta = player_game_meta_store.get(gid_str, {}) if gid_str else {}

        league_name = meta.get("league", "")
        season = meta.get("season", "")
        if league_name and season:
            sport_slug = slugify(f"{league_name}-{season}")
        elif league_name:
            sport_slug = slugify(league_name)
        else:
            pid_int = int(pid) if pid.isdigit() else -1
            sport_id = pid_sport_map.get(pid_int)
            if sport_id and sport_id in sport_map:
                s = sport_map[sport_id]
                sname = s.get("name", "unknown")
                sse = s.get("season", "")
                sport_slug = slugify(f"{sname}-{sse}" if sse else sname)
            else:
                sport_slug = "unknown"

        player_stat_totals[canonical_id][stat] += value
        league_stats[sport_slug][canonical_id][stat] += value

        if gid_str:
            player_game_stats[canonical_id][gid_str][stat] += value
            # Store sport_slug and week on the meta
            if "sport_slug" not in player_game_meta_store[gid_str]:
                player_game_meta_store[gid_str]["sport_slug"] = sport_slug
            if "week" not in player_game_meta_store[gid_str] and week is not None:
                player_game_meta_store[gid_str]["week"] = week

            gslug = game_id_slug(gid_str)
            game_player_stats[gslug][canonical_id][stat] += value
            game_players_seen[gslug].add(canonical_id)
            if gslug not in game_meta:
                parsed = dict(player_game_meta_store[gid_str])
                parsed["sport_slug"] = sport_slug
                parsed["week"] = week
                game_meta[gslug] = parsed
                game_sport_slug[gslug] = sport_slug

    # ─── Player files ─────────────────────────────────────────────────────
    print("Writing player files ...")
    search_index = []

    for cp in players_merged:
        cid = cp["canonical_id"]
        totals = dict(player_stat_totals.get(cid, {}))

        game_log_by_game = []
        for gid_str, stats_dict in sorted(
            player_game_stats.get(cid, {}).items(),
            key=lambda kv: (
                player_game_meta_store.get(kv[0], {}).get("season") or 0,
                player_game_meta_store.get(kv[0], {}).get("week") or 0,
                kv[0],
            ),
        ):
            meta = player_game_meta_store.get(gid_str, {})
            game_log_by_game.append({
                "game_id": gid_str,
                "game_slug": game_id_slug(gid_str),
                "display": meta.get("display", gid_str),
                "season": meta.get("season", ""),
                "week": meta.get("week", ""),
                "league": meta.get("league", ""),
                "sport_slug": meta.get("sport_slug", ""),
                "away_team": meta.get("away_team", ""),
                "home_team": meta.get("home_team", ""),
                "date_str": meta.get("date_str", ""),
                "score_home": meta.get("score_home", ""),
                "score_away": meta.get("score_away", ""),
                "stats": dict(stats_dict),
            })

        by_season = defaultdict(lambda: defaultdict(float))
        for entry in game_log_by_game:
            skey = f"{entry['league']}-{entry['season']}" if entry.get("league") else "unknown"
            for stat, val in entry["stats"].items():
                by_season[skey][stat] += val
        season_totals = {k: dict(v) for k, v in by_season.items()}

        player_data = {
            "canonical_id": cid,
            "canonical_name": cp["canonical_name"],
            "positions": cp["positions"],
            "leagues": cp["leagues"],
            "sport_names": cp["sport_names"],
            "ambiguous": cp["ambiguous"],
            "appearances": cp["appearances"],
            "career_totals": totals,
            "season_totals": season_totals,
            "game_log": game_log_by_game,
            "college": _match_college(cp, college_name_index, college_stats_data),
            "nfl":     nfl_stats_data.get(cid),
        }

        write_json_xml(SITE_DATA / "players" / cid, player_data, root_tag="player")

        if game_log_by_game:
            all_stat_keys = sorted({sk for g in game_log_by_game for sk in g["stats"]})
            csv_rows = []
            for g in game_log_by_game:
                row_d = {
                    "game_id": g["game_id"],
                    "date": g["date_str"],
                    "season": g["season"],
                    "week": g["week"],
                    "league": g["league"],
                    "away": g["away_team"],
                    "home": g["home_team"],
                }
                for sk in all_stat_keys:
                    row_d[sk] = g["stats"].get(sk, "")
                csv_rows.append(row_d)
            write_csv(
                SITE_DATA / "players" / cid,
                csv_rows,
                ["game_id", "date", "season", "week", "league", "away", "home"] + all_stat_keys,
            )

        if totals:
            search_index.append({
                "id": cid,
                "name": cp["canonical_name"],
                "positions": cp["positions"],
                "leagues": sorted({g["league"] for g in game_log_by_game if g.get("league")}),
                "sport_names": cp["sport_names"],
                "ambiguous": cp["ambiguous"],
                "totals": {k: round(v, 1) for k, v in totals.items()},
            })

    print(f"Written {len(players_merged)} player files")

    # ─── League files ─────────────────────────────────────────────────────
    print("Writing league files ...")
    league_index = []

    for sport_slug, player_totals in league_stats.items():
        display = sport_slug.replace("-", " ").upper()
        all_stat_keys = sorted({sk for pt in player_totals.values() for sk in pt})
        league_players = []

        for cid, stats in player_totals.items():
            cp = canonical_map.get(cid, {})
            league_players.append({
                "canonical_id": cid,
                "canonical_name": cp.get("canonical_name", cid),
                "positions": cp.get("positions", []),
                "ambiguous": cp.get("ambiguous", False),
                "stats": stats,
            })
        league_players.sort(key=lambda p: p["canonical_name"])

        # Build game list for this league with full metadata for the template.
        # Deduplicate synthetic games: two per-team slugs often resolve to the same real
        # matchup after DB enrichment — keep one entry, combining player counts.
        league_games_raw = []
        for gs, ss in game_sport_slug.items():
            if ss == sport_slug:
                gm = game_meta.get(gs, {})
                league_games_raw.append({
                    "slug": gs,
                    "display": gm.get("display", gs),
                    "week": gm.get("week", ""),
                    "season": gm.get("season", ""),
                    "date_str": gm.get("date_str", ""),
                    "away_team": gm.get("away_team", ""),
                    "home_team": gm.get("home_team", ""),
                    "team": gm.get("team", ""),
                    "score_home": gm.get("score_home", ""),
                    "score_away": gm.get("score_away", ""),
                    "channel": gm.get("channel", ""),
                    "synthetic": gm.get("synthetic", False),
                    "player_count": len(game_players_seen.get(gs, set())),
                })
        # Deduplicate: if two entries share (away_team, home_team, week, season), merge them
        seen_matchups = {}
        league_games = []
        for g in sorted(league_games_raw, key=lambda x: (x.get("season") or 0, x.get("week") or 0, x.get("slug", ""))):
            away = (g.get("away_team") or "").upper()
            home = (g.get("home_team") or "").upper()
            wk = g.get("week")
            ssn = g.get("season")
            if away and home:
                dedup_key = (away, home, wk, ssn)
                if dedup_key in seen_matchups:
                    # Merge player_count into existing entry
                    seen_matchups[dedup_key]["player_count"] += g["player_count"]
                    continue
                seen_matchups[dedup_key] = g
            league_games.append(g)

        league_data = {
            "slug": sport_slug,
            "display_name": display,
            "player_count": len(league_players),
            "game_count": len(league_games),
            "games": league_games,
            "players": league_players,
        }
        write_json_xml(SITE_DATA / "leagues" / sport_slug, league_data, root_tag="league")

        if league_players:
            csv_rows = []
            for lp in league_players:
                row_d = {
                    "player_id": lp["canonical_id"],
                    "player_name": lp["canonical_name"],
                    "positions": "/".join(lp["positions"]),
                }
                for sk in all_stat_keys:
                    row_d[sk] = lp["stats"].get(sk, "")
                csv_rows.append(row_d)
            write_csv(
                SITE_DATA / "leagues" / sport_slug,
                csv_rows,
                ["player_id", "player_name", "positions"] + all_stat_keys,
            )

        league_index.append({
            "slug": sport_slug,
            "display_name": display,
            "player_count": len(league_players),
            "game_count": len(league_games),
        })

    league_index.sort(key=lambda x: x["display_name"])
    write_json_xml(SITE_DATA / "leagues" / "index", {"leagues": league_index}, root_tag="leagues")
    print(f"Written {len(league_index)} league files")

    # ─── Game files ───────────────────────────────────────────────────────
    print("Writing game files ...")
    game_index = []

    for gslug, meta in game_meta.items():
        all_stat_keys = sorted(
            {sk for cid in game_players_seen[gslug] for sk in game_player_stats[gslug][cid]}
        )
        player_entries = []
        for cid in game_players_seen[gslug]:
            cp = canonical_map.get(cid, {})
            player_entries.append({
                "canonical_id": cid,
                "canonical_name": cp.get("canonical_name", cid),
                "positions": cp.get("positions", []),
                "ambiguous": cp.get("ambiguous", False),
                "stats": dict(game_player_stats[gslug][cid]),
            })
        player_entries.sort(key=lambda p: p["canonical_name"])

        game_data = {
            "slug": gslug,
            "game_id": meta.get("game_id", gslug),
            "display": meta.get("display", gslug),
            "league": meta.get("league", ""),
            "season": meta.get("season", ""),
            "week": meta.get("week", ""),
            "away_team": meta.get("away_team", ""),
            "home_team": meta.get("home_team", ""),
            "score_away": meta.get("score_away", ""),
            "score_home": meta.get("score_home", ""),
            "date_str": meta.get("date_str", ""),
            "channel": meta.get("channel", ""),
            "record_home": meta.get("record_home", ""),
            "record_away": meta.get("record_away", ""),
            "sport_slug": meta.get("sport_slug", ""),
            "synthetic": meta.get("synthetic", False),
            "player_count": len(player_entries),
            "stat_keys": all_stat_keys,
            "players": player_entries,
        }
        write_json_xml(SITE_DATA / "games" / gslug, game_data, root_tag="game")

        if player_entries:
            csv_rows = []
            for pe in player_entries:
                row_d = {
                    "player_id": pe["canonical_id"],
                    "player_name": pe["canonical_name"],
                    "positions": "/".join(pe["positions"]),
                }
                for sk in all_stat_keys:
                    row_d[sk] = pe["stats"].get(sk, "")
                csv_rows.append(row_d)
            write_csv(
                SITE_DATA / "games" / gslug,
                csv_rows,
                ["player_id", "player_name", "positions"] + all_stat_keys,
            )

        game_index.append({
            "slug": gslug,
            "game_id": meta.get("game_id", gslug),
            "display": meta.get("display", gslug),
            "league": meta.get("league", ""),
            "season": meta.get("season", ""),
            "week": meta.get("week", ""),
            "away_team": meta.get("away_team", ""),
            "home_team": meta.get("home_team", ""),
            "date_str": meta.get("date_str", ""),
            "sport_slug": meta.get("sport_slug", ""),
            "player_count": len(player_entries),
        })

    game_index.sort(key=lambda g: (str(g.get("season") or ""), str(g.get("date_str") or ""), g["slug"]))
    (SITE_DATA / "games" / "index.json").write_text(
        json.dumps(game_index, indent=2), encoding="utf-8"
    )
    print(f"Written {len(game_index)} game files")

    # ─── Hall of Fame ─────────────────────────────────────────────────────
    print("Building Hall of Fame ...")

    # Leagues excluded from all HoF calculations (main rankings + extras).
    HOF_EXCLUDED_BASES = {"50 Yard"}

    # Sport IDs whose stats should not count toward HoF totals.
    _hof_excl_sport_ids = {
        sid for sid, s in sport_map.items()
        if re.sub(r"\s*\d{4}$", "", s.get("name", "")).strip() in HOF_EXCLUDED_BASES
    }
    # Slugified versions for the league_stats / player_league_seasons keys.
    _hof_excl_slugs = {
        slugify(f"{s.get('name','')}-{s.get('season','')}" if s.get("season") else s.get("name",""))
        for sid, s in sport_map.items()
        if re.sub(r"\s*\d{4}$", "", s.get("name", "")).strip() in HOF_EXCLUDED_BASES
    }

    # Recompute stat totals with excluded leagues removed.
    # (player_stat_totals still includes them for player pages; hof_stat_totals does not.)
    hof_stat_totals: dict = defaultdict(lambda: defaultdict(float))
    for _r in raw_stats:
        _pid_int = _r.get("player_id")
        if _hof_excl_sport_ids and pid_sport_map.get(_pid_int) in _hof_excl_sport_ids:
            continue
        _pid = str(_pid_int) if _pid_int is not None else ""
        _cid = id_lookup.get(_pid)
        if not _cid:
            continue
        _st = _r.get("stat", "")
        if not _st:
            continue
        hof_stat_totals[_cid][_st] += float(_r.get("value") or 0)

    def _has_real_name(cp: dict) -> bool:
        """Return True if the canonical player has a real name (not a team-code placeholder like 'TOR QB')."""
        return any(c.islower() for c in cp.get("canonical_name", ""))

    hof_stats = {
        "passing":   ["passing_yards", "passing_tds", "completions", "interceptions_lost"],
        "rushing":   ["rushing_yards", "rushing_tds"],
        "receiving": ["receiving_yards", "receiving_tds", "receptions"],
        "defense":   ["def_tackles", "def_sacks", "def_int"],
        "kicking":   ["made_49", "made_50", "extra_points", "missed"],
    }

    hof_all = {}
    for category, stat_keys in hof_stats.items():
        primary_stat = stat_keys[0]
        ranked = []
        for cp in players_merged:
            cid = cp["canonical_id"]
            if not _has_real_name(cp):
                continue
            t = hof_stat_totals.get(cid, {})
            primary_val = t.get(primary_stat, 0)
            if primary_val == 0:
                continue
            entry = {
                "rank": 0,
                "canonical_id": cid,
                "canonical_name": cp["canonical_name"],
                "positions": cp["positions"],
                "ambiguous": cp["ambiguous"],
            }
            for sk in stat_keys:
                entry[sk] = t.get(sk, 0)
            ranked.append(entry)

        ranked.sort(key=lambda x: x[primary_stat], reverse=True)
        for i, r in enumerate(ranked):
            r["rank"] = i + 1

        top100 = ranked[:100]
        hof_data = {"category": category, "primary_stat": primary_stat, "leaders": top100}
        write_json_xml(SITE_DATA / "hof" / category, hof_data, root_tag="hof")

        if top100:
            csv_fields = ["rank", "canonical_id", "canonical_name", "positions"] + stat_keys
            write_csv(
                SITE_DATA / "hof" / category,
                [
                    {
                        "rank": r["rank"],
                        "canonical_id": r["canonical_id"],
                        "canonical_name": r["canonical_name"],
                        "positions": "/".join(r.get("positions", [])),
                        **{sk: r.get(sk, "") for sk in stat_keys},
                    }
                    for r in top100
                ],
                csv_fields,
            )

        hof_all[category] = top100[:10]

    write_json_xml(SITE_DATA / "hof" / "all", {"top10s": hof_all}, root_tag="hof")
    print("Written Hall of Fame files")

    # ─── HoF Extras (records & curiosities) ───────────────────────────────────
    print("Building HoF extras ...")

    # 1. Most distinct league-seasons (e.g. xfl-2020, usfl-2022, cfl-2023 = 3)
    player_league_seasons: dict = defaultdict(set)
    for _sl, _pd in league_stats.items():
        if _sl in _hof_excl_slugs:
            continue
        for _cid, _sd in _pd.items():
            if any(v > 0 for v in _sd.values()):
                player_league_seasons[_cid].add(_sl)

    most_leagues = []
    for _cid, _sl_set in player_league_seasons.items():
        _cp = canonical_map.get(_cid)
        if not _cp or not _has_real_name(_cp) or len(_sl_set) < 2:
            continue
        _fmt = sorted(s.upper().replace("-", " ") for s in _sl_set)
        most_leagues.append({
            "canonical_id": _cid,
            "canonical_name": _cp["canonical_name"],
            "value": len(_sl_set),
            "display": f"{len(_sl_set)} seasons",
            "detail": " · ".join(_fmt),
        })
    most_leagues.sort(key=lambda x: x["value"], reverse=True)
    most_leagues = most_leagues[:10]

    # 2. Most distinct leagues by base name (XFL, USFL, CFL counted once each)
    player_distinct_leagues: dict = defaultdict(set)
    for _cp2 in players_merged:
        _cid2 = _cp2["canonical_id"]
        for _app in _cp2.get("appearances", []):
            _sid = _app.get("sport_id")
            if _sid and _sid in sport_map:
                _sname = sport_map[_sid].get("name", "")
                _base = re.sub(r"\s*\d{4}$", "", _sname).strip()
                if _base and _base not in HOF_EXCLUDED_BASES:
                    player_distinct_leagues[_cid2].add(_base)

    most_distinct_leagues = []
    for _cid2, _ls in player_distinct_leagues.items():
        _cp2 = canonical_map.get(_cid2)
        if not _cp2 or not _has_real_name(_cp2) or len(_ls) < 2:
            continue
        most_distinct_leagues.append({
            "canonical_id": _cid2,
            "canonical_name": _cp2["canonical_name"],
            "value": len(_ls),
            "display": f"{len(_ls)} leagues",
            "detail": ", ".join(sorted(_ls)),
        })
    most_distinct_leagues.sort(key=lambda x: x["value"], reverse=True)
    most_distinct_leagues = most_distinct_leagues[:10]

    # 3. Most career games played (distinct game keys with any stats)
    most_games = []
    for _cid3, _gd in player_game_stats.items():
        _cp3 = canonical_map.get(_cid3)
        if not _cp3 or not _has_real_name(_cp3) or not _gd:
            continue
        most_games.append({
            "canonical_id": _cid3,
            "canonical_name": _cp3["canonical_name"],
            "value": len(_gd),
            "display": f"{len(_gd)} games",
            "detail": "",
        })
    most_games.sort(key=lambda x: x["value"], reverse=True)
    most_games = most_games[:10]

    # 4. Most career combined TDs (passing + rushing + receiving)
    most_tds = []
    for _cid4, _tot in hof_stat_totals.items():
        _cp4 = canonical_map.get(_cid4)
        if not _cp4 or not _has_real_name(_cp4):
            continue
        _tds = int(_tot.get("passing_tds", 0) + _tot.get("rushing_tds", 0) +
                   _tot.get("receiving_tds", 0))
        if _tds <= 0:
            continue
        most_tds.append({
            "canonical_id": _cid4,
            "canonical_name": _cp4["canonical_name"],
            "value": _tds,
            "display": f"{_tds} TDs",
            "detail": (
                f"Pass {int(_tot.get('passing_tds',0))} · "
                f"Rush {int(_tot.get('rushing_tds',0))} · "
                f"Rec {int(_tot.get('receiving_tds',0))}"
            ),
        })
    most_tds.sort(key=lambda x: x["value"], reverse=True)
    most_tds = most_tds[:10]

    # 5. Best single-game passing / rushing / receiving yards
    best_game_pass, best_game_rush, best_game_recv = [], [], []
    for _cid5, _gd5 in player_game_stats.items():
        _cp5 = canonical_map.get(_cid5)
        if not _cp5 or not _has_real_name(_cp5):
            continue
        for _gid, _sd in _gd5.items():
            _meta = player_game_meta_store.get(_gid, {})
            _disp = _meta.get("display") or _gid or "Unknown game"
            for _lst, _key in (
                (best_game_pass, "passing_yards"),
                (best_game_rush, "rushing_yards"),
                (best_game_recv, "receiving_yards"),
            ):
                _val5 = int(_sd.get(_key, 0))
                if _val5 > 0:
                    _lst.append({
                        "canonical_id": _cid5,
                        "canonical_name": _cp5["canonical_name"],
                        "value": _val5,
                        "display": f"{_val5:,} yds",
                        "detail": _disp,
                    })
    for _lst in (best_game_pass, best_game_rush, best_game_recv):
        _lst.sort(key=lambda x: x["value"], reverse=True)
        del _lst[10:]

    hof_extras = {
        "most_league_seasons": most_leagues,
        "most_distinct_leagues": most_distinct_leagues,
        "most_games": most_games,
        "most_tds": most_tds,
        "best_single_game_passing": best_game_pass,
        "best_single_game_rushing": best_game_rush,
        "best_single_game_receiving": best_game_recv,
    }
    (SITE_DATA / "hof" / "extras.json").write_text(
        json.dumps(hof_extras, indent=2), encoding="utf-8"
    )
    print("Written HoF extras")

    # ─── Fun Stats ────────────────────────────────────────────────────────────
    print("Building fun stats ...")
    from datetime import datetime as _datetime

    # Collect game keys that fell on a Thursday
    _thu_game_keys: set = set()
    for _gk, _gm in player_game_meta_store.items():
        _ds = _gm.get("date_str", "")
        if len(_ds) == 10:
            try:
                if _datetime.strptime(_ds, "%Y-%m-%d").weekday() == 3:
                    _thu_game_keys.add(_gk)
            except ValueError:
                pass

    # Aggregate per-player stats across all Thursday games
    thursday_totals: dict = defaultdict(lambda: defaultdict(float))
    thursday_game_counts: dict = defaultdict(int)
    for _cid, _gd in player_game_stats.items():
        for _gk, _sd in _gd.items():
            if _gk in _thu_game_keys:
                for _sk, _sv in _sd.items():
                    thursday_totals[_cid][_sk] += _sv
                thursday_game_counts[_cid] += 1

    # Thursday Night Dream Lineup — best at each slot across all Thursday games
    # Note: CFL uses "extra_point" (not "extra_points") and "tackles" (not "def_tackles")
    LINEUP_SLOTS = [
        ("QB",   ["QB"],                         "passing_yards",  "Pass Yds"),
        ("RB",   ["RB", "FB"],                   "rushing_yards",  "Rush Yds"),
        ("WR",   ["WR"],                         "receiving_yards","Rec Yds"),
        ("TE",   ["TE"],                         "receiving_yards","Rec Yds"),
        ("K",    ["K", "PK"],                    "extra_point",    "XP Made"),
        ("LB",   ["LB", "ILB", "OLB", "MLB"],   "tackles",        "Tackles"),
        ("DB",   ["DB", "CB", "S", "FS", "SS"],  "tackles",        "Tackles"),
    ]
    thursday_lineup: dict = {}
    for _slot, _positions, _primary, _plabel in LINEUP_SLOTS:
        _pos_set = set(_positions)
        _candidates = []
        for _cid, _tot in thursday_totals.items():
            _cp = canonical_map.get(_cid)
            if not _cp or not _has_real_name(_cp):
                continue
            if not set(_cp.get("positions", [])).intersection(_pos_set):
                continue
            _val = _tot.get(_primary, 0)
            if _val <= 0:
                continue
            _candidates.append({
                "canonical_id": _cid,
                "canonical_name": _cp["canonical_name"],
                "positions": list(_cp.get("positions", [])),
                "value": int(_val),
                "stat_key": _primary,
                "stat_label": _plabel,
                "games": thursday_game_counts.get(_cid, 0),
            })
        _candidates.sort(key=lambda x: x["value"], reverse=True)
        thursday_lineup[_slot] = _candidates[:5]

    # Two-Way TD Machine — players who scored BOTH a rushing TD and receiving TD
    # in the same game (aggregated across all such games)
    _two_way_map: dict = defaultdict(lambda: {"rush_td": 0, "recv_td": 0, "games": 0, "game_slugs": []})
    for _cid, _gd in player_game_stats.items():
        _cp = canonical_map.get(_cid)
        if not _cp or not _has_real_name(_cp):
            continue
        for _gk, _sd in _gd.items():
            _rtd = int(_sd.get("rushing_tds", 0))
            _cvtd = int(_sd.get("receiving_tds", 0))
            if _rtd > 0 and _cvtd > 0:
                _entry = _two_way_map[_cid]
                _entry["rush_td"]  += _rtd
                _entry["recv_td"]  += _cvtd
                _entry["games"]    += 1
                if len(_entry["game_slugs"]) < 3:
                    _gmeta = player_game_meta_store.get(_gk, {})
                    _entry["game_slugs"].append({
                        "slug":    game_id_slug(_gk),
                        "display": _gmeta.get("display", _gk),
                        "rush_td": _rtd,
                        "recv_td": _cvtd,
                    })
    two_way_td = []
    for _cid, _s in _two_way_map.items():
        _cp = canonical_map.get(_cid)
        if not _cp:
            continue
        two_way_td.append({
            "canonical_id":   _cid,
            "canonical_name": _cp["canonical_name"],
            "games":          _s["games"],
            "rush_td":        _s["rush_td"],
            "recv_td":        _s["recv_td"],
            "game_slugs":     _s["game_slugs"],
        })
    two_way_td.sort(key=lambda x: x["games"], reverse=True)
    two_way_td = two_way_td[:20]

    funstats = {
        "thursday_game_count": len(_thu_game_keys),
        "thursday_lineup":     thursday_lineup,
        "two_way_td":          two_way_td,
    }
    (SITE_DATA / "hof" / "funstats.json").write_text(
        json.dumps(funstats, indent=2), encoding="utf-8"
    )
    print(f"Written fun stats ({len(_thu_game_keys)} Thursday games, "
          f"{sum(bool(thursday_lineup.get(s)) for s in [x[0] for x in LINEUP_SLOTS])} lineup slots filled, "
          f"{len(two_way_td)} two-way TD players)")

    # ─── Sports index ─────────────────────────────────────────────────────
    write_json_xml(SITE_DATA / "sports", {"sports": sports}, root_tag="sports")

    # ─── Search index ─────────────────────────────────────────────────────
    (SITE_DATA / "search_index.json").write_text(
        json.dumps(search_index), encoding="utf-8"
    )
    print(f"Written search index with {len(search_index)} players")
    print("Done.")


if __name__ == "__main__":
    main()

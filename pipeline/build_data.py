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


# ─── main ───────────────────────────────────────────────────────────────────

def main():
    print("Loading data ...")
    players_merged = json.loads((MERGED / "players_merged.json").read_text())
    id_lookup = json.loads((MERGED / "id_to_canonical.json").read_text())
    raw_stats = json.loads((RAW / "player_stats.json").read_text())
    sports = json.loads((RAW / "sports.json").read_text())
    raw_players = json.loads((RAW / "players.json").read_text())

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

    sport_map = {s["id"]: s for s in sports}
    canonical_map = {cp["canonical_id"]: cp for cp in players_merged}
    pid_sport_map = {p["id"]: p.get("sport_id") for p in raw_players}
    pid_team_map = {p["id"]: (p.get("team") or "").upper().replace(" ", "") for p in raw_players}

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
    hof_stats = {
        "passing": ["passing_yards", "passing_tds", "completions", "interceptions_lost"],
        "rushing": ["rushing_yards", "rushing_tds"],
        "receiving": ["receiving_yards", "receiving_tds", "receptions"],
        "kicking": ["made_49", "made_50", "extra_points", "missed"],
    }

    hof_all = {}
    for category, stat_keys in hof_stats.items():
        primary_stat = stat_keys[0]
        ranked = []
        for cp in players_merged:
            cid = cp["canonical_id"]
            t = player_stat_totals.get(cid, {})
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

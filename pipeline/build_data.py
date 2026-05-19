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


# ─── Sport classification ───────────────────────────────────────────────────

FOOTBALL_LEAGUES = {
    "UFL", "USFL", "XFL", "CFL", "AF1", "AAF", "ELF", "AFL", 
    "IFL", "NAL", "LFA", "X-League", "MLFB", "FCF"
}
DISC_GOLF_LEAGUES = {"DGPT"}
LACROSSE_LEAGUES = {"NLL", "PLL"}
ULTIMATE_LEAGUES = {"AUDL", "UFA", "PUL"}
BASKETBALL_LEAGUES = {"BIG3", "SLAMBALL", "UNRIVALED", "WNBA"}
SOCCER_LEAGUES  = {"MLS", "NWSL", "USLC", "USL1", "USLS", "MLSNP", "NASL"}
CRICKET_LEAGUES = {"T20I", "ODI", "Tests", "IPL", "BBL", "WBBL", "PSL", "MLC", "CPL", "WPL", "BPL", "LPL", "HND", "ILT20", "SA20", "NPL"}
CURLING_LEAGUES = {"WCF-WORLD", "WCF-EUROPE", "WCF-PANCONT", "WCF-OLYMPIC", "WCF-QUAL", "WCF-OTHER", "CURLING-EVENTS"}


def classify_sport(league_name: str) -> str:
    """Classify league name to sport type."""
    league_upper = (league_name or "").upper()
    
    if league_upper in LACROSSE_LEAGUES:
        return "lacrosse"
    if league_upper in DISC_GOLF_LEAGUES:
        return "disc"
    if league_upper in FOOTBALL_LEAGUES:
        return "football"
    if league_upper in SOCCER_LEAGUES:
        return "soccer"
    if league_upper in {l.upper() for l in CRICKET_LEAGUES}:
        return "cricket"
    if league_upper in {l.upper() for l in CURLING_LEAGUES}:
        return "curling"
    if league_upper in ULTIMATE_LEAGUES:
        return "ultimate"
    if league_upper in BASKETBALL_LEAGUES:
        return "basketball"
    return "other"


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


# ---------------------------------------------------------------------------
# IFL play-by-play -> UFL-compatible viz schema
# ---------------------------------------------------------------------------

_IFL_YL_RE = re.compile(r"^([A-Z][A-Z_]*[A-Z])(\d{1,2})$")
_IFL_RESULT_BY_DIFF = {1: "PAT", 2: "Safety", 3: "FG", 4: "TD", 6: "TD",
                       7: "TD", 8: "TD + 2PT"}


def _ifl_team_match(full: str, candidate: str) -> bool:
    if not full or not candidate:
        return False
    a = full.lower(); b = candidate.lower()
    return a in b or b in a


def _convert_ifl_pbp_to_viz(pbp: dict, game_meta: dict) -> dict | None:
    """Convert IFL PBP (drive/play list) into the UFL-shaped viz schema.

    Returns a dict with scoring_drives, viz aliases, and the IFL field
    dimensions (50 between goal lines + 8-yard endzones) so the renderer
    can draw the correct field size.
    """
    plays = pbp.get("plays") or []
    drives = pbp.get("drives") or []
    if not plays or not drives:
        return None

    # 1. Discover the two yard-line prefix tokens used in this game.
    prefix_counts: dict[str, int] = {}
    for p in plays:
        m = _IFL_YL_RE.match(p.get("yardline") or "")
        if m:
            prefix_counts[m.group(1)] = prefix_counts.get(m.group(1), 0) + 1
    if len(prefix_counts) < 2:
        return None
    prefixes = sorted(prefix_counts.keys(), key=lambda k: -prefix_counts[k])[:2]

    # 2. Map each team name to its "own" prefix (where it most often
    #    starts a drive).
    team_prefix_score: dict[tuple, int] = {}
    plays_by_drive: dict[int, list] = {}
    for p in plays:
        di = p.get("drive_index")
        if di is None:
            continue
        plays_by_drive.setdefault(di, []).append(p)
    for d in drives:
        team = d.get("team") or ""
        dp = plays_by_drive.get(d.get("index"))
        if not dp:
            continue
        m = _IFL_YL_RE.match(dp[0].get("yardline") or "")
        if not m:
            continue
        team_prefix_score[(team, m.group(1))] = (
            team_prefix_score.get((team, m.group(1)), 0) + 1
        )
    teams = {t for (t, _) in team_prefix_score}
    team_own_prefix: dict[str, str] = {}
    for t in teams:
        scored = sorted(
            ((pfx, team_prefix_score.get((t, pfx), 0)) for pfx in prefixes),
            key=lambda x: -x[1],
        )
        if scored:
            team_own_prefix[t] = scored[0][0]

    # 3. Resolve which discovered team is home / away.
    home_full = game_meta.get("team_home") or game_meta.get("home_team") or ""
    away_full = game_meta.get("team_away") or game_meta.get("away_team") or ""
    home_team_name = next((t for t in teams if _ifl_team_match(home_full, t)), None)
    away_team_name = next((t for t in teams if _ifl_team_match(away_full, t)), None)
    if not home_team_name or not away_team_name:
        ordered = sorted(teams)
        home_team_name = home_team_name or (ordered[0] if ordered else "")
        away_team_name = away_team_name or (ordered[-1] if ordered else "")
    home_alias = team_own_prefix.get(home_team_name) or prefixes[0]
    away_alias = team_own_prefix.get(away_team_name) or (
        prefixes[1] if prefixes[1] != home_alias else prefixes[0]
    )

    # 4. Build scoring drives in UFL viz shape.
    scoring_drives: list[dict] = []
    prev_away = 0
    prev_home = 0
    for d in drives:
        sa = d.get("score_after")
        if not sa:
            continue
        new_away, new_home = prev_away, prev_home
        for k in ("a", "b"):
            tn = sa.get(f"team_{k}") or ""
            sc = sa.get(f"score_{k}")
            if sc is None:
                continue
            if _ifl_team_match(home_full, tn):
                new_home = sc
            elif _ifl_team_match(away_full, tn):
                new_away = sc
        diff_away = new_away - prev_away
        diff_home = new_home - prev_home
        prev_away, prev_home = new_away, new_home

        team_name = d.get("team") or ""
        if _ifl_team_match(home_full, team_name):
            diff = diff_home
            team_alias = home_alias
        elif _ifl_team_match(away_full, team_name):
            diff = diff_away
            team_alias = away_alias
        else:
            diff = diff_home + diff_away
            team_alias = home_alias
        if diff <= 0:
            # Defensive/return TD or safety scored by the other team -- skip.
            continue
        result = _IFL_RESULT_BY_DIFF.get(diff, f"{diff} pts")

        dp = plays_by_drive.get(d.get("index")) or []
        viz_plays: list[dict] = []
        for idx, p in enumerate(dp):
            m = _IFL_YL_RE.match(p.get("yardline") or "")
            if not m:
                continue
            ss = m.group(1)
            sy = int(m.group(2))
            # End position = next play's start yardline (clean).
            if idx + 1 < len(dp):
                m2 = _IFL_YL_RE.match(dp[idx + 1].get("yardline") or "")
                if m2:
                    es = m2.group(1)
                    ey = int(m2.group(2))
                else:
                    es, ey = ss, sy
            else:
                # Last play -- if a TD, end at opponent's goal line (0).
                txt = (p.get("text") or "").upper()
                if "TOUCHDOWN" in txt:
                    es = away_alias if ss == home_alias else home_alias
                    ey = 0
                else:
                    es, ey = ss, sy
            viz_plays.append({
                "startSide": ss,
                "startYard": sy,
                "endSide": es,
                "endYard": ey,
                "description": p.get("text") or "",
                "type": "scoring" if p.get("scoring") else "play",
            })

        scoring_drives.append({
            "team": {"alias": team_alias, "name": team_name},
            "result": result,
            "quarter": (dp[0].get("quarter") if dp else None),
            "plays": viz_plays,
        })

    return {
        "game_id": pbp.get("game_id"),
        "scoring_drives": scoring_drives,
        "scoring_plays": [],
        "field_length": 50,
        "field_endzone": 8,
        "viz_away_alias": away_alias,
        "viz_home_alias": home_alias,
    }


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

    # Basketball-reference WNBA box score id: WNBA_YYYYMMDD0HOM
    m = re.match(r"WNBA_(\d{4})(\d{2})(\d{2})\d([A-Z]{2,4})$", gid)
    if m:
        result.update({
            "sport_type": "basketball",
            "league": "WNBA",
            "season": int(m.group(1)),
            "month": int(m.group(2)),
            "day": int(m.group(3)),
            "home_team": m.group(4),
            "date_str": f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
            "display": f"@ {m.group(4)} ({m.group(1)}-{m.group(2)}-{m.group(3)})",
        })
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

    # Generic season-total key: {LEAGUE}_{SEASON}_SEASON_TOTAL  (NLL, DGPT, PLL, X-League, etc.)
    m = re.match(r"([A-Z][A-Z0-9-]+)_(\d{4})_SEASON_TOTAL$", gid)
    if m:
        league_name = m.group(1)
        season = int(m.group(2))
        sport_type = classify_sport(league_name)
        result.update({
            "sport_type": sport_type,
            "league": league_name,
            "season": season,
            "synthetic": True,
            "display": f"{league_name} {season} Season",
        })
        return result

    # Cricket match key from scrape_cricket.py:
    # cricket-{league}-{date}-{cricsheet_id}
    m = re.match(r"cricket-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})-(.+)$", gid, re.I)
    if m:
        league_name = m.group(1).upper()
        date_str    = m.group(2)
        season      = int(date_str[:4])
        result.update({
            "sport_type": "cricket",
            "league":     league_name,
            "season":     season,
            "date_str":   date_str,
            "display":    f"{league_name} {date_str}",
        })
        return result

    # Curling match key from scrape_curling.py:
    # curling-{league_slug}-{year}-{championship_id}-{draw_idx}-{game_idx}
    m = re.match(r"curling-([a-z0-9-]+)-(\d{4})-(\d+)-(\d+)-(\d+)$", gid, re.I)
    if m:
        league_slug = m.group(1)
        season = int(m.group(2))
        result.update({
            "sport_type": "curling",
            "league": league_slug.upper(),
            "season": season,
            "display": f"{league_slug.upper()} {season}",
        })
        return result

    # CurlingZone event key from scrape_curling.py:
    # curling-cz-event-{year}-{event_id}-{showgameid}
    m = re.match(r"curling-([a-z0-9-]+)-(\d{4})-(\d+)-(\d+)$", gid, re.I)
    if m:
        league_slug = m.group(1)
        season = int(m.group(2))
        result.update({
            "sport_type": "curling",
            "league": league_slug.upper(),
            "season": season,
            "display": f"{league_slug.upper()} {season}",
        })
        return result

    # Soccer season/team aggregate key from scrape_soccer.py:
    # soccer-{league_code}-{season}-{team_id}
    m = re.match(r"soccer-(mls|nwsl|uslc|usl1|usls|mlsnp|nasl)-(\d{4})-(.+)$", gid, re.I)
    if m:
        code = m.group(1).lower()
        league_map = {
            "mls": "MLS",
            "nwsl": "NWSL",
            "uslc": "USLC",
            "usl1": "USL1",
            "usls": "USLS",
            "mlsnp": "MLSNP",
            "nasl": "NASL",
        }
        league_name = league_map.get(code, code.upper())
        season = int(m.group(2))
        team_id = m.group(3)
        result.update({
            "sport_type": "soccer",
            "league": league_name,
            "season": season,
            "team": team_id,
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

# College abbreviation to full name mapping
COLLEGE_ABBR_TO_NAME = {
    # Power 5 / ACC
    "BC": "Boston College", "CLEM": "Clemson", "DUKE": "Duke", "FSU": "Florida State",
    "GT": "Georgia Tech", "LOU": "Louisville", "MIAMI": "Miami", "NC": "North Carolina",
    "NCSU": "North Carolina State", "PITT": "Pittsburgh", "RUSS": "Rutgers", "SCAR": "South Carolina",
    "SNCN": "Syracuse", "UVA": "Virginia", "VT": "Virginia Tech", "WAKE": "Wake Forest",
    # SEC
    "ALA": "Alabama", "ARK": "Arkansas", "AUBA": "Auburn", "FLEX": "Florida",
    "UGA": "Georgia", "TAMU": "Texas A&M", "UK": "Kentucky", "LSU": "LSU",
    "MISS": "Mississippi", "MSST": "Mississippi State", "MIZZ": "Missouri", "TENN": "Tennessee",
    "TXLH": "Texas", "VAND": "Vanderbilt", "OKLA": "Oklahoma", "OUST": "Oklahoma State",
    # Big 12 (current and former)
    "BAYLOR": "Baylor", "ISU": "Iowa State", "KU": "Kansas", "KSTATE": "Kansas State",
    "TCU": "TCU", "TEXAS": "Texas", "TTU": "Texas Tech", "WVU": "West Virginia",
    # Pac-12 / West
    "ARIZ": "Arizona", "ASU": "Arizona State", "CAL": "California", "COLO": "Colorado",
    "OREG": "Oregon", "OSU": "Oregon State", "STAN": "Stanford", "USC": "USC",
    "UTAH": "Utah", "WASH": "Washington", "WSU": "Washington State",
    # Big Ten
    "ILL": "Illinois", "IU": "Indiana", "IOWA": "Iowa", "MICH": "Michigan",
    "MIST": "Michigan State", "MINN": "Minnesota", "NEBR": "Nebraska", "NWU": "Northwestern",
    "OHST": "Ohio State", "PSU": "Penn State", "PURDUE": "Purdue", "RUTG": "Rutgers",
    "WISC": "Wisconsin",
    # Group of 5
    "AIRFORCE": "Air Force", "ARMY": "Army", "NAVY": "Navy", "MEMY": "Memphis",
    "SMU": "SMU", "TULANE": "Tulane", "HOUSTON": "Houston", "RICE": "Rice",
    "UCF": "UCF", "USFLAM": "USF", "UCONN": "UConn", "FAU": "Florida Atlantic",
    "FIU": "FIU", "LA-LAFA": "Louisiana-Lafayette", "LA-MUNO": "Louisiana-Monroe",
    "TXSTATE": "Texas State", "TROY": "Troy", "MARSHALL": "Marshall",
    "OLD DOM": "Old Dominion", "CUSA": "CUSA",
    # Other notable programs
    "BYU": "BYU", "UNT": "North Texas", "SBDGO": "San Diego State",
    "UNLV": "UNLV", "SJSTATE": "San Jose State", "NEVADA": "Nevada",
    "UTEP": "UTEP", "NMEXICO": "New Mexico", "WYOMING": "Wyoming", "HAWAII": "Hawaii",
    # Ivy League
    "COLUM": "Columbia", "CORNELL": "Cornell", "DMOUTH": "Dartmouth", "HARVARD": "Harvard",
    "PENN": "Penn", "PRINCETON": "Princeton", "YALE": "Yale", "BROWN": "Brown",
    # Patriot League (sample)
    "ANIMAL": "Colgate", "GATES": "Colgate", "FORDHAM": "Fordham", "BUCKNELL": "Bucknell",
    # CAA/Colonial (sample)
    "JAMES": "James Madison", "HOFSTRA": "Hofstra", "TTOWN": "Towson",
    # SWAC
    "JACKSON": "Jackson State", "GRAMB": "Grambling", "PV": "Prairie View", "TXSU": "Texas Southern",
    "FAMU": "FAMU", "NCATSU": "North Carolina A&T", "SCSU": "South Carolina State",
    # MEAC
    "DELSTATE": "Delaware State", "MORGAN": "Morgan State", "COPPIN": "Coppin State",
    "HOWARD": "Howard", "BETHUNE": "Bethune-Cookman", "NORFOLK": "Norfolk State",
    # Big Sky / FCS
    "MONTANA": "Montana", "NMONTANA": "Northern Montana", "WEBER": "Weber State",
    "IDAHO": "Idaho", "EASHINGTON": "Eastern Washington", "SACDIEGO": "San Diego",
    # Generic fallback
    "STATE": "State", "UNIV": "University"
}

def _article_sort_key(article):
    return (
        article.get("date") or "",
        article.get("indexed_at") or "",
        article.get("title") or "",
    )


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

    # Map college abbreviation to full name if available
    school_abbr = entry.get("school", "")
    school_name = COLLEGE_ABBR_TO_NAME.get(school_abbr, school_abbr)

    return {
        "school": school_name,
        "fdb_url": url,
        "seasons": seasons,
        "career": career,
    }


# ─── this week in history ────────────────────────────────────────────────────

def build_this_week(game_index: list) -> None:
    """Build docs/data/this-week.json: games from each day of the current calendar
    week (Mon–Sun) across all historical years, plus top stat highlights."""
    from datetime import date, timedelta

    today = date.today()
    monday = today - timedelta(days=today.weekday())  # weekday() 0=Mon, 6=Sun
    week_days = [monday + timedelta(days=i) for i in range(7)]

    WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    # stat_key → (display_label, normalisation_weight for highlight ranking)
    # Higher weight = more "impressive" per unit
    HIGHLIGHT_STATS = {
        "passing_yards":   ("Pass Yds",    1.0),
        "rushing_yards":   ("Rush Yds",    1.5),
        "receiving_yards": ("Rec Yds",     1.5),
        "passing_tds":     ("Pass TDs",   45.0),
        "rushing_tds":     ("Rush TDs",   55.0),
        "receiving_tds":   ("Rec TDs",    55.0),
        "goals":           ("Goals",      20.0),
        "assists":         ("Assists",    15.0),
        "blocks":          ("Blocks",     18.0),
        "turnovers":       ("Turnovers",   8.0),
        "touches":         ("Touches",     2.0),
        "o_points":        ("O-Pts",       3.0),
        "d_points":        ("D-Pts",       4.0),
        "plus_minus":      ("+/-",         5.0),
        "yardsThrown":     ("Yds Thrown",  0.9),
        "yardsReceived":   ("Yds Rec'd",   1.4),
        "points":          ("Points",     12.0),
        "field_goals":     ("FGs",        35.0),
    }

    SPORT_ORDER = ["Football", "Soccer", "Cricket", "Curling", "Lacrosse", "Ultimate Disc", "Basketball", "Disc Golf", "Other"]
    FOOTBALL = {"UFL", "USFL", "XFL", "CFL", "AF1", "AAF", "ELF", "AFL", "IFL", "NAL", "LFA", "X-LEAGUE", "XLEAGUE", "MLFB", "FCF"}
    BASKETBALL = {"BIG3", "SLAMBALL", "UNRIVALED", "WNBA"}
    DISC = {"AUDL", "UFA", "PUL"}
    LACROSSE = {"NLL", "PLL"}
    DISCGOLF = {"DGPT"}
    SOCCER = {"MLS", "NWSL", "USLC", "USL1", "USLS", "MLSNP", "NASL"}
    CRICKET = {"T20I", "ODI", "TESTS", "IPL", "BBL", "WBBL", "PSL", "MLC", "CPL", "WPL", "BPL", "LPL", "HND", "ILT20", "SA20", "NPL"}
    CURLING = {"WCF-WORLD", "WCF-EUROPE", "WCF-PANCONT", "WCF-OLYMPIC", "WCF-QUAL", "WCF-OTHER", "CURLING-EVENTS"}

    def classify_sport(league: str, sport_slug: str) -> str:
        up = (league or "").upper()
        base = (sport_slug or "").split("-", 1)[0].upper()
        token = up.split()[0] if up else ""
        if token in LACROSSE or base in LACROSSE or "NLL" in up or "PLL" in up:
            return "Lacrosse"
        if token in DISCGOLF or base in DISCGOLF or "DGPT" in up:
            return "Disc Golf"
        if token in FOOTBALL or base in FOOTBALL or any(f in up for f in ("XFL", "USFL", "UFL", "CFL", "AF1", "YARD", "FCF", "FAN CONTROLLED")):
            return "Football"
        if token in SOCCER or base in SOCCER:
            return "Soccer"
        if token in CRICKET or base in CRICKET:
            return "Cricket"
        if token in CURLING or token == "WCF" or base in CURLING or base == "WCF" or up.startswith("WCF ") or "CURLING" in up:
            return "Curling"
        if token in DISC or base in DISC or "AUDL" in up or "UFA" in up or "PUL" in up or "PREMIER ULTIMATE" in up:
            return "Ultimate Disc"
        if token in BASKETBALL or base in BASKETBALL or "BIG3" in up or "SLAMBALL" in up or "UNRIVALED" in up or "WNBA" in up:
            return "Basketball"
        return "Other"

    # Build a quick slug→game_index_meta lookup for O(1) access
    game_by_mm_dd: dict[str, list] = {}
    for gm in game_index:
        ds = gm.get("date_str", "")
        if len(ds) >= 10:
            mm_dd = ds[5:10]
            game_by_mm_dd.setdefault(mm_dd, []).append(gm)

    _POS_ABBREVS = {"QB", "RB", "WR", "TE", "K", "P", "LB", "CB", "DB", "DE",
                    "DT", "OL", "OT", "OG", "C", "LS", "S", "SS", "FS", "NT"}

    days_data = []
    for day in week_days:
        mm_dd = day.strftime("%m-%d")
        weekday_name = WEEKDAY_NAMES[day.weekday()]
        month_name = MONTH_NAMES[day.month - 1]
        label = f"{weekday_name} {month_name} {day.day}"

        day_games_by_key = {}
        all_highlights = []

        for gm in game_by_mm_dd.get(mm_dd, []):
            slug = gm["slug"]
            game_file = SITE_DATA / "games" / f"{slug}.json"
            if not game_file.exists():
                continue
            try:
                gdata = json.loads(game_file.read_text())
            except Exception:
                continue

            score_away = gdata.get("score_away")
            score_home = gdata.get("score_home")
            season_val = gdata.get("season", "")
            season_int = int(season_val) if str(season_val).isdigit() else 0
            game_obj = {
                "slug":       slug,
                "display":    gdata.get("display", ""),
                "league":     gdata.get("league", ""),
                "season":     season_val,
                "date_str":   gdata.get("date_str", ""),
                "away_team":  gdata.get("away_team", ""),
                "home_team":  gdata.get("home_team", ""),
                "score_away": score_away,
                "score_home": score_home,
                "sport_slug": gdata.get("sport_slug", ""),
                "sport":      classify_sport(gdata.get("league", ""), gdata.get("sport_slug", "")),
                "synthetic":  bool(gdata.get("synthetic", False)),
            }
            dedup_key = (
                game_obj["league"],
                season_int,
                game_obj["date_str"],
                game_obj["away_team"],
                game_obj["home_team"],
                score_away,
                score_home,
            )
            existing = day_games_by_key.get(dedup_key)
            if existing is None:
                day_games_by_key[dedup_key] = game_obj
            else:
                old_rank = (1 if existing.get("synthetic") else 0, len(existing.get("slug", "")))
                new_rank = (1 if game_obj.get("synthetic") else 0, len(game_obj.get("slug", "")))
                if new_rank < old_rank:
                    day_games_by_key[dedup_key] = game_obj

            for player in gdata.get("players", []):
                # Skip placeholder names (e.g. 'TOR QB', 'ST. LOUIS BATTLEHAWKS QB', 'Stars QB')
                cname = player.get("canonical_name", "")
                if not any(c.islower() for c in cname):
                    continue
                words = cname.split()
                if len(words) < 2 or words[-1].upper() in _POS_ABBREVS:
                    continue
                stats = player.get("stats", {})
                for stat_key, (stat_lbl, weight) in HIGHLIGHT_STATS.items():
                    raw = stats.get(stat_key)
                    if raw is None:
                        continue
                    try:
                        val = float(raw)
                    except (TypeError, ValueError):
                        continue
                    if val <= 0:
                        continue
                    all_highlights.append({
                        "canonical_id":   player.get("canonical_id", ""),
                        "canonical_name": player.get("canonical_name", ""),
                        "stat":           stat_key,
                        "stat_label":     stat_lbl,
                        "value":          int(val) if val == int(val) else round(val, 1),
                        "game_display":   gdata.get("display", ""),
                        "game_slug":      slug,
                        "league":         f"{gdata.get('league','')} {gdata.get('season','')}".strip(),
                        "date_str":       gdata.get("date_str", ""),
                        "_score":         val * weight,
                    })

        day_games = list(day_games_by_key.values())

        # Newest season first, then alphabetical league
        day_games.sort(key=lambda g: (-int(g.get("season") or 0), g.get("league", "")))

        # Group games by league to keep each day compact in the UI.
        league_groups = []
        games_by_league = {}
        for g in day_games:
            league = g.get("league", "") or "Other"
            if league not in games_by_league:
                games_by_league[league] = []
                league_groups.append({"league": league, "games": games_by_league[league]})
            games_by_league[league].append(g)
        for grp in league_groups:
            grp["count"] = len(grp["games"])

        sport_counts = {k: 0 for k in SPORT_ORDER}
        for g in day_games:
            sport_name = g.get("sport", "Other")
            sport_counts[sport_name] = sport_counts.get(sport_name, 0) + 1
        sport_summary = [
            {"sport": sport, "count": count}
            for sport, count in sport_counts.items()
            if count > 0
        ]

        # Top highlights: deduplicate so same player doesn't appear twice, cap at 6
        all_highlights.sort(key=lambda h: -h["_score"])
        seen_players = set()
        top_highlights = []
        for h in all_highlights:
            pid = h["canonical_id"]
            if pid and pid in seen_players:
                continue
            seen_players.add(pid)
            top_highlights.append({k: v for k, v in h.items() if k != "_score"})
            if len(top_highlights) >= 6:
                break

        days_data.append({
            "date":        day.isoformat(),
            "mm_dd":       mm_dd,
            "weekday":     weekday_name,
            "label":       label,
            "game_count":  len(day_games),
            "games":       day_games,
            "game_groups": league_groups,
            "sport_summary": sport_summary,
            "highlights":  top_highlights,
        })

    week_label = (
        f"{MONTH_NAMES[week_days[0].month-1]} {week_days[0].day}"
        f" – {MONTH_NAMES[week_days[6].month-1]} {week_days[6].day}"
    )
    this_week = {
        "generated":  today.isoformat(),
        "week_label": week_label,
        "today":      today.isoformat(),
        "days":       days_data,
    }
    (SITE_DATA / "this-week.json").write_text(
        json.dumps(this_week, indent=2), encoding="utf-8"
    )
    total_games = sum(d["game_count"] for d in days_data)
    print(f"Written this-week.json ({total_games} historical games across 7 days)")


# ─── main ───────────────────────────────────────────────────────────────────

def main():
    print("Loading data ...")
    players_merged = json.loads((MERGED / "players_merged.json").read_text())
    id_lookup = json.loads((MERGED / "id_to_canonical.json").read_text())
    raw_stats = json.loads((RAW / "player_stats.json").read_text())
    sports = json.loads((RAW / "sports.json").read_text())
    raw_players = json.loads((RAW / "players.json").read_text())

    # ── Drop internal-accounting sports (e.g. "50 Yard") from every artifact ───
    # These leagues exist in source SQL exports for back-office bookkeeping only
    # and should never appear on the public site (player pages, league index,
    # game pages, sitemap, HOF, search index, etc.).
    _EXCLUDED_SPORT_NAME_NORMS = {"50yard"}
    _EXCLUDED_LEAGUE_NAMES = {"50 Yard", "50YARD", "50 YARD", "50-yard", "50-Yard"}
    excluded_sport_ids = {
        s["id"] for s in sports
        if re.sub(r"\s+", "", (s.get("name") or "")).lower() in _EXCLUDED_SPORT_NAME_NORMS
    }
    if excluded_sport_ids:
        excluded_pids = {p["id"] for p in raw_players if p.get("sport_id") in excluded_sport_ids}
        _before_p = len(raw_players)
        _before_s = len(raw_stats)
        raw_players = [p for p in raw_players if p.get("sport_id") not in excluded_sport_ids]
        raw_stats = [s for s in raw_stats if s.get("player_id") not in excluded_pids]
        sports = [s for s in sports if s["id"] not in excluded_sport_ids]
        # Scrub merged players: drop excluded appearances, prune league name
        for _mp in players_merged:
            _apps = _mp.get("appearances") or []
            _kept = [a for a in _apps if a.get("sport_id") not in excluded_sport_ids]
            if len(_kept) != len(_apps):
                _mp["appearances"] = _kept
            _lgs = _mp.get("leagues") or []
            _mp["leagues"] = [lg for lg in _lgs if lg not in _EXCLUDED_LEAGUE_NAMES]
        print(f"Excluded internal-accounting sports {excluded_sport_ids}: "
              f"dropped {_before_p - len(raw_players)} players, "
              f"{_before_s - len(raw_stats)} stat rows")
    # Stash for later filtering of raw_games (loaded much later in main()).
    _excluded_sport_ids = excluded_sport_ids
    _excluded_league_names = _EXCLUDED_LEAGUE_NAMES
    
    # Load articles if available
    articles_raw_path = RAW / "articles_raw.json"
    player_articles = {}  # Map of player_name -> [articles]
    if articles_raw_path.exists():
        articles_data = json.loads(articles_raw_path.read_text())
        for article in articles_data.get("articles", []):
            for player_name in article.get("players_matched", []):
                if player_name not in player_articles:
                    player_articles[player_name] = []
                player_articles[player_name].append({
                    "source": article.get("source"),
                    "source_name": article.get("source_name"),
                    "trust_level": article.get("trust_level"),
                    "title": article.get("title"),
                    "link": article.get("link"),
                    "date": article.get("date"),
                    "summary": article.get("summary"),
                    "indexed_at": article.get("indexed_at"),
                })
        for player_name, articles in player_articles.items():
            player_articles[player_name] = sorted(articles, key=_article_sort_key, reverse=True)
        print(f"Loaded articles for {len(player_articles)} players")
    
    # Load coaches if available
    coaches_merged_path = MERGED / "coaches_merged.json"
    coaches_merged = (
        json.loads(coaches_merged_path.read_text())
        if coaches_merged_path.exists()
        else []
    )
    if coaches_merged:
        print(f"Loaded {len(coaches_merged)} canonical coaches")

    # Build (league, year, team) -> [coach summary] index. Used to attach
    # per-season coaching staff to player pages, and to render coach-of
    # rosters on coach pages. Each coach summary has the canonical id +
    # name + role string from that team-season appearance.
    team_season_coaches: dict[tuple[str, int, str], list[dict]] = {}
    # Priority order for sorting roles within a team-season (HC first,
    # coordinators next, position coaches last).
    _ROLE_PRIORITY = [
        ("head coach", 0),
        ("offensive coordinator", 1),
        ("defensive coordinator", 1),
        ("special teams coordinator", 2),
        ("assistant head coach", 3),
    ]

    def _role_rank(role: str) -> int:
        rl = (role or "").lower()
        for needle, rank in _ROLE_PRIORITY:
            if needle in rl:
                # Demote "assistant head coach" since it contains "head coach".
                if "assistant" in rl and needle == "head coach":
                    continue
                return rank
        return 9

    for _coach in coaches_merged:
        for _app in _coach.get("appearances", []):
            _league = _app.get("league")
            _year = _app.get("year")
            _team = _app.get("team")
            if not (_league and _year and _team):
                continue
            key = (_league, int(_year), _team)
            team_season_coaches.setdefault(key, []).append({
                "canonical_id": _coach["canonical_id"],
                "name": _coach["canonical_name"],
                "role": _app.get("role") or "",
            })

    # Sort each team-season's coach list by role priority then name.
    for _k, _coaches in team_season_coaches.items():
        _coaches.sort(key=lambda c: (_role_rank(c["role"]), c["name"]))

    if team_season_coaches:
        print(f"Built coach-of-team-season index: {len(team_season_coaches)} team-seasons")

    def _register_injected_players(_players):
        """Register synthetic/injected player IDs into canonical lookup maps."""
        for _p in _players:
            _pid_raw = _p.get("id")
            _full_name = (_p.get("full_name") or "").strip()
            if _pid_raw is None or not _full_name:
                continue

            _pid = str(_pid_raw)
            _cid = slugify(_full_name)
            id_lookup[_pid] = _cid

            _existing = next((cp for cp in players_merged if cp.get("canonical_id") == _cid), None)
            if _existing is None:
                players_merged.append({
                    "canonical_id": _cid,
                    "canonical_name": _full_name,
                    "positions": [_p.get("position")] if _p.get("position") else [],
                    "leagues": [_p.get("league")] if _p.get("league") else [],
                    "sport_ids": [],
                    "sport_names": [],
                    "ambiguous": False,
                    "record_count": 1,
                    "appearances": [{
                        "id": int(_pid),
                        "full_name": _full_name,
                        "team": _p.get("team", ""),
                        "position": _p.get("position", ""),
                        "sport_id": _p.get("sport_id"),
                        "league": _p.get("league", ""),
                        "jersey": _p.get("jersey"),
                        "college": _p.get("college"),
                        "college_stats": _p.get("college_stats"),
                        "height": _p.get("height"),
                        "weight": _p.get("weight"),
                    }],
                    "_raw_ids": [int(_pid)],
                })
            else:
                _raw_ids = _existing.setdefault("_raw_ids", [])
                if int(_pid) not in _raw_ids:
                    _raw_ids.append(int(_pid))
                _apps = _existing.setdefault("appearances", [])
                if not any(str(a.get("id")) == _pid for a in _apps):
                    _apps.append({
                        "id": int(_pid),
                        "full_name": _full_name,
                        "team": _p.get("team", ""),
                        "position": _p.get("position", ""),
                        "sport_id": _p.get("sport_id"),
                        "league": _p.get("league", ""),
                        "jersey": _p.get("jersey"),
                        "college": _p.get("college"),
                        "college_stats": _p.get("college_stats"),
                        "height": _p.get("height"),
                        "weight": _p.get("weight"),
                    })

    # ── Register new league players in id_lookup (before any aggregation) ──────
    # New league players (IFL, NAL, X-League, etc.) have synthetic IDs that
    # need to map to canonical IDs. Since they're new, create canonical IDs
    # and register them so stats can be aggregated.
    _new_league_player_files = [
        "ifl_players.json", "ifl_official_players.json", "af1_players.json", "nal_players.json", "xleague_players.json", "lfa_players.json",
        "xfl_2020_players.json",
    ]
    for _nlf in _new_league_player_files:
        _nlf_path = RAW / _nlf
        if _nlf_path.exists():
            _nl_players = json.loads(_nlf_path.read_text())
            for _nl_player in _nl_players:
                _pid = str(_nl_player.get("id", ""))
                _full_name = _nl_player.get("full_name", "")
                if _pid and _full_name:
                    # Create canonical_id from name (slugified)
                    _cid = slugify(_full_name)
                    # Register in id_lookup
                    id_lookup[_pid] = _cid
                    # Add to players_merged if not already there
                    if not any(p.get("canonical_id") == _cid for p in players_merged):
                        players_merged.append({
                            "canonical_id": _cid,
                            "canonical_name": _full_name,
                            "positions": [],
                            "leagues": [_nl_player.get("league", "")],
                            "sport_ids": [],
                            "sport_names": [],
                            "ambiguous": False,
                            "record_count": 1,
                            "appearances": [{
                                "id": int(_pid),
                                "full_name": _full_name,
                                "team": _nl_player.get("team", ""),
                                "position": _nl_player.get("position", ""),
                                "sport_id": None,
                                "league": _nl_player.get("league", ""),
                                "jersey": _nl_player.get("jersey"),
                                "college": _nl_player.get("college"),
                                "college_stats": None,
                                "height": _nl_player.get("height"),
                                "weight": _nl_player.get("weight"),
                            }],
                            "_raw_ids": [int(_pid)],
                        })


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
        _register_injected_players(cfl_hist_players)
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
        _register_injected_players(elf_hist_players)
        years_seen = sorted({r.get("_year") for r in elf_hist_stats if r.get("_year")})
        print(f"Injected {len(elf_hist_players)} ELF historical players, "
              f"{len(elf_hist_stats)} stat rows (years: {years_seen})")

    # ── Inject Cricket stats (built by scrape_cricket.py) ───────────────────
    _cricket_players_file = RAW / "cricket_players.json"
    _cricket_stats_file   = RAW / "cricket_stats.json"
    if _cricket_players_file.exists() and _cricket_stats_file.exists():
        cricket_players = json.loads(_cricket_players_file.read_text())
        cricket_stats   = json.loads(_cricket_stats_file.read_text())
        raw_players.extend(cricket_players)
        raw_stats.extend(cricket_stats)
        _register_injected_players(cricket_players)
        _cricket_leagues = sorted({r.get("_league") for r in cricket_stats if r.get("_league")})
        _cricket_years   = sorted({r.get("_year")   for r in cricket_stats if r.get("_year")})
        print(f"Injected {len(cricket_players)} cricket players, "
              f"{len(cricket_stats)} stat rows "
              f"(leagues: {_cricket_leagues}, years: {_cricket_years[:3]}…{_cricket_years[-3:] if len(_cricket_years) > 3 else ''})")

        # ── Inject Curling stats (built by scrape_curling.py) ───────────────────
        _curling_players_file = RAW / "curling_players.json"
        _curling_stats_file   = RAW / "curling_stats.json"
        if _curling_players_file.exists() and _curling_stats_file.exists():
          curling_players = json.loads(_curling_players_file.read_text())
          curling_stats   = json.loads(_curling_stats_file.read_text())
          raw_players.extend(curling_players)
          raw_stats.extend(curling_stats)
          _register_injected_players(curling_players)
          _curling_leagues = sorted({r.get("_league") for r in curling_stats if r.get("_league")})
          _curling_years   = sorted({r.get("_year")   for r in curling_stats if r.get("_year")})
          print(f"Injected {len(curling_players)} curling players, "
              f"{len(curling_stats)} stat rows "
              f"(leagues: {_curling_leagues}, years: {_curling_years[:3]}…{_curling_years[-3:] if len(_curling_years) > 3 else ''})")

    # ── Inject US Soccer stats (built by scrape_soccer.py) ───────────────────
    _soccer_players_file = RAW / "soccer_players.json"
    _soccer_stats_file   = RAW / "soccer_stats.json"
    if _soccer_players_file.exists() and _soccer_stats_file.exists():
        soccer_players = json.loads(_soccer_players_file.read_text())
        soccer_stats   = json.loads(_soccer_stats_file.read_text())
        raw_players.extend(soccer_players)
        raw_stats.extend(soccer_stats)
        _register_injected_players(soccer_players)
        _soccer_leagues = sorted({r.get("_league") for r in soccer_stats if r.get("_league")})
        _soccer_years   = sorted({r.get("_year")   for r in soccer_stats if r.get("_year")})
        print(f"Injected {len(soccer_players)} soccer players, "
              f"{len(soccer_stats)} stat rows "
              f"(leagues: {_soccer_leagues}, years: {_soccer_years[:3]}…{_soccer_years[-3:] if len(_soccer_years) > 3 else ''})")

    # ── Inject new league data from individual scrapers ───────────────────
    _new_league_pairs = [
        ("nll_historical_players.json", "nll_historical_stats.json", "NLL historical"),
        ("pll_players.json",            "pll_stats.json",            "PLL"),
        ("pul_players.json",            "pul_stats.json",            "PUL"),
        ("fcf_players.json",            "fcf_stats.json",            "FCF"),
        ("xfl_2020_players.json",       "xfl_2020_stats.json",       "XFL 2020"),
        ("ifl_players.json",            "ifl_stats.json",            "IFL"),
        ("ifl_official_players.json",   "ifl_official_stats.json",   "IFL (official)"),
        ("af1_players.json",            "af1_stats.json",            "AF1"),
        ("nal_players.json",            "nal_stats.json",            "NAL"),
        ("lfa_players.json",            "lfa_stats.json",            "LFA"),
        ("xleague_players.json",        "xleague_stats.json",        "X-League"),
        ("au_players.json",             "au_stats.json",             "Athletes Unlimited"),
        ("dgpt_players.json",           "dgpt_stats.json",           "DGPT"),
        ("ufl_players.json",            "ufl_stats.json",            "UFL"),
        ("unrivaled_players.json",      "unrivaled_stats.json",      "Unrivaled"),
        ("wnba_players.json",           "wnba_stats.json",           "WNBA"),
    ]
    for _pfile, _sfile, _label in _new_league_pairs:
        _pf = RAW / _pfile
        _sf = RAW / _sfile
        if _pf.exists() and _sf.exists():
            _ps = json.loads(_pf.read_text())
            _ss = json.loads(_sf.read_text())
            if _ps or _ss:
                raw_players.extend(_ps)
                raw_stats.extend(_ss)
                _yrs = sorted({r.get("_year") for r in _ss if r.get("_year")})
                print(f"Injected {len(_ps)} {_label} players, "
                      f"{len(_ss)} stat rows (years: {_yrs})")

    # Load games table if available
    raw_games = json.loads((RAW / "games.json").read_text()) if (RAW / "games.json").exists() else []
    if _excluded_sport_ids:
        _before_g = len(raw_games)
        raw_games = [g for g in raw_games if g.get("sport_id") not in _excluded_sport_ids]
        if _before_g != len(raw_games):
            print(f"Excluded {_before_g - len(raw_games)} games for internal-accounting sports")
    curling_games_file = RAW / "curling_games.json"
    if curling_games_file.exists():
        curling_games = json.loads(curling_games_file.read_text())
        raw_games.extend(curling_games)
        print(f"Loaded {len(curling_games)} curling match rows")
    cricket_games_file = RAW / "cricket_games.json"
    if cricket_games_file.exists():
        cricket_games = json.loads(cricket_games_file.read_text())
        raw_games.extend(cricket_games)
        print(f"Loaded {len(cricket_games)} cricket match rows")
    soccer_games_file = RAW / "soccer_games.json"
    if soccer_games_file.exists():
        soccer_games = json.loads(soccer_games_file.read_text())
        raw_games.extend(soccer_games)
        print(f"Loaded {len(soccer_games)} soccer fixture rows")
    
    # Also load scraper-produced games that are not in SQL exports
    aaf_games_file = RAW / "aaf_2019_games.json"
    if aaf_games_file.exists():
        aaf_games = json.loads(aaf_games_file.read_text())
        raw_games.extend(aaf_games)

    af1_games_file = RAW / "af1_games.json"
    if af1_games_file.exists():
        af1_games = json.loads(af1_games_file.read_text())
        raw_games.extend(af1_games)
        print(f"Loaded {len(af1_games)} AF1 game records")

    ifl_official_games_file = RAW / "ifl_official_games.json"
    if ifl_official_games_file.exists():
        ifl_official_games = json.loads(ifl_official_games_file.read_text())
        raw_games.extend(ifl_official_games)
        print(f"Loaded {len(ifl_official_games)} IFL (official) game records")

    nal_games_file = RAW / "nal_games.json"
    if nal_games_file.exists():
        nal_games = json.loads(nal_games_file.read_text())
        raw_games.extend(nal_games)

    xfl_2020_games_file = RAW / "xfl_2020_games.json"
    if xfl_2020_games_file.exists():
        xfl_2020_games = json.loads(xfl_2020_games_file.read_text())
        raw_games.extend(xfl_2020_games)

    ufl_games_file = RAW / "ufl_games.json"
    if ufl_games_file.exists():
        ufl_games = json.loads(ufl_games_file.read_text())
        raw_games.extend(ufl_games)
        print(f"Loaded {len(ufl_games)} UFL game records")

    wnba_games_file = RAW / "wnba_games.json"
    if wnba_games_file.exists():
        wnba_games = json.loads(wnba_games_file.read_text())
        raw_games.extend(wnba_games)
        print(f"Loaded {len(wnba_games)} WNBA game records")
    
    # Load play-by-play data by game_id for rendering
    pbp_by_game_id = {}
    ufl_pbp_file = RAW / "ufl_pbp.json"
    if ufl_pbp_file.exists():
        ufl_pbp_records = json.loads(ufl_pbp_file.read_text())
        for pbp in ufl_pbp_records:
            game_id = pbp.get("game_id")
            if game_id:
                pbp_by_game_id[str(game_id)] = pbp
        print(f"Loaded {len(pbp_by_game_id)} UFL play-by-play records")

    # IFL play-by-play (goifl.com) -- convert to UFL-compatible viz schema.
    ifl_pbp_file = RAW / "ifl_official_pbp.json"
    if ifl_pbp_file.exists():
        ifl_pbp_records = json.loads(ifl_pbp_file.read_text())
        # Index IFL games by game_id so we can look up home/away team names.
        ifl_games_lookup = {}
        ifl_games_file = RAW / "ifl_official_games.json"
        if ifl_games_file.exists():
            for g in json.loads(ifl_games_file.read_text()):
                gid = g.get("game_id")
                if gid:
                    ifl_games_lookup[str(gid)] = g
        converted = 0
        for pbp in ifl_pbp_records:
            gid = pbp.get("game_id")
            if not gid:
                continue
            g_meta = ifl_games_lookup.get(str(gid)) or {}
            viz = _convert_ifl_pbp_to_viz(pbp, g_meta)
            if viz:
                pbp_by_game_id[str(gid)] = viz
                converted += 1
        print(f"Loaded {converted} IFL play-by-play records")
    
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
            # Support both naming conventions: DB uses team_home/team_away, scrapers use home_team/away_team
            for team_field in ("team_home", "team_away", "home_team", "away_team"):
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
    
    # ── Build college abbreviation → full name lookup ──
    # Extract full school names from college stats data
    college_abbr_to_full = {}
    for fdb_url, entry in college_stats_data.items():
        school_name = entry.get("school", "")
        if school_name:
            # entry["abbr"] would be the abbreviation used in player rosters
            # We build a reverse lookup: abbreviation → full name
            # The abbreviation is typically stored in player appearances as "college"
            pass
    # Note: college_stats_data is indexed by FDB URL, not by abbreviation.
    # We'll use this data in _match_college to resolve individual players.

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
            # Prefer a real per-game id from raw_games if one exists for this
            # (sport, week, team) — avoids creating a synthetic week-aggregate
            # game that duplicates a real game from a scraper.
            real_match = None
            if sport_id and week is not None and team:
                real_match = db_game_by_sport_week_team.get((sport_id, week, team))
            if real_match and real_match.get("game_id"):
                gid_str = str(real_match["game_id"])
            elif sport_id and sport_id in sport_map and week is not None and team:
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

        league_name = meta.get("league", "") or row.get("league", "")
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

    # Collected as a side-effect of player processing: which players were
    # on which (league, year, team). Used later to build "players coached"
    # rosters on coach pages.
    league_year_team_players: dict[tuple[str, int, str], list[dict]] = {}

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
            league = entry.get("league", "")
            season = entry.get("season", "")
            # Only aggregate into season_totals if both league and season are present
            # Skip synthetic games with missing seasons to avoid "USFL-" keys
            if league and season:
                skey = f"{league}-{season}"
                for stat, val in entry["stats"].items():
                    by_season[skey][stat] += val
        season_totals = {k: dict(v) for k, v in by_season.items()}

        # Attach coaching staff per (league-season). For each season the
        # player has stats in, derive the team(s) they were on by looking
        # at which team appears most often in their game_log entries
        # for that league-season (a player is on one team all year, so
        # that team appears in every game's home_team or away_team).
        # season_coaches: { "UFL-2024": [ {team, coaches:[{canonical_id,name,role}]} ] }
        season_coaches: dict[str, list[dict]] = {}
        if team_season_coaches:
            # Pre-bucket games by (league, season) and tally team frequencies.
            from collections import Counter as _Counter
            _games_by_ls: dict[tuple[str, int], list[dict]] = {}
            for _g in game_log_by_game:
                _l = _g.get("league") or ""
                _s = _g.get("season")
                if not _l or _s in (None, ""):
                    continue
                try:
                    _yr_g = int(_s)
                except (TypeError, ValueError):
                    continue
                _games_by_ls.setdefault((_l, _yr_g), []).append(_g)

            # Also accept appearance-derived (league, team) when game_log is
            # absent (e.g. coach-as-player synthetic appearances or pure
            # roster injects with team set).
            _app_teams_by_lg: dict[str, set[str]] = {}
            for _app in cp.get("appearances", []):
                _al = _app.get("league") or ""
                _at = _app.get("team") or ""
                if _al and _at:
                    _app_teams_by_lg.setdefault(_al, set()).add(_at)

            for skey in season_totals.keys():
                if "-" not in skey:
                    continue
                _league, _season_str = skey.split("-", 1)
                try:
                    _yr = int(_season_str)
                except ValueError:
                    continue

                _player_teams: list[str] = []
                _games = _games_by_ls.get((_league, _yr), [])
                if _games:
                    _tally: _Counter = _Counter()
                    for _g in _games:
                        for _side in ("away_team", "home_team"):
                            _t = _g.get(_side) or ""
                            if _t:
                                _tally[_t] += 1
                    if _tally:
                        # Player's team = most-frequent team across the
                        # season's games. Take all teams tied for max in
                        # case of mid-season trade (rare but possible).
                        _max_n = max(_tally.values())
                        _player_teams = [
                            t for t, n in _tally.items() if n == _max_n
                        ]
                # Fallback to appearance teams if no game_log evidence.
                if not _player_teams:
                    _player_teams = sorted(_app_teams_by_lg.get(_league, set()))

                _by_team: dict[str, list[dict]] = {}
                for _team in _player_teams:
                    if _team in _by_team:
                        continue
                    # Record this player as having played for this
                    # team-season (used by coach pages).
                    league_year_team_players.setdefault(
                        (_league, _yr, _team), []
                    ).append({
                        "canonical_id": cid,
                        "name": cp["canonical_name"],
                        "position": (cp.get("positions") or [None])[0],
                    })
                    _staff = team_season_coaches.get((_league, _yr, _team))
                    if _staff:
                        _by_team[_team] = _staff
                if _by_team:
                    season_coaches[skey] = [
                        {"team": t, "coaches": s} for t, s in _by_team.items()
                    ]

        player_data = {
            "canonical_id": cid,
            "canonical_name": cp["canonical_name"],
            "positions": cp["positions"],
            "leagues": cp["leagues"],
            "sport_names": cp["sport_names"],
            "ambiguous": cp["ambiguous"],
            "appearances": [
                {**a, "college_full": a.get("college")}  # Add college_full (same as college for now)
                for a in cp["appearances"]
            ],
            "career_totals": totals,
            "season_totals": season_totals,
            "season_coaches": season_coaches,
            "game_log": game_log_by_game,
            "college": _match_college(cp, college_name_index, college_stats_data),
            "nfl":     nfl_stats_data.get(cid),
            "articles": player_articles.get(cp["canonical_name"], [])[:50],  # Top 50 most recent
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

    # ─── Coach player files ───────────────────────────────────────────────
    # Write coaches as player JSON files so they can be linked from league pages.
    # We use league_year_team_players (built during the player loop) to
    # surface "players coached" rosters per coaching appearance.

    for coach in coaches_merged:
        cid = coach["canonical_id"]

        # Enrich coaching appearances: sort chronologically, include role,
        # and attach the per-team-season player roster.
        _apps_raw = coach.get("appearances", [])
        coaching_appearances = sorted(
            [
                {
                    "year": a.get("year"),
                    "league": a.get("league"),
                    "team": a.get("team"),
                    "role": a.get("role") or "",
                }
                for a in _apps_raw
                if a.get("year") and a.get("league") and a.get("team")
            ],
            key=lambda a: (a["year"] or 0, a["league"] or "", a["team"] or ""),
        )

        # Roster of players the coach was on staff with. Dedup by canonical_id;
        # keep the earliest year/team they appeared for as their "coached as".
        _player_seen: dict[str, dict] = {}
        for _ca in coaching_appearances:
            _key = (_ca["league"], _ca["year"], _ca["team"])
            for _p in league_year_team_players.get(_key, []):
                if _p["canonical_id"] in _player_seen:
                    continue
                _player_seen[_p["canonical_id"]] = {
                    "canonical_id": _p["canonical_id"],
                    "name": _p["name"],
                    "position": _p["position"],
                    "year": _ca["year"],
                    "team": _ca["team"],
                    "league": _ca["league"],
                }
        players_coached = sorted(_player_seen.values(), key=lambda p: p["name"])

        # Summary aggregates.
        _years = sorted({a["year"] for a in coaching_appearances if a["year"]})
        _teams = sorted({a["team"] for a in coaching_appearances if a["team"]})
        _leagues = sorted({a["league"] for a in coaching_appearances if a["league"]})
        coach_summary = {
            "year_span": (
                f"{_years[0]}–{_years[-1]}" if len(_years) >= 2
                else (str(_years[0]) if _years else "")
            ),
            "team_count": len(_teams),
            "season_count": len(coaching_appearances),
            "leagues": _leagues,
            "teams": _teams,
        }

        # Build coach player data in same format as regular players
        coach_data = {
            "canonical_id": cid,
            "canonical_name": coach["canonical_name"],
            "positions": [],  # Coaches don't have positions
            "leagues": coach.get("leagues", []),
            "sport_names": ["Football"],  # Coaches are football-related
            "ambiguous": False,
            "appearances": coach.get("appearances", []),
            "career_totals": {},  # Coaches don't have stat totals
            "season_totals": {},
            "season_coaches": {},
            "game_log": [],  # Coaches don't have game logs
            "college": None,
            "nfl": None,
            # Coach-specific enrichment
            "coach_summary": coach_summary,
            "coaching_appearances": coaching_appearances,
            "players_coached": players_coached,
            "roles": coach.get("roles", []),
        }

        write_json_xml(SITE_DATA / "players" / cid, coach_data, root_tag="player")
    
    # Add coaches to search index
    for coach in coaches_merged:
        cid = coach["canonical_id"]
        search_index.append({
            "id": cid,
            "name": coach["canonical_name"],
            "positions": coach.get("roles", []),  # Use coaching roles instead of positions
            "leagues": coach.get("leagues", []),
            "sport_names": ["Football"],
            "ambiguous": False,
            "totals": {},
        })

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

        _m = re.match(r"([a-z0-9-]+)-(\d{4})$", sport_slug)
        _sport_base = (_m.group(1).upper() if _m else "")
        _sport_season = (int(_m.group(2)) if _m else None)
        if _sport_base in SOCCER_LEAGUES and _sport_season is not None:
            soccer_league_games = []
            for rg in raw_games:
                if (rg.get("league") or "").upper() != _sport_base:
                    continue
                start_time = rg.get("start_time") or ""
                if not isinstance(start_time, str) or len(start_time) < 4:
                    continue
                try:
                    game_year = int(start_time[:4])
                except ValueError:
                    continue
                if game_year != _sport_season:
                    continue
                soccer_league_games.append({
                    "slug": game_id_slug(rg.get("game_id") or ""),
                    "display": f"{rg.get('team_away', '')} @ {rg.get('team_home', '')}",
                    "week": rg.get("week", ""),
                    "season": _sport_season,
                    "date_str": start_time[:10],
                    "away_team": rg.get("team_away", ""),
                    "home_team": rg.get("team_home", ""),
                    "team": "",
                    "score_home": rg.get("score_home", ""),
                    "score_away": rg.get("score_away", ""),
                    "channel": rg.get("channel", ""),
                    "synthetic": False,
                    "status": rg.get("status_line", ""),
                    "player_count": 0,
                })
            if soccer_league_games:
                league_games = sorted(
                    soccer_league_games,
                    key=lambda x: (x.get("season") or 0, x.get("week") or 0, x.get("date_str") or "", x.get("slug") or ""),
                )

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

    # ─── Team pages ───────────────────────────────────────────────────────
    # X-League team season pages
    team_seasons_file = RAW / "xleague_team_seasons.json"
    if team_seasons_file.exists():
        print("Writing X-League team pages ...")
        team_seasons = json.loads(team_seasons_file.read_text())
        
        teams_dir = SITE_DATA / "teams"
        teams_dir.mkdir(parents=True, exist_ok=True)
        
        team_count = 0
        for team_name, seasons in team_seasons.items():
            for season, team_data in seasons.items():
                team_slug = slugify(f"xleague-{team_name}-{season}")
                team_file_data = {
                    "slug": team_slug,
                    "team": team_name,
                    "season": season,
                    "league": "X-League",
                    "players": team_data.get("players", []),
                    "record": team_data.get("record", {}),
                }
                write_json_xml(teams_dir / team_slug, team_file_data, root_tag="team")
                team_count += 1
        
        print(f"Written {team_count} team season pages")

    # Seed curling game pages from raw curling match data
    for rg in raw_games:
        league_name = (rg.get("league") or "")
        if league_name.upper() not in {l.upper() for l in CURLING_LEAGUES}:
            continue
        game_id = rg.get("game_id") or ""
        if not game_id:
            continue
        gslug = game_id_slug(game_id)
        if gslug in game_meta:
            continue
        start_time = rg.get("start_time") or ""
        date_str = start_time[:10] if isinstance(start_time, str) else ""
        season = ""
        if len(date_str) >= 4:
            try:
                season = int(date_str[:4])
            except ValueError:
                season = ""
        away_team = rg.get("team_away") or ""
        home_team = rg.get("team_home") or ""
        score_away = rg.get("score_away")
        score_home = rg.get("score_home")
        game_meta[gslug] = {
            "game_id": game_id,
            "slug": gslug,
            "display": f"{away_team} @ {home_team}" if away_team and home_team else game_id,
            "league": league_name,
            "season": season,
            "week": rg.get("week", ""),
            "away_team": away_team,
            "home_team": home_team,
            "score_away": "" if score_away is None else score_away,
            "score_home": "" if score_home is None else score_home,
            "date_str": date_str,
            "channel": rg.get("channel") or "",
            "sport_slug": slugify(f"{league_name}-{season}" if season else league_name),
            "synthetic": False,
        }
        game_sport_slug[gslug] = game_meta[gslug]["sport_slug"]
        game_players_seen[gslug] = set()

    # Seed cricket game pages from raw cricket match data
    for rg in raw_games:
        league_name = (rg.get("league") or "")
        if league_name.upper() not in {l.upper() for l in CRICKET_LEAGUES}:
            continue
        game_id = rg.get("game_id") or ""
        if not game_id:
            continue
        gslug = game_id_slug(game_id)
        if gslug in game_meta:
            continue
        start_time = rg.get("start_time") or ""
        date_str = start_time[:10] if isinstance(start_time, str) else ""
        season = ""
        if len(date_str) >= 4:
            try:
                season = int(date_str[:4])
            except ValueError:
                season = ""
        away_team = rg.get("team_away") or ""
        home_team = rg.get("team_home") or ""
        score_away = rg.get("score_away")
        score_home = rg.get("score_home")
        display = f"{away_team} vs {home_team}" if away_team and home_team else game_id
        status_line = rg.get("status_line") or ""
        if status_line:
            display = f"{display} ({status_line})"
        game_meta[gslug] = {
            "game_id": game_id,
            "slug": gslug,
            "display": display,
            "league": league_name,
            "season": season,
            "week": rg.get("week", ""),
            "away_team": away_team,
            "home_team": home_team,
            "score_away": "" if score_away is None else score_away,
            "score_home": "" if score_home is None else score_home,
            "date_str": date_str,
            "channel": rg.get("channel") or "",
            "sport_slug": slugify(f"{league_name}-{season}" if season else league_name),
            "synthetic": False,
            "venue": rg.get("venue", ""),
            "match_type": rg.get("match_type", ""),
        }
        game_sport_slug[gslug] = game_meta[gslug]["sport_slug"]
        game_players_seen[gslug] = set()

    # Seed fixture-only soccer game pages so league-page matchup links resolve
    # even when the source only provides player stats at the team-season level.
    for rg in raw_games:
        league_name = (rg.get("league") or "").upper()
        if league_name not in SOCCER_LEAGUES:
            continue
        game_id = rg.get("game_id") or ""
        if not game_id:
            continue
        gslug = game_id_slug(game_id)
        if gslug in game_meta:
            continue
        start_time = rg.get("start_time") or ""
        date_str = start_time[:10] if isinstance(start_time, str) else ""
        season = ""
        if len(date_str) >= 4:
            try:
                season = int(date_str[:4])
            except ValueError:
                season = ""
        away_team = rg.get("team_away") or ""
        home_team = rg.get("team_home") or ""
        score_away = rg.get("score_away")
        score_home = rg.get("score_home")
        game_meta[gslug] = {
            "game_id": game_id,
            "slug": gslug,
            "display": f"{away_team} @ {home_team}" if away_team and home_team else game_id,
            "league": league_name,
            "season": season,
            "week": rg.get("week", ""),
            "away_team": away_team,
            "home_team": home_team,
            "score_away": "" if score_away is None else score_away,
            "score_home": "" if score_home is None else score_home,
            "date_str": date_str,
            "channel": rg.get("channel") or "",
            "sport_slug": slugify(f"{league_name}-{season}" if season else league_name),
            "synthetic": False,
        }
        game_sport_slug[gslug] = game_meta[gslug]["sport_slug"]
        game_players_seen[gslug] = set()

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
        
        # Inject play-by-play data if available
        game_id_str = str(meta.get("game_id", gslug))
        if game_id_str in pbp_by_game_id:
            pbp = pbp_by_game_id[game_id_str]
            game_data["scoring_plays"] = pbp.get("scoring_plays", [])
            game_data["scoring_drives"] = pbp.get("scoring_drives", [])
            # Optional viz overrides (used for non-standard fields, e.g. IFL).
            for k in ("field_length", "field_endzone",
                      "viz_away_alias", "viz_home_alias"):
                if k in pbp:
                    game_data[k] = pbp[k]
        
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
        if re.sub(r"\s*\d{4}$", "", (s.get("name") or "")).strip() in HOF_EXCLUDED_BASES
    }
    # Slugified versions for the league_stats / player_league_seasons keys.
    _hof_excl_slugs = {
        slugify(f"{(s.get('name') or '')}-{s.get('season','')}" if s.get("season") else (s.get("name") or ""))
        for sid, s in sport_map.items()
        if re.sub(r"\s*\d{4}$", "", (s.get("name") or "")).strip() in HOF_EXCLUDED_BASES
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

    # Dual-threat: passing TD + rushing TD in the same game
    _dual_qb_map: dict = defaultdict(lambda: {"pass_td": 0, "rush_td": 0, "games": 0, "game_slugs": []})
    for _cid, _gd in player_game_stats.items():
        _cp = canonical_map.get(_cid)
        if not _cp or not _has_real_name(_cp):
            continue
        for _gk, _sd in _gd.items():
            _ptd = int(_sd.get("passing_tds", 0))
            _rtd = int(_sd.get("rushing_tds", 0))
            if _ptd > 0 and _rtd > 0:
                _entry = _dual_qb_map[_cid]
                _entry["pass_td"] += _ptd
                _entry["rush_td"] += _rtd
                _entry["games"]   += 1
                if len(_entry["game_slugs"]) < 3:
                    _gmeta = player_game_meta_store.get(_gk, {})
                    _entry["game_slugs"].append({
                        "slug":    game_id_slug(_gk),
                        "display": _gmeta.get("display", _gk),
                        "pass_td": _ptd,
                        "rush_td": _rtd,
                    })
    dual_threat_qb = []
    for _cid, _s in _dual_qb_map.items():
        _cp = canonical_map.get(_cid)
        if not _cp:
            continue
        dual_threat_qb.append({
            "canonical_id":   _cid,
            "canonical_name": _cp["canonical_name"],
            "games":          _s["games"],
            "pass_td":        _s["pass_td"],
            "rush_td":        _s["rush_td"],
            "game_slugs":     _s["game_slugs"],
        })
    dual_threat_qb.sort(key=lambda x: x["games"], reverse=True)
    dual_threat_qb = dual_threat_qb[:20]

    # Three or more combined TDs (rushing + receiving + passing) in a single game
    _three_td_map: dict = defaultdict(lambda: {"games": 0, "max_tds": 0, "game_slugs": []})
    for _cid, _gd in player_game_stats.items():
        _cp = canonical_map.get(_cid)
        if not _cp or not _has_real_name(_cp):
            continue
        for _gk, _sd in _gd.items():
            _rtd  = int(_sd.get("rushing_tds",  0))
            _cvtd = int(_sd.get("receiving_tds", 0))
            _ptd  = int(_sd.get("passing_tds",   0))
            _total = _rtd + _cvtd + _ptd
            if _total >= 3:
                _entry = _three_td_map[_cid]
                _entry["games"]  += 1
                _entry["max_tds"] = max(_entry["max_tds"], _total)
                if len(_entry["game_slugs"]) < 3:
                    _gmeta = player_game_meta_store.get(_gk, {})
                    _entry["game_slugs"].append({
                        "slug":    game_id_slug(_gk),
                        "display": _gmeta.get("display", _gk),
                        "total":   _total,
                        "rush_td": _rtd,
                        "recv_td": _cvtd,
                        "pass_td": _ptd,
                    })
    three_td_game = []
    for _cid, _s in _three_td_map.items():
        _cp = canonical_map.get(_cid)
        if not _cp:
            continue
        three_td_game.append({
            "canonical_id":   _cid,
            "canonical_name": _cp["canonical_name"],
            "games":          _s["games"],
            "max_tds":        _s["max_tds"],
            "game_slugs":     _s["game_slugs"],
        })
    three_td_game.sort(key=lambda x: (x["games"], x["max_tds"]), reverse=True)
    three_td_game = three_td_game[:20]

    # Most teammates in pro career — players with most unique teammates (FOOTBALL only)
    # Football leagues: NFL, CFL, XFL, USFL, AAF, IFL, NAL, LFA, FCF, ELF, College Football, X-League
    _football_leagues = {
        'NFL', 'CFL', 'XFL', 'USFL', 'AAF', 'IFL', 'NAL', 'LFA', 'FCF', 'ELF', 
        'College Football', 'X-League', 'AFL', 'UFL'
    }
    _teammates_map: dict = defaultdict(set)
    for _gslug, _players in game_players_seen.items():
        _gmeta = game_meta.get(_gslug, {})
        _league = _gmeta.get("league", "")
        # Only include football leagues
        if _league not in _football_leagues:
            continue
        
        _player_list = list(_players)
        # For each player in the game, add all other players as teammates
        for _p1 in _player_list:
            _cp1 = canonical_map.get(_p1)
            if not _cp1 or not _has_real_name(_cp1):
                continue
            for _p2 in _player_list:
                if _p1 != _p2:
                    _teammates_map[_p1].add(_p2)
    
    most_teammates = []
    for _cid, _teammates in _teammates_map.items():
        _cp = canonical_map.get(_cid)
        if not _cp:
            continue
        most_teammates.append({
            "canonical_id":   _cid,
            "canonical_name": _cp["canonical_name"],
            "teammate_count": len(_teammates),
        })
    most_teammates.sort(key=lambda x: x["teammate_count"], reverse=True)
    most_teammates = most_teammates[:20]

    # ─── All Around Score (football only) ──────────────────────────────────
    # Calculate percentile rankings for each player across their stats
    _football_set = _football_leagues
    
    # Build league-season aggregations for percentile calculation
    _ls_stat_values: dict = defaultdict(lambda: defaultdict(list))
    
    # Collect all values per league-season-stat from all players
    for _cid, _pdata in {
        _c: {"leagues": set(canonical_map[_c].get("leagues", [])), "season_totals": {}}
        for _c in player_game_stats.keys()
    }.items():
        # Reconstruct season_totals from game_log for percentile calculation
        _player_leagues = _pdata.get("leagues", set())
        _is_football = bool(_player_leagues.intersection(_football_set))
        if not _is_football:
            continue
        
        for _gid_str, _stats_dict in player_game_stats.get(_cid, {}).items():
            _meta = player_game_meta_store.get(_gid_str, {})
            _league = _meta.get("league", "")
            _season = _meta.get("season", "")
            if not _league or not _season or _league not in _football_set:
                continue
            _ls_key = f"{_league}-{_season}"
            if _ls_key not in _pdata["season_totals"]:
                _pdata["season_totals"][_ls_key] = defaultdict(float)
            for _stat, _val in _stats_dict.items():
                _pdata["season_totals"][_ls_key][_stat] += _val
    
    # Collect stat values for percentile baseline
    for _cid in player_game_stats.keys():
        _cp = canonical_map.get(_cid)
        if not _cp or not _has_real_name(_cp):
            continue
        _player_leagues = set(_cp.get("leagues", []))
        _is_football = bool(_player_leagues.intersection(_football_set))
        if not _is_football:
            continue
        
        for _gid_str, _stats_dict in player_game_stats.get(_cid, {}).items():
            _meta = player_game_meta_store.get(_gid_str, {})
            _league = _meta.get("league", "")
            _season = _meta.get("season", "")
            if not _league or not _season or _league not in _football_set:
                continue
            _ls_key = f"{_league}-{_season}"
            
            for _stat, _val in _stats_dict.items():
                try:
                    _val_float = float(_val)
                    if _val_float > 0:
                        _ls_stat_values[_ls_key][_stat].append(_val_float)
                except (ValueError, TypeError):
                    pass
    
    # Calculate percentiles for each player
    _all_around_scores: dict = defaultdict(lambda: {"total_percentile": 0, "stat_count": 0})
    
    for _cid in player_game_stats.keys():
        _cp = canonical_map.get(_cid)
        if not _cp or not _has_real_name(_cp):
            continue
        _player_leagues = set(_cp.get("leagues", []))
        _is_football = bool(_player_leagues.intersection(_football_set))
        if not _is_football:
            continue
        
        for _gid_str, _stats_dict in player_game_stats.get(_cid, {}).items():
            _meta = player_game_meta_store.get(_gid_str, {})
            _league = _meta.get("league", "")
            _season = _meta.get("season", "")
            if not _league or not _season or _league not in _football_set:
                continue
            _ls_key = f"{_league}-{_season}"
            
            for _stat, _val in _stats_dict.items():
                try:
                    _val_float = float(_val)
                    if _val_float > 0:
                        _all_vals = _ls_stat_values[_ls_key].get(_stat, [])
                        if _all_vals:
                            # Calculate percentile: (count <= val) / total * 100
                            _count_lte = sum(1 for v in _all_vals if v <= _val_float)
                            _percentile = (_count_lte / len(_all_vals)) * 100
                            _all_around_scores[_cid]["total_percentile"] += _percentile
                            _all_around_scores[_cid]["stat_count"] += 1
                except (ValueError, TypeError):
                    pass
    
    # Calculate average percentile and create ranked list
    all_around = []
    for _cid, _score_data in _all_around_scores.items():
        # Only include players with at least 5 stats (for meaningful "all around" score)
        if _score_data["stat_count"] >= 5:
            _avg_percentile = _score_data["total_percentile"] / _score_data["stat_count"]
            _cp = canonical_map.get(_cid)
            if _cp:
                all_around.append({
                    "canonical_id": _cid,
                    "canonical_name": _cp["canonical_name"],
                    "all_around_score": round(_avg_percentile, 2),
                    "stats_counted": _score_data["stat_count"],
                })
    
    all_around.sort(key=lambda x: x["all_around_score"], reverse=True)
    all_around_top20 = all_around[:20]

    # ─── Position-Specific Scoring (football only) ────────────────────────────
    # Map positions to relevant statistical categories
    _position_stat_map = {
        "QB": {
            "passing_yards", "passing_tds", "passing_attempts", "interceptions",
            "completion_pct", "passing_rushing_yards", "passing_rushing_tds"
        },
        "RB": {
            "rushing_yards", "rushing_tds", "rushing_attempts",
            "receptions", "receiving_yards", "receiving_tds", "targets",
            "fumbles_lost_game", "fumbles_forced", "fumbles_own_rec"
        },
        "WR": {
            "receptions", "receiving_yards", "receiving_tds", "targets",
            "rushing_yards", "rushing_tds", "rushing_attempts"
        },
        "TE": {
            "receptions", "receiving_yards", "receiving_tds", "targets",
            "rushing_yards", "rushing_tds"
        },
        "K": {
            "extra_points", "field_goals", "field_goal_attempts",
            "kickoff_yards", "kickoff_touchbacks"
        },
        "P": {
            "punts", "punt_yards", "punt_avg", "punt_long"
        },
        "Defense": {
            "tackles", "sacks", "interceptions", "passes_defended",
            "fumbles_recovered", "fumbles_forced", "defensive_tds",
            "safeties", "blocked_kicks"
        },
    }
    
    # Calculate position-specific scores for each player
    pos_specific_scores: dict = {}
    
    for _cid in player_game_stats.keys():
        _cp = canonical_map.get(_cid)
        if not _cp or not _has_real_name(_cp):
            continue
        _player_leagues = set(_cp.get("leagues", []))
        _is_football = bool(_player_leagues.intersection(_football_set))
        if not _is_football:
            continue
        
        # Get player's primary position
        _positions = _cp.get("positions", [])
        if not _positions:
            continue
        
        # Find the position with stats in position map, prioritizing first listed position
        _pos = None
        for _p in _positions:
            if _p in _position_stat_map:
                _pos = _p
                break
        
        if not _pos:
            continue  # No relevant position for scoring
        
        _relevant_stats = _position_stat_map[_pos]
        _total_percentile = 0
        _stat_count = 0
        
        # Calculate percentiles for position-relevant stats
        for _gid_str, _stats_dict in player_game_stats.get(_cid, {}).items():
            _meta = player_game_meta_store.get(_gid_str, {})
            _league = _meta.get("league", "")
            _season = _meta.get("season", "")
            if not _league or not _season or _league not in _football_set:
                continue
            _ls_key = f"{_league}-{_season}"
            
            for _stat, _val in _stats_dict.items():
                if _stat not in _relevant_stats:
                    continue  # Skip irrelevant stats for this position
                
                try:
                    _val_float = float(_val)
                    if _val_float > 0:
                        _all_vals = _ls_stat_values[_ls_key].get(_stat, [])
                        if _all_vals:
                            _count_lte = sum(1 for v in _all_vals if v <= _val_float)
                            _percentile = (_count_lte / len(_all_vals)) * 100
                            _total_percentile += _percentile
                            _stat_count += 1
                except (ValueError, TypeError):
                    pass
        
        # Only include if enough relevant stats
        if _stat_count >= 5:
            _avg_percentile = _total_percentile / _stat_count
            pos_specific_scores[_cid] = {
                "score": round(_avg_percentile, 2),
                "position": _pos,
                "stat_count": _stat_count,
            }
    
    # Create ranked position lists and top 20 per position
    pos_specific_by_position = defaultdict(list)
    for _cid, _data in pos_specific_scores.items():
        _cp = canonical_map.get(_cid)
        if _cp:
            pos_specific_by_position[_data["position"]].append({
                "canonical_id": _cid,
                "canonical_name": _cp["canonical_name"],
                "pos_specific_score": _data["score"],
                "position": _data["position"],
            })
    
    # Sort each position by score and add ranks
    _pos_specific_full_map: dict = {}  # {canonical_id: (score, rank, position)}
    for _pos in pos_specific_by_position:
        pos_specific_by_position[_pos].sort(key=lambda x: x["pos_specific_score"], reverse=True)
        for _rank, _entry in enumerate(pos_specific_by_position[_pos], 1):
            _cid = _entry["canonical_id"]
            _pos_specific_full_map[_cid] = (_entry["pos_specific_score"], _rank, _pos)

    # Create position-specific top 20 lists for Hall of Fame
    pos_specific_top20 = {}
    for _pos, _players in pos_specific_by_position.items():
        pos_specific_top20[_pos] = _players[:20]

    funstats = {
        "thursday_game_count": len(_thu_game_keys),
        "thursday_lineup":     thursday_lineup,
        "two_way_td":          two_way_td,
        "dual_threat_qb":      dual_threat_qb,
        "three_td_game":       three_td_game,
        "most_teammates":      most_teammates,
        "all_around":          all_around_top20,
        "position_specific":   pos_specific_top20,
    }
    (SITE_DATA / "hof" / "funstats.json").write_text(
        json.dumps(funstats, indent=2), encoding="utf-8"
    )
    print(f"Written fun stats ({len(_thu_game_keys)} Thursday games, "
          f"{sum(bool(thursday_lineup.get(s)) for s in [x[0] for x in LINEUP_SLOTS])} lineup slots, "
          f"{len(two_way_td)} rush+recv TD, {len(dual_threat_qb)} dual-threat QB, "
          f"{len(three_td_game)} 3-TD games, {len(all_around)} all-around scores, "
          f"{len(pos_specific_scores)} position-specific scores)")

    # Add all_around scores to player JSON files (full list, not just top 20)
    _all_around_full_map = {p["canonical_id"]: (p["all_around_score"], i + 1) 
                            for i, p in enumerate(all_around)}
    
    for _player_file in (SITE_DATA / "players").glob("*.json"):
        try:
            _pdata = json.loads(_player_file.read_text())
            _cid = _pdata.get("canonical_id")
            if _cid and _cid in _all_around_full_map:
                _score, _rank = _all_around_full_map[_cid]
                _pdata["all_around_score"] = _score
                _pdata["all_around_rank"] = _rank
            
            # Add position-specific scores
            if _cid and _cid in _pos_specific_full_map:
                _pos_score, _pos_rank, _position = _pos_specific_full_map[_cid]
                _pdata["pos_specific_score"] = _pos_score
                _pdata["pos_specific_rank"] = _pos_rank
                _pdata["position_badge_label"] = _position  # Just the position, rank shown separately
            
            _player_file.write_text(json.dumps(_pdata, indent=2), encoding="utf-8")
        except (json.JSONDecodeError, IOError):
            pass

    # ─── Sports index ─────────────────────────────────────────────────────
    write_json_xml(SITE_DATA / "sports", {"sports": sports}, root_tag="sports")

    # ─── Coaches index ────────────────────────────────────────────────────
    if coaches_merged:
        # Group coaches by league and role
        coaches_by_league: dict = {}
        for coach in coaches_merged:
            for league in coach.get("leagues", []):
                if league not in coaches_by_league:
                    coaches_by_league[league] = []
                coaches_by_league[league].append(coach)
        
        # Write coaches index
        coaches_index = []
        for coach in coaches_merged:
            coaches_index.append({
                "canonical_id": coach["canonical_id"],
                "name": coach["canonical_name"],
                "roles": coach.get("roles", []),
                "leagues": coach.get("leagues", []),
                "years": coach.get("years", []),
            })
        
        (SITE_DATA / "coaches.json").write_text(
            json.dumps(coaches_index, indent=2), encoding="utf-8"
        )
        
        # Write league-specific coaching staff files
        (SITE_DATA / "coaches").mkdir(parents=True, exist_ok=True)
        for league, coaches in coaches_by_league.items():
            league_slug = slugify(league)
            league_coaches_data = {
                "league": league,
                "coaches": [
                    {
                        "canonical_id": c["canonical_id"],
                        "name": c["canonical_name"],
                        "roles": c.get("roles", []),
                        "years": c.get("years", []),
                        "appearances": c.get("appearances", []),
                    }
                    for c in coaches
                ],
            }
            write_json_xml(
                SITE_DATA / "coaches" / league_slug,
                league_coaches_data,
                root_tag="coaches",
            )
        
        print(f"Written {len(coaches_merged)} coaches across {len(coaches_by_league)} leagues")

    # ─── Search index ─────────────────────────────────────────────────────
    (SITE_DATA / "search_index.json").write_text(
        json.dumps(search_index), encoding="utf-8"
    )
    print(f"Written search index with {len(search_index)} players")

    # ─── This week in history ─────────────────────────────────────────────
    build_this_week(game_index)

    print("Done.")


if __name__ == "__main__":
    main()

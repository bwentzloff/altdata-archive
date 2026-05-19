"""
Study: Football players who suited up in TWO different non-NFL leagues
in the same calendar year.

We deliberately exclude the NFL — moving from the NFL to (or from) an alt
league in the same year is its own well-known phenomenon. The interesting
catch here is the journeyman who pieces together a season across multiple
secondary leagues: e.g. a USFL spring → IFL summer combo, or an XFL spring
followed by a CFL fall.

Inputs:
  docs/data/players/*.json  (canonical player records with season_totals)

For each player, we read keys of the form ``<LEAGUE>-<YEAR>`` from the
player's ``season_totals`` map — this is the source of truth for which
leagues they actually accumulated stats in during which year.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SLUG = "multi-league-same-year"
TITLE = "Football journeymen: two leagues in one year"
SUBTITLE = "Non-NFL players who pieced a season together across multiple pro leagues"
CATEGORY = "Football"
TAGS = ["football", "journeyman", "cross-league", "career"]

# Pro football leagues tracked in the archive. NFL is intentionally excluded;
# 50 YARD is excluded as an internal/accounting bucket.
FOOTBALL_LEAGUES = {
    "CFL", "USFL", "XFL", "UFL", "AAF", "AFL", "IFL", "MLFB",
    "ELF", "EFA", "AF1", "FCF", "NAL", "LFA", "X-LEAGUE",
}

# Used to detect any football appearance (so we can skip non-football players).
ALL_FOOTBALL = FOOTBALL_LEAGUES | {"NFL"}

_KEY_RE = re.compile(r"^([A-Z0-9\- ]+)-(\d{4})$")


def _norm_league(name: str) -> str:
    v = (name or "").upper().strip()
    if v == "50YARD":
        return "50 YARD"
    return v


def _load_sport_map(data_dir: Path) -> dict[int, str]:
    try:
        sports = json.loads((data_dir / "sports.json").read_text()).get("sports", [])
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[int, str] = {}
    for s in sports:
        sid = s.get("id")
        if sid is None:
            continue
        out[sid] = _norm_league(s.get("name") or "")
    return out


def _team_for_league(player: dict, league: str, sport_map: dict[int, str]) -> str:
    """Best-effort team lookup for a given league using appearances."""
    for app in (player.get("appearances") or []):
        sid = app.get("sport_id")
        if sid is None:
            continue
        if sport_map.get(sid) == league:
            t = (app.get("team") or "").strip()
            if t:
                return t
    return ""


def _is_placeholder_name(name: str) -> bool:
    n = (name or "").strip().upper()
    if not n:
        return True
    for suffix in (" QB", " RB", " WR", " TE", " OL", " DL", " LB", " DB", " K", " P"):
        if n.endswith(suffix):
            return True
    return False


def _short_year_range(years: list[int]) -> str:
    if not years:
        return ""
    if len(years) == 1:
        return str(years[0])
    if years == list(range(years[0], years[-1] + 1)):
        return f"{years[0]}-{years[-1]}"
    return ", ".join(str(y) for y in years)


def compute(data_dir: Path) -> dict:
    players_dir = data_dir / "players"
    files = list(players_dir.glob("*.json"))
    sport_map = _load_sport_map(data_dir)

    # All instances: one row per (player, year) where >= 2 non-NFL football leagues.
    instances: list[dict] = []
    # Per-player aggregation: how many multi-league years, which years/leagues.
    per_player: dict[str, dict] = {}

    examined = 0
    football_players = 0
    multi_year_total = 0

    for pf in files:
        try:
            p = json.loads(pf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if p.get("ambiguous"):
            continue
        examined += 1

        sport_names = {_norm_league(s) for s in (p.get("sport_names") or [])}
        if not (sport_names & ALL_FOOTBALL):
            continue
        football_players += 1

        player_id = p.get("canonical_id")
        player_name = p.get("canonical_name") or player_id or "Unknown"
        if not player_id or _is_placeholder_name(player_name):
            continue

        # Pull (league, year) from season_totals keys — most reliable per-year signal.
        season_totals = p.get("season_totals") or {}
        year_to_leagues: dict[int, set[str]] = {}
        for key in season_totals.keys():
            m = _KEY_RE.match(key)
            if not m:
                continue
            lg = _norm_league(m.group(1))
            yr = int(m.group(2))
            if lg not in FOOTBALL_LEAGUES:
                continue
            year_to_leagues.setdefault(yr, set()).add(lg)

        multi_years = sorted(y for y, lgs in year_to_leagues.items() if len(lgs) >= 2)
        if not multi_years:
            continue

        # Positions for display (deduped).
        positions = []
        seen_pos: set[str] = set()
        for pos in (p.get("positions") or []):
            v = (pos or "").upper().strip()
            if v and v not in seen_pos:
                positions.append(v)
                seen_pos.add(v)

        for yr in multi_years:
            leagues = sorted(year_to_leagues[yr])
            league_teams = []
            for lg in leagues:
                team = _team_for_league(p, lg, sport_map)
                league_teams.append(f"{lg} ({team})" if team else lg)
            instances.append({
                "player_id": player_id,
                "player": player_name,
                "positions": ", ".join(positions[:3]),
                "year": yr,
                "league_count": len(leagues),
                "leagues": ", ".join(leagues),
                "leagues_with_teams": " · ".join(league_teams),
            })
            multi_year_total += 1

        per_player[player_id] = {
            "player_id": player_id,
            "player": player_name,
            "positions": ", ".join(positions[:3]),
            "multi_league_years": multi_years,
            "years_count": len(multi_years),
            "years_display": _short_year_range(multi_years),
            "all_leagues": sorted({lg for y in multi_years for lg in year_to_leagues[y]}),
            "max_leagues_in_year": max(len(year_to_leagues[y]) for y in multi_years),
        }

    # ── Sort the per-(player,year) instance table ───────────────────────────
    instances.sort(key=lambda r: (-r["league_count"], -r["year"], r["player"]))

    # ── Per-player leaderboard ──────────────────────────────────────────────
    player_rows = sorted(
        per_player.values(),
        key=lambda r: (-r["years_count"], -r["max_leagues_in_year"], r["player"]),
    )
    player_table_rows = [
        {
            "player": r["player"],
            "positions": r["positions"],
            "years_count": r["years_count"],
            "years": r["years_display"],
            "max_leagues_in_year": r["max_leagues_in_year"],
            "leagues_seen": ", ".join(r["all_leagues"]),
        }
        for r in player_rows
    ]

    # ── Headline stats ──────────────────────────────────────────────────────
    unique_players = len(per_player)
    if instances:
        max_leagues = max(r["league_count"] for r in instances)
        top_year_inst = max(instances, key=lambda r: (r["league_count"], r["year"]))
    else:
        max_leagues = 0
        top_year_inst = None

    if player_rows:
        top_player = player_rows[0]
        top_player_text = (
            f"{top_player['player']} — {top_player['years_count']} year"
            + ("s" if top_player["years_count"] != 1 else "")
        )
        top_player_sub = top_player["years_display"]
    else:
        top_player_text = "None"
        top_player_sub = ""

    headline_stats = [
        {
            "label": "Multi-league player-years",
            "value": f"{multi_year_total:,}",
            "sub": f"{unique_players:,} unique players in {len({r['year'] for r in instances})} different calendar years",
        },
        {
            "label": "Most leagues in a single year",
            "value": str(max_leagues) if max_leagues else "0",
            "sub": (
                f"{top_year_inst['player']} in {top_year_inst['year']} "
                f"({top_year_inst['leagues']})"
            ) if top_year_inst else "",
        },
        {
            "label": "Most multi-league seasons (career)",
            "value": top_player_text,
            "sub": top_player_sub,
        },
    ]

    # ── Charts ──────────────────────────────────────────────────────────────
    # Chart 1: instances per year.
    year_counts: dict[int, int] = {}
    for r in instances:
        year_counts[r["year"]] = year_counts.get(r["year"], 0) + 1
    years_sorted = sorted(year_counts)
    chart_by_year = {
        "id": "chart-multi-league-by-year",
        "type": "bar",
        "title": "Two-league player-years per calendar year",
        "labels": [str(y) for y in years_sorted],
        "datasets": [{
            "label": "Players with 2+ non-NFL leagues",
            "data": [year_counts[y] for y in years_sorted],
        }],
        "note": "Each bar counts distinct players who accumulated stats in two or more non-NFL pro football leagues during that calendar year.",
    }

    # Chart 2: league pair frequency (which combos pop up most).
    pair_counts: dict[tuple[str, str], int] = {}
    for r in instances:
        lgs = r["leagues"].split(", ")
        # Count each unordered pair within the instance.
        for i in range(len(lgs)):
            for j in range(i + 1, len(lgs)):
                key = tuple(sorted([lgs[i], lgs[j]]))
                pair_counts[key] = pair_counts.get(key, 0) + 1
    top_pairs = sorted(pair_counts.items(), key=lambda x: x[1], reverse=True)[:15]
    chart_pairs = {
        "id": "chart-multi-league-pairs",
        "type": "bar",
        "title": "Most common two-league combinations",
        "labels": [f"{a} + {b}" for (a, b), _ in top_pairs],
        "datasets": [{
            "label": "Player-years",
            "data": [c for _, c in top_pairs],
        }],
        "indexAxis": "y",
        "note": "Counts each (player, year) instance once per league pair that appears together that year.",
    }

    # Chart 3: top players by # of multi-league years.
    top_players = player_rows[:12]
    chart_players = {
        "id": "chart-multi-league-top-players",
        "type": "bar",
        "title": "Players with the most multi-league seasons",
        "labels": [r["player"] for r in top_players],
        "datasets": [{
            "label": "Years with 2+ non-NFL leagues",
            "data": [r["years_count"] for r in top_players],
        }],
        "indexAxis": "y",
        "note": "How many separate calendar years each player suited up in two or more non-NFL pro football leagues.",
    }

    # ── Sections + methodology ──────────────────────────────────────────────
    sections = [
        {
            "heading": "What this study highlights",
            "html": (
                "<p>Some football players don't wait for one league's season to end before suiting "
                "up in another. We surface players who recorded stats in <strong>two or more pro "
                "football leagues during the same calendar year</strong>, excluding the NFL.</p>"
                "<p>That filter intentionally keeps the spotlight on journeymen carving out careers "
                "across the CFL, UFL/USFL/XFL, AFL, IFL, ELF, AF1 and other secondary circuits — "
                "rather than NFL veterans dropping down for a single appearance.</p>"
            ),
        },
    ]

    methodology = (
        "<p>Source: <code>docs/data/players/*.json</code>, specifically the <code>season_totals</code> "
        "map. Keys in that map are formatted <code>&lt;LEAGUE&gt;-&lt;YEAR&gt;</code> (e.g. "
        "<code>IFL-2024</code>), which gives a per-year ledger of every league a player put stats up "
        "in.</p>"
        "<p>Eligible leagues: "
        + ", ".join(sorted(FOOTBALL_LEAGUES))
        + ". NFL is excluded by design. Players flagged as ambiguous in the canonicalization step are "
          "dropped, as are obvious roster-placeholder names (e.g. <em>STALLIONS QB</em>).</p>"
        "<p>Team assignments shown next to each league are best-effort — they come from the player's "
        "<code>appearances</code> list and may not be year-specific if a player suited up for multiple "
        "teams in the same league.</p>"
    )

    history_row = {
        "multi_league_player_years": multi_year_total,
        "unique_players": unique_players,
        "max_leagues_in_year": max_leagues,
        "top_player": player_rows[0]["player"] if player_rows else None,
        "top_player_years": player_rows[0]["years_count"] if player_rows else 0,
    }

    return {
        "headline_stats": headline_stats,
        "charts": [chart_by_year, chart_pairs, chart_players],
        "sections": sections,
        "methodology": methodology,
        "table": {
            "columns": [
                {"key": "player", "label": "Player"},
                {"key": "positions", "label": "Position(s)"},
                {"key": "year", "label": "Year", "numeric": True},
                {"key": "league_count", "label": "Leagues", "numeric": True},
                {"key": "leagues_with_teams", "label": "League — team"},
            ],
            "rows": instances,
        },
        "secondary_table": {
            "title": "Career leaderboard — most multi-league seasons",
            "columns": [
                {"key": "player", "label": "Player"},
                {"key": "positions", "label": "Position(s)"},
                {"key": "years_count", "label": "Multi-league years", "numeric": True},
                {"key": "years", "label": "Years"},
                {"key": "max_leagues_in_year", "label": "Max leagues in a year", "numeric": True},
                {"key": "leagues_seen", "label": "Leagues seen"},
            ],
            "rows": player_table_rows,
        },
        "history_row": history_row,
    }

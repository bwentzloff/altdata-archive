"""
Study 3: Team-season teammate environment vs NFL outcomes.

Question:
Do players in denser NFL-bound teammate environments convert to the NFL at
higher rates?
"""

from __future__ import annotations

import json
from pathlib import Path

SLUG = "teammate-density-nfl"
TITLE = "Do NFL-rich locker rooms produce more NFL players?"
SUBTITLE = "Teammate-density signals across alt-football team-seasons"
CATEGORY = "Football"
TAGS = ["football", "nfl", "teammates", "team-season", "network"]

FOOTBALL_LEAGUES = {
    "NFL", "CFL", "USFL", "XFL", "UFL", "AAF", "AFL", "IFL", "MLFB",
    "ELF", "AF1", "FCF", "NAL", "LFA", "X-LEAGUE",
}


def _norm_league(name: str) -> str:
    v = (name or "").upper().strip()
    if v == "50YARD":
        return "50 YARD"
    return v


def _load_sport_map(data_dir: Path) -> dict[int, dict]:
    try:
        sports = json.loads((data_dir / "sports.json").read_text()).get("sports", [])
    except (json.JSONDecodeError, OSError):
        return {}

    out: dict[int, dict] = {}
    for s in sports:
        sid = s.get("id")
        if sid is None:
            continue
        out[sid] = {
            "league": _norm_league(s.get("name") or ""),
            "season": s.get("season"),
        }
    return out


def _short_name(full_name: str) -> str:
    parts = (full_name or "").replace(".", "").split()
    if len(parts) <= 1:
        return full_name or "Unknown"
    return f"{parts[0]} {parts[-1]}"


def _season_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(str(v)[:4])
        except (TypeError, ValueError):
            return None


def compute(data_dir: Path) -> dict:
    players_dir = data_dir / "players"
    sport_map = _load_sport_map(data_dir)

    # Team-season groups keyed by (sport_id, team).
    groups: dict[tuple[int, str], dict] = {}

    # Player-level metadata used downstream.
    players_meta: dict[str, dict] = {}

    for pf in players_dir.glob("*.json"):
        try:
            p = json.loads(pf.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if p.get("ambiguous"):
            continue

        cid = p.get("canonical_id")
        if not cid:
            continue

        name = p.get("canonical_name") or cid
        sport_names = {_norm_league(s) for s in (p.get("sport_names") or [])}
        in_nfl = "NFL" in sport_names

        # Collect NFL seasons (as ints) so we can later distinguish players
        # whose NFL stint came AFTER an alt-league team-season.
        nfl_seasons: set[int] = set()
        for app in (p.get("appearances") or []):
            sid = app.get("sport_id")
            sm = sport_map.get(sid) if sid is not None else None
            if not sm:
                continue
            if sm.get("league") != "NFL":
                continue
            yr = _season_int(sm.get("season"))
            if yr is not None:
                nfl_seasons.add(yr)
        min_nfl_season = min(nfl_seasons) if nfl_seasons else None
        max_nfl_season = max(nfl_seasons) if nfl_seasons else None

        players_meta[cid] = {
            "name": name,
            "in_nfl": in_nfl,
            "nfl_seasons": nfl_seasons,
            "min_nfl_season": min_nfl_season,
            "max_nfl_season": max_nfl_season,
        }

        for app in (p.get("appearances") or []):
            sid = app.get("sport_id")
            team = (app.get("team") or "").strip().upper()
            if sid is None or not team:
                continue

            sm = sport_map.get(sid)
            if not sm:
                continue

            league = sm.get("league")
            season = sm.get("season")
            if league not in FOOTBALL_LEAGUES or league == "NFL":
                continue

            key = (sid, team)
            g = groups.setdefault(key, {
                "sid": sid,
                "team": team,
                "league": league,
                "season": season,
                "players": set(),
            })
            g["players"].add(cid)

    # League baselines:
    #   nfl  = share of roster records belonging to NFL veterans (ever in NFL)
    #   after = share of roster records where the player reached the NFL AFTER
    #           that team-season (the "incubator" signal)
    league_counts: dict[str, dict] = {}
    for g in groups.values():
        lg = g["league"]
        ts_year = _season_int(g.get("season"))
        c = league_counts.setdefault(lg, {"records": 0, "nfl": 0, "after": 0})
        for cid in g["players"]:
            c["records"] += 1
            meta = players_meta.get(cid, {})
            if meta.get("in_nfl"):
                c["nfl"] += 1
            if ts_year is not None:
                max_nfl = meta.get("max_nfl_season")
                if max_nfl is not None and max_nfl > ts_year:
                    c["after"] += 1

    league_baseline_pct = {
        lg: (100.0 * c["nfl"] / c["records"]) if c["records"] else 0.0
        for lg, c in league_counts.items()
    }
    league_baseline_after_pct = {
        lg: (100.0 * c["after"] / c["records"]) if c["records"] else 0.0
        for lg, c in league_counts.items()
    }

    # Player environment exposure accumulated across team-seasons.
    player_env: dict[str, dict] = {}

    team_rows = []
    for g in groups.values():
        roster = sorted(g["players"])
        n = len(roster)
        if n < 2:
            continue

        ts_year = _season_int(g.get("season"))
        nfl_in_group = sum(1 for cid in roster if players_meta.get(cid, {}).get("in_nfl"))
        if ts_year is not None:
            after_in_group = sum(
                1 for cid in roster
                if (players_meta.get(cid, {}).get("max_nfl_season") or -1) > ts_year
            )
        else:
            after_in_group = 0

        conv_pct = 100.0 * nfl_in_group / n if n else 0.0
        after_pct = 100.0 * after_in_group / n if n else 0.0
        base_pct = league_baseline_pct.get(g["league"], 0.0)
        base_after_pct = league_baseline_after_pct.get(g["league"], 0.0)

        season_disp = "?" if g["season"] is None else str(g["season"])
        team_label = f"{g['league']} {season_disp} {g['team']}"

        team_rows.append({
            "team_season": team_label,
            "league": g["league"],
            "season": g["season"],
            "team": g["team"],
            "roster_size": n,
            "nfl_vets": nfl_in_group,
            "nfl_after": after_in_group,
            "veteran_pct": round(conv_pct, 1),
            "after_pct": round(after_pct, 1),
            "league_baseline_pct": round(base_pct, 1),
            "league_baseline_after_pct": round(base_after_pct, 1),
            "lift_pct_points": round(after_pct - base_after_pct, 1),
            "has_season": ts_year is not None,
            "sid": g["sid"],
        })

        for cid in roster:
            in_nfl = bool(players_meta.get(cid, {}).get("in_nfl"))
            teammates = n - 1
            nfl_teammates = nfl_in_group - (1 if in_nfl else 0)
            density = (nfl_teammates / teammates) if teammates > 0 else 0.0

            e = player_env.setdefault(cid, {
                "team_seasons": 0,
                "density_sum": 0.0,
                "teammates": 0,
                "nfl_teammates": 0,
                "leagues": set(),
            })
            e["team_seasons"] += 1
            e["density_sum"] += density
            e["teammates"] += teammates
            e["nfl_teammates"] += nfl_teammates
            e["leagues"].add(g["league"])

    # Player-level rows for decile analysis.
    player_rows = []
    for cid, env in player_env.items():
        in_nfl = bool(players_meta.get(cid, {}).get("in_nfl"))
        avg_density = env["density_sum"] / env["team_seasons"] if env["team_seasons"] else 0.0
        player_rows.append({
            "canonical_id": cid,
            "name": players_meta.get(cid, {}).get("name", cid),
            "has_nfl_history": in_nfl,
            "team_seasons": env["team_seasons"],
            "avg_nfl_teammate_density": avg_density,
            "decile": 1,
            "leagues_count": len(env["leagues"]),
            "total_teammates": env["teammates"],
            "nfl_teammates": env["nfl_teammates"],
        })

    # Rank-based deciles (equal-sized cohorts) are more stable than fixed
    # numeric bins when teammate density distribution is highly skewed.
    if player_rows:
        sorted_idx = sorted(
            range(len(player_rows)),
            key=lambda i: player_rows[i]["avg_nfl_teammate_density"],
        )
        n_rows = len(player_rows)
        for rank, idx in enumerate(sorted_idx):
            dec = min(10, int(rank * 10 / n_rows) + 1)
            player_rows[idx]["decile"] = dec

    # Decile chart
    dec_stats = {d: {"players": 0, "nfl": 0} for d in range(1, 11)}
    for r in player_rows:
        d = r["decile"]
        dec_stats[d]["players"] += 1
        if r["has_nfl_history"]:
            dec_stats[d]["nfl"] += 1

    dec_labels = [f"D{d}" for d in range(1, 11)]
    dec_rates = []
    dec_counts = []
    for d in range(1, 11):
        c = dec_stats[d]["players"]
        n = dec_stats[d]["nfl"]
        dec_counts.append(c)
        dec_rates.append(round((100.0 * n / c) if c else 0.0, 1))

    chart_decile = {
        "id": "chart-density-deciles",
        "type": "bar",
        "title": "NFL conversion by teammate-density group",
        "labels": dec_labels,
        "datasets": [{
            "label": "Has NFL history (%)",
            "data": dec_rates,
        }],
        "value_suffix": "%",
        "note": "D1 is the lowest-density group and D10 is the highest-density group.",
    }

    # Incubator team-seasons table/chart.
    # Only consider team-seasons with a known season year; otherwise we cannot
    # tell whether a player's NFL stint came AFTER this team-season.
    incubators = [r for r in team_rows if r["roster_size"] >= 15 and r["has_season"]]
    incubators.sort(
        key=lambda r: (r["lift_pct_points"], r["nfl_after"], r["after_pct"]),
        reverse=True,
    )
    top_incubators = incubators[:15]

    chart_incubators = {
        "id": "chart-incubators",
        "type": "bar",
        "title": "Top team-seasons by subsequent NFL rate vs league baseline",
        "labels": [r["team_season"] for r in top_incubators],
        "datasets": [
            {
                "label": "Reached NFL after this season (%)",
                "data": [r["after_pct"] for r in top_incubators],
            },
            {
                "label": "League baseline (%)",
                "data": [r["league_baseline_after_pct"] for r in top_incubators],
            },
        ],
        "indexAxis": "y",
        "value_suffix": "%",
        "note": "Only counts players whose first NFL appearance came AFTER this team-season. Minimum roster size 15.",
    }

    # Bridge network among top incubators:
    # Include players who appear in at least 2 top incubators and went on to
    # the NFL after at least one of those team-seasons.
    top_keys = {(r["sid"], r["team"]) for r in top_incubators[:8]}
    key_to_row = {(r["sid"], r["team"]): r for r in top_incubators}
    player_to_top_teams: dict[str, set[tuple[int, str]]] = {}

    for key in top_keys:
        g = groups.get(key)
        if not g:
            continue
        ts_year = _season_int(g.get("season"))
        for cid in g["players"]:
            meta = players_meta.get(cid, {})
            max_nfl = meta.get("max_nfl_season")
            if ts_year is None or max_nfl is None or max_nfl <= ts_year:
                continue
            player_to_top_teams.setdefault(cid, set()).add(key)

    bridge_players = [
        cid for cid, ks in player_to_top_teams.items()
        if len(ks) >= 2
    ]

    network_nodes = []
    network_edges = []

    for key in sorted(top_keys):
        row = key_to_row.get(key)
        if not row:
            continue
        node_id = f"team:{row['sid']}:{row['team']}"
        network_nodes.append({
            "id": node_id,
            "label": row["team_season"],
            "value": max(1, row["nfl_after"]),
            "is_nfl": False,
        })

    # Keep network readable.
    bridge_players = sorted(
        bridge_players,
        key=lambda cid: len(player_to_top_teams.get(cid, set())),
        reverse=True,
    )[:18]

    for cid in bridge_players:
        name = players_meta.get(cid, {}).get("name", cid)
        short = _short_name(name)
        pnode_id = f"player:{cid}"
        network_nodes.append({
            "id": pnode_id,
            "label": short,
            "value": len(player_to_top_teams.get(cid, set())),
            "is_nfl": True,
        })
        for key in sorted(player_to_top_teams.get(cid, set())):
            row = key_to_row.get(key)
            if not row:
                continue
            tnode_id = f"team:{row['sid']}:{row['team']}"
            network_edges.append({
                "source": tnode_id,
                "target": pnode_id,
                "value": 1,
                "directed": False,
            })

    chart_network = {
        "id": "chart-incubator-network",
        "type": "network",
        "title": "Bridge players who reached the NFL after multiple high-yield team-seasons",
        "nodes": network_nodes,
        "edges": network_edges,
        "note": "Team-season nodes connect to players who reached the NFL after this team-season and appear in multiple top incubators.",
    }

    # Build output table rows
    table_rows = []
    for r in top_incubators:
        table_rows.append({
            "team_season": r["team_season"],
            "league": r["league"],
            "roster_size": r["roster_size"],
            "nfl_after": r["nfl_after"],
            "after_pct": r["after_pct"],
            "nfl_vets": r["nfl_vets"],
            "veteran_pct": r["veteran_pct"],
            "league_baseline_after_pct": r["league_baseline_after_pct"],
            "lift_pct_points": r["lift_pct_points"],
        })

    # Headline stats
    if top_incubators:
        best = top_incubators[0]
    else:
        best = None

    total_player_records = len(player_rows)
    overall_nfl = sum(1 for r in player_rows if r["has_nfl_history"])
    overall_pct = round((100.0 * overall_nfl / total_player_records) if total_player_records else 0.0, 1)

    d10 = dec_stats[10]
    d1 = dec_stats[1]
    d10_pct = round((100.0 * d10["nfl"] / d10["players"]) if d10["players"] else 0.0, 1)
    d1_pct = round((100.0 * d1["nfl"] / d1["players"]) if d1["players"] else 0.0, 1)

    headline_stats = [
        {
            "label": "Top incubator team-season",
            "value": best["team_season"] if best else "None yet",
            "sub": (
                f"{best['after_pct']}% of the roster reached the NFL afterward, vs {best['league_baseline_after_pct']}% typical for that league"
                if best else "No team-seasons met minimum sample filter"
            ),
        },
        {
            "label": "Highest vs lowest density group",
            "value": f"{d10_pct}% vs {d1_pct}%",
            "sub": "NFL veteran rates for players in the most NFL-rich teammate environments vs the least",
        },
        {
            "label": "Overall NFL veteran rate",
            "value": f"{overall_pct}%",
            "sub": f"Across {total_player_records:,} alt-football team-season player records",
        },
    ]

    sections = [
        {
            "heading": "What this study asks",
            "html": (
                "<p>Instead of only comparing leagues, this study looks at the team around each player. "
                "For each alt-football team-season, we measure how many teammates show up in NFL records, "
                "then check whether players from those NFL-rich teams are also more likely to appear in NFL records.</p>"
            ),
        },
        {
            "heading": "How to read the groups",
            "html": (
                "<p>Each player gets an average teammate-density score based on the teams they played on. "
                "We sort players into 10 groups from D1 (lowest) to D10 (highest), then compare NFL reach rates "
                "across those groups.</p>"
            ),
        },
        {
            "heading": "Team-season incubators",
            "html": (
                "<p>The table ranks team-seasons by how many roster players went on to reach the NFL <em>after</em> that season, "
                "compared with the typical rate for the same league. That isolates teams that actually launched players into the NFL, "
                "rather than teams that simply collected NFL veterans.</p>"
                "<p>The right-most columns show the share of the roster who are NFL veterans (ever in the NFL, before or after) for context.</p>"
            ),
        },
    ]

    methodology = (
        "<p>Data source: player files under <code>docs/data/players/</code> and league-season metadata in "
        "<code>docs/data/sports.json</code>.</p>"
        "<p>Team-season identity is inferred from roster appearances keyed by <code>(sport_id, team)</code>. "
        "Only non-NFL football leagues are included in environment construction. \"NFL veteran\" means the player has any "
        "NFL appearance on record (before or after the team-season). \"Reached NFL after\" means the player has at least one "
        "NFL appearance whose season is later than the team-season in question; team-seasons without a known year are excluded "
        "from the incubator ranking.</p>"
        "<p>This is pattern-tracking, not proof of cause and effect. Team context and NFL outcomes move together here, "
        "but that does not prove one directly causes the other.</p>"
    )

    history_row = {
        "player_records": total_player_records,
        "overall_nfl_veteran_pct": overall_pct,
        "d10_pct": d10_pct,
        "d1_pct": d1_pct,
        "top_incubator": best["team_season"] if best else None,
        "top_incubator_after_pct": best["after_pct"] if best else 0.0,
    }

    return {
        "headline_stats": headline_stats,
        "charts": [chart_decile, chart_incubators, chart_network],
        "sections": sections,
        "methodology": methodology,
        "table": {
            "columns": [
                {"key": "team_season", "label": "Team-season"},
                {"key": "league", "label": "League"},
                {"key": "roster_size", "label": "Roster size", "numeric": True},
                {"key": "nfl_after", "label": "Reached NFL after", "numeric": True},
                {"key": "after_pct", "label": "Reached NFL after %", "numeric": True, "suffix": "%"},
                {"key": "league_baseline_after_pct", "label": "League typical %", "numeric": True, "suffix": "%"},
                {"key": "lift_pct_points", "label": "Difference (points)", "numeric": True},
                {"key": "nfl_vets", "label": "NFL veterans on roster", "numeric": True},
                {"key": "veteran_pct", "label": "NFL veteran %", "numeric": True, "suffix": "%"},
            ],
            "rows": table_rows,
        },
        "history_row": history_row,
    }

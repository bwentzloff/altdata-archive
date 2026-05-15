"""
Study 3: Team-season teammate environment vs NFL outcomes.

Question:
Do players in denser NFL-bound teammate environments convert to the NFL at
higher rates?
"""

from __future__ import annotations

import json
import math
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


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    w = pos - lo
    return float(sorted_vals[lo] * (1 - w) + sorted_vals[hi] * w)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


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

    # Reference NFL observation window used for right-censor adjustments.
    nfl_seasons_in_data = {
        _season_int(s.get("season"))
        for s in sport_map.values()
        if s.get("league") == "NFL"
    }
    nfl_seasons_in_data.discard(None)
    max_nfl_season_in_data = max(nfl_seasons_in_data) if nfl_seasons_in_data else None

    # Team-season scatter: do NFL-veteran-heavy rosters actually translate to
    # post-season NFL conversion?
    eligible_team_rows = [
        r for r in team_rows
        if r["roster_size"] >= 15 and r["has_season"]
    ]
    scatter_points = [
        {
            "x": r["veteran_pct"],
            "y": r["after_pct"],
            "team_season": r["team_season"],
            "league": r["league"],
            "roster_size": r["roster_size"],
        }
        for r in eligible_team_rows
    ]
    chart_scatter = {
        "id": "chart-team-scatter",
        "type": "scatter",
        "title": "Team-season signal: NFL veteran share vs players who reached the NFL afterward",
        "datasets": [{
            "label": "Team-seasons",
            "data": scatter_points,
        }],
        "x_label": "NFL veterans on roster (%)",
        "y_label": "Reached NFL after this season (%)",
        "value_suffix": "%",
        "note": "Each dot is one team-season (minimum roster size 15, known year). X-axis is veteran share on roster; Y-axis is share that reached the NFL after that season.",
    }

    # Year-level trend: does the after-rate drift by calendar season?
    season_counts: dict[int, dict[str, int]] = {}
    for g in groups.values():
        ts_year = _season_int(g.get("season"))
        if ts_year is None:
            continue
        if max_nfl_season_in_data is None or ts_year >= max_nfl_season_in_data:
            continue
        roster = g["players"]
        if len(roster) < 2:
            continue
        c = season_counts.setdefault(ts_year, {"records": 0, "nfl": 0, "after": 0})
        for cid in roster:
            c["records"] += 1
            meta = players_meta.get(cid, {})
            if meta.get("in_nfl"):
                c["nfl"] += 1
            max_nfl = meta.get("max_nfl_season")
            if max_nfl is not None and max_nfl > ts_year:
                c["after"] += 1

    season_rows = [
        {
            "year": yr,
            "records": c["records"],
            "veteran_pct": round(100.0 * c["nfl"] / c["records"], 1) if c["records"] else 0.0,
            "after_pct": round(100.0 * c["after"] / c["records"], 1) if c["records"] else 0.0,
        }
        for yr, c in season_counts.items()
        if c["records"] >= 60
    ]
    season_rows.sort(key=lambda r: r["year"])
    chart_season_trend = {
        "id": "chart-season-trend",
        "type": "line",
        "title": "League-wide trend by season cohort",
        "labels": [str(r["year"]) for r in season_rows],
        "datasets": [
            {
                "label": "NFL veterans on roster (%)",
                "data": [r["veteran_pct"] for r in season_rows],
            },
            {
                "label": "Reached NFL after this season (%)",
                "data": [r["after_pct"] for r in season_rows],
            },
        ],
        "value_suffix": "%",
        "note": "Each season pools all eligible non-NFL football rosters that year (minimum 60 player-records). This shows whether context and outcomes move together over time.",
    }

    # League-level distribution.
    # Aggregate every team-season of each league into a single number per
    # league: what share of all roster records belong to NFL veterans, and
    # what share went on to reach the NFL after that team-season.
    # Only include team-seasons where there is at least one NFL season AFTER
    # them in the data — otherwise the after-rate is structurally zero and
    # would unfairly drag down recent leagues like the UFL.
    league_dist_counts: dict[str, dict] = {}
    for g in groups.values():
        lg = g["league"]
        ts_year = _season_int(g.get("season"))
        if ts_year is None:
            continue
        if max_nfl_season_in_data is None or ts_year >= max_nfl_season_in_data:
            continue  # no NFL seasons after this team-season exist in the data
        c = league_dist_counts.setdefault(lg, {"records": 0, "nfl": 0, "after": 0})
        for cid in g["players"]:
            c["records"] += 1
            meta = players_meta.get(cid, {})
            if meta.get("in_nfl"):
                c["nfl"] += 1
            max_nfl = meta.get("max_nfl_season")
            if max_nfl is not None and max_nfl > ts_year:
                c["after"] += 1

    league_rows = []
    for lg, c in league_dist_counts.items():
        if c["records"] < 30:
            continue
        league_rows.append({
            "league": lg,
            "records": c["records"],
            "veteran_pct": round(100.0 * c["nfl"] / c["records"], 1),
            "after_pct": round(100.0 * c["after"] / c["records"], 1),
        })
    league_rows.sort(key=lambda r: r["veteran_pct"], reverse=True)

    chart_leagues = {
        "id": "chart-league-distribution",
        "type": "bar",
        "title": "NFL footprint by league (team-seasons with a chance to reach the NFL afterward)",
        "labels": [r["league"] for r in league_rows],
        "datasets": [
            {
                "label": "NFL veterans on roster (%)",
                "data": [r["veteran_pct"] for r in league_rows],
            },
            {
                "label": "Reached NFL after this team-season (%)",
                "data": [r["after_pct"] for r in league_rows],
            },
        ],
        "indexAxis": "y",
        "value_suffix": "%",
        "note": (
            f"Each league pools every team-season strictly before the latest NFL season in the data "
            f"({max_nfl_season_in_data}). Team-seasons from {max_nfl_season_in_data} or later are excluded "
            "because no later NFL season exists yet for players to reach. Minimum 30 roster records per league."
        ),
    }

    # Box-and-whisker by league for distribution (not just averages).
    league_after_values: dict[str, list[float]] = {}
    for r in eligible_team_rows:
        ts_year = _season_int(r.get("season"))
        if ts_year is None:
            continue
        if max_nfl_season_in_data is None or ts_year >= max_nfl_season_in_data:
            continue
        league_after_values.setdefault(r["league"], []).append(float(r["after_pct"]))

    box_rows = []
    for lg, vals in league_after_values.items():
        if len(vals) < 6:
            continue
        svals = sorted(vals)
        q1 = _quantile(svals, 0.25)
        med = _quantile(svals, 0.50)
        q3 = _quantile(svals, 0.75)
        iqr = q3 - q1
        lo_fence = q1 - 1.5 * iqr
        hi_fence = q3 + 1.5 * iqr
        inlier = [v for v in svals if lo_fence <= v <= hi_fence]
        outlier = [v for v in svals if v < lo_fence or v > hi_fence]
        box_rows.append({
            "label": lg,
            "n": len(svals),
            "min": round(min(inlier) if inlier else min(svals), 1),
            "q1": round(q1, 1),
            "median": round(med, 1),
            "q3": round(q3, 1),
            "max": round(max(inlier) if inlier else max(svals), 1),
            "outliers": [round(v, 1) for v in outlier],
        })

    box_rows.sort(key=lambda r: r["median"], reverse=True)
    chart_league_box = {
        "id": "chart-league-box",
        "type": "boxplot",
        "title": "League spread in team-season NFL-after rates (box-and-whisker)",
        "boxes": box_rows,
        "value_suffix": "%",
        "note": "Box = interquartile range (Q1 to Q3), center line = median, whiskers = non-outlier range, dots = outliers. Includes leagues with at least six eligible team-seasons.",
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

    scatter_x = [p["x"] for p in scatter_points]
    scatter_y = [p["y"] for p in scatter_points]
    team_corr = _pearson(scatter_x, scatter_y)
    team_corr_disp = f"{team_corr:.2f}" if team_corr is not None else "n/a"

    if box_rows:
        league_dispersion_leader = box_rows[0]
    else:
        league_dispersion_leader = None

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
        {
            "label": "Team-season association",
            "value": f"r = {team_corr_disp}",
            "sub": "Correlation between roster NFL-veteran share and post-season NFL conversion across team-seasons",
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
                f"<p>In this snapshot, the gap between the endpoints is large: D10 sits at <strong>{d10_pct}%</strong> "
                f"while D1 is <strong>{d1_pct}%</strong>. That spread is descriptive rather than causal, but it is too "
                "large to ignore when ranking environments.</p>"
            ),
        },
        {
            "heading": "What the new NFL season depth adds",
            "html": (
                "<p>This version uses expanded NFL season coverage in player records, which sharpens the "
                "<em>reached NFL after</em> signal. Earlier snapshots often collapsed this into a coarse NFL/non-NFL "
                "flag. With season-level dating, we can now ask whether a player reached the league <em>after</em> a given "
                "alt-league stop rather than merely whether they ever appeared there.</p>"
                "<p>That change matters most for timing: newer alt seasons are right-censored until later NFL years "
                "arrive in the data. For that reason, the league-comparison charts only include alt team-seasons that "
                "still have at least one later NFL season available for observation.</p>"
            ),
        },
        {
            "heading": "Team-season incubators",
            "html": (
                "<p>The table ranks team-seasons by how many roster players went on to reach the NFL <em>after</em> that season, "
                "compared with the typical rate for the same league. That isolates teams that actually launched players into the NFL, "
                "rather than teams that simply collected NFL veterans.</p>"
                "<p>The right-most columns show the share of the roster who are NFL veterans (ever in the NFL, before or after) for context. "
                "Read this jointly with the scatter and box plots: one chart captures central tendency, the others show variance and outliers.</p>"
            ),
        },
        {
            "heading": "Variance across leagues",
            "html": (
                "<p>League averages can hide structure. Box-and-whisker views show whether a league is consistently strong or just carrying a "
                "small number of unusually productive team-seasons. In practical terms, evaluators care about both: median environment and tail upside.</p>"
                + (
                    f"<p>In the current sample, <strong>{league_dispersion_leader['label']}</strong> posts the highest median team-season "
                    f"after-rate among leagues with enough observations (median {league_dispersion_leader['median']}%, n={league_dispersion_leader['n']}).</p>"
                    if league_dispersion_leader else ""
                )
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
        "<p>League-wide and season-cohort comparisons are right-censor adjusted: alt team-seasons are only included if at least "
        "one later NFL season exists in the source data, so recent cohorts are not penalized for lack of elapsed time.</p>"
        "<p>Chart notes: scatter plots show one point per team-season; box plots summarize distributions at league level; "
        "line charts show pooled season cohorts and are weighted by roster records (not by number of teams).</p>"
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
        "charts": [chart_decile, chart_season_trend, chart_scatter, chart_incubators, chart_league_box, chart_leagues],
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

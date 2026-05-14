"""
Study 2: Cross-league QB + receiver chemistry and OL-linked units.

Important caveat:
We do not have direct passer->target linkage in every league dataset. This
study infers QB/receiver combos from shared team-season membership and combines
that with each player's season production in the same league-season.
"""

from __future__ import annotations

import json
from pathlib import Path

SLUG = "qb-receiver-nomads"
TITLE = "QB-receiver chemistry that survives league changes"
SUBTITLE = "Cross-team-season duos and OL-linked continuity"
CATEGORY = "Football"
TAGS = ["football", "qb", "receiving", "chemistry", "cross-league"]

FOOTBALL_LEAGUES = {
    "NFL", "CFL", "USFL", "XFL", "UFL", "AAF", "AFL", "IFL", "MLFB",
    "ELF", "AF1", "FCF", "NAL", "LFA", "X-LEAGUE",
}

QB_POS = {"QB"}
RECEIVER_POS = {"WR", "RB", "TE", "FB", "HB"}
OL_POS = {"OL", "OT", "OG", "C", "G", "T", "LT", "RT"}

PASS_YARDS_KEYS = ("passing_yards", "pass_yds", "pass_yards")
PASS_TD_KEYS = ("passing_tds", "pass_td")
INT_KEYS = ("interceptions_lost", "pass_int", "interceptions")

REC_YARDS_KEYS = ("receiving_yards", "receiving_yds", "rec_yds")
REC_TD_KEYS = ("receiving_tds", "receiving_td", "rec_td")
TARGET_KEYS = ("targets",)


def _norm_league(name: str) -> str:
    v = (name or "").upper().strip()
    if v == "50YARD":
        return "50 YARD"
    return v


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _sum_keys(stats: dict, keys: tuple[str, ...]) -> float:
    return sum(_safe_float(stats.get(k)) for k in keys)


def _season_stats_for_appearance(season_totals: dict, league: str, season) -> dict:
    if season is not None:
        key = f"{league}-{season}"
        val = season_totals.get(key)
        if isinstance(val, dict):
            return val
    # Fallback: combine all same-league season buckets.
    out = {}
    prefix = f"{league}-"
    for k, v in (season_totals or {}).items():
        if not (isinstance(k, str) and k.startswith(prefix) and isinstance(v, dict)):
            continue
        for sk, sv in v.items():
            out[sk] = out.get(sk, 0.0) + _safe_float(sv)
    return out


def _player_positions(player: dict) -> set[str]:
    pos = {(p or "").upper().strip() for p in (player.get("positions") or []) if p}
    for app in (player.get("appearances") or []):
        ap = (app.get("position") or "").upper().strip()
        if ap:
            pos.add(ap)
    return pos


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


def _is_placeholder_name(name: str) -> bool:
    n = (name or "").strip().upper()
    if not n:
        return True
    # Synthetic roster placeholders like "STALLIONS QB" dominate otherwise.
    if n.endswith(" QB") or n.endswith(" RB") or n.endswith(" WR") or n.endswith(" TE"):
        return True
    return False


def compute(data_dir: Path) -> dict:
    players_dir = data_dir / "players"
    files = list(players_dir.glob("*.json"))
    sport_map = _load_sport_map(data_dir)

    groups: dict[tuple, dict] = {}

    for pf in files:
        try:
            p = json.loads(pf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if p.get("ambiguous"):
            continue

        sport_names = {_norm_league(s) for s in (p.get("sport_names") or [])}
        if not (sport_names & FOOTBALL_LEAGUES):
            continue

        positions = _player_positions(p)
        is_qb = bool(positions & QB_POS)
        is_receiver = bool(positions & RECEIVER_POS)
        is_ol = bool(positions & OL_POS)
        if not (is_qb or is_receiver or is_ol):
            continue

        player_id = p.get("canonical_id")
        player_name = p.get("canonical_name") or player_id or "Unknown"
        if not player_id:
            continue
        if _is_placeholder_name(player_name):
            continue

        season_totals = p.get("season_totals") or {}
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
            if league not in FOOTBALL_LEAGUES:
                continue

            stats = _season_stats_for_appearance(season_totals, league, season)
            pass_yards = _sum_keys(stats, PASS_YARDS_KEYS)
            pass_tds = _sum_keys(stats, PASS_TD_KEYS)
            interceptions = _sum_keys(stats, INT_KEYS)
            rec_yards = _sum_keys(stats, REC_YARDS_KEYS)
            rec_tds = _sum_keys(stats, REC_TD_KEYS)
            targets = _sum_keys(stats, TARGET_KEYS)

            # Heuristic production scores used for ranking, not causal claims.
            qb_score = max(0.0, pass_yards + 40.0 * pass_tds - 45.0 * interceptions)
            rec_score = max(0.0, rec_yards + 60.0 * rec_tds + 2.0 * targets)

            gk = (sid, team)
            g = groups.setdefault(gk, {
                "sid": sid,
                "team": team,
                "league": league,
                "season": season,
                "qbs": [],
                "receivers": [],
                "ols": [],
                "seen_qb": set(),
                "seen_receiver": set(),
                "seen_ol": set(),
            })

            if is_qb and player_id not in g["seen_qb"]:
                g["qbs"].append({
                    "id": player_id,
                    "name": player_name,
                    "pass_yards": pass_yards,
                    "pass_tds": pass_tds,
                    "interceptions": interceptions,
                    "qb_score": qb_score,
                })
                g["seen_qb"].add(player_id)

            if is_receiver and player_id not in g["seen_receiver"]:
                g["receivers"].append({
                    "id": player_id,
                    "name": player_name,
                    "rec_yards": rec_yards,
                    "rec_tds": rec_tds,
                    "targets": targets,
                    "rec_score": rec_score,
                })
                g["seen_receiver"].add(player_id)

            if is_ol and player_id not in g["seen_ol"]:
                g["ols"].append({
                    "id": player_id,
                    "name": player_name,
                })
                g["seen_ol"].add(player_id)

    pair_agg: dict[tuple[str, str], dict] = {}

    for g in groups.values():
        if not g["qbs"] or not g["receivers"]:
            continue
        league = g["league"]
        season = g["season"]
        team = g["team"]
        season_disp = "?" if season is None else str(season)
        ts_label = f"{league} {season_disp} {team}"

        for qb in g["qbs"]:
            for rec in g["receivers"]:
                if qb["id"] == rec["id"]:
                    continue
                if qb["qb_score"] <= 0 and rec["rec_score"] <= 0:
                    continue

                combo_score = qb["qb_score"] + rec["rec_score"]
                key = (qb["id"], rec["id"])
                pa = pair_agg.setdefault(key, {
                    "qb_id": qb["id"],
                    "qb_name": qb["name"],
                    "receiver_id": rec["id"],
                    "receiver_name": rec["name"],
                    "instances": [],
                    "leagues": set(),
                    "teams": set(),
                    "total_score": 0.0,
                    "total_pass_yards": 0.0,
                    "total_rec_yards": 0.0,
                    "ol": {},
                })

                inst = {
                    "league": league,
                    "season": season,
                    "team": team,
                    "label": ts_label,
                    "score": combo_score,
                    "pass_yards": qb["pass_yards"],
                    "pass_tds": qb["pass_tds"],
                    "interceptions": qb["interceptions"],
                    "rec_yards": rec["rec_yards"],
                    "rec_tds": rec["rec_tds"],
                    "targets": rec["targets"],
                }
                pa["instances"].append(inst)
                pa["leagues"].add(league)
                pa["teams"].add((league, season, team))
                pa["total_score"] += combo_score
                pa["total_pass_yards"] += qb["pass_yards"]
                pa["total_rec_yards"] += rec["rec_yards"]

                for ol in g["ols"]:
                    o = pa["ol"].setdefault(ol["id"], {
                        "name": ol["name"],
                        "count": 0,
                        "score_sum": 0.0,
                        "leagues": set(),
                    })
                    o["count"] += 1
                    o["score_sum"] += combo_score
                    o["leagues"].add(league)

    candidates = []
    for pa in pair_agg.values():
        if len(pa["instances"]) < 2:
            continue

        inst_sorted = sorted(pa["instances"], key=lambda x: x["score"], reverse=True)
        best_inst = inst_sorted[0]
        avg_score = pa["total_score"] / len(pa["instances"])

        best_ol_name = ""
        best_ol_count = 0
        best_ol_leagues = 0
        if pa["ol"]:
            ol_items = sorted(
                pa["ol"].values(),
                key=lambda x: (x["count"], x["score_sum"]),
                reverse=True,
            )
            top_ol = ol_items[0]
            best_ol_name = top_ol["name"]
            best_ol_count = top_ol["count"]
            best_ol_leagues = len(top_ol["leagues"])

        candidates.append({
            "qb_id": pa["qb_id"],
            "qb_name": pa["qb_name"],
            "receiver_id": pa["receiver_id"],
            "receiver_name": pa["receiver_name"],
            "duo": f"{_short_name(pa['qb_name'])} -> {_short_name(pa['receiver_name'])}",
            "team_seasons": len(pa["instances"]),
            "leagues_count": len(pa["leagues"]),
            "cross_league": len(pa["leagues"]) >= 2,
            "leagues": sorted(pa["leagues"]),
            "leagues_str": ", ".join(sorted(pa["leagues"])),
            "total_score": round(pa["total_score"], 1),
            "avg_score": round(avg_score, 1),
            "total_pass_yards": int(round(pa["total_pass_yards"])),
            "total_rec_yards": int(round(pa["total_rec_yards"])),
            "best_team_season": best_inst["label"],
            "best_team_season_score": round(best_inst["score"], 1),
            "best_ol": best_ol_name,
            "best_ol_shared_team_seasons": best_ol_count,
            "best_ol_leagues": best_ol_leagues,
            "instances": pa["instances"],
        })

    cross_league = [r for r in candidates if r["cross_league"]]
    leaderboard = sorted(
        cross_league if cross_league else candidates,
        key=lambda x: (x["leagues_count"], x["total_score"], x["team_seasons"]),
        reverse=True,
    )

    # Build rows for output table.
    rows = []
    for r in leaderboard:
        unit_context = ""
        if r["best_ol"]:
            unit_context = (
                f"{_short_name(r['qb_name'])} + {_short_name(r['receiver_name'])} + "
                f"{_short_name(r['best_ol'])} shared {r['best_ol_shared_team_seasons']} team-seasons"
            )
            if r["best_ol_leagues"] > 1:
                unit_context += f" across {r['best_ol_leagues']} leagues"

        rows.append({
            "duo": r["duo"],
            "qb": r["qb_name"],
            "receiver": r["receiver_name"],
            "team_seasons": r["team_seasons"],
            "leagues_count": r["leagues_count"],
            "leagues": r["leagues_str"],
            "total_score": r["total_score"],
            "avg_score": r["avg_score"],
            "pass_yards": r["total_pass_yards"],
            "rec_yards": r["total_rec_yards"],
            "best_team_season": r["best_team_season"],
            "best_ol": r["best_ol"] or "",
            "unit_context": unit_context,
        })

    if leaderboard:
        top = leaderboard[0]
        most_team_seasons = max(leaderboard, key=lambda x: x["team_seasons"])
        oddest = None
        ol_rankable = [r for r in leaderboard if r["best_ol"]]
        if ol_rankable:
            oddest = max(
                ol_rankable,
                key=lambda x: (
                    x["best_ol_shared_team_seasons"],
                    x["best_ol_leagues"],
                    x["total_score"],
                ),
            )
        oddest_text = "No stable OL-linked trio surfaced in repeat pairings."
        if oddest:
            oddest_text = (
                f"{_short_name(oddest['qb_name'])} + {_short_name(oddest['receiver_name'])} + "
                f"{_short_name(oddest['best_ol'])} ({oddest['best_ol_shared_team_seasons']} shared team-seasons)"
            )

        headline_stats = [
            {
                "label": "Top cross-league QB-receiver duo",
                "value": f"{top['leagues_count']} leagues",
                "sub": f"{top['duo']} across {top['team_seasons']} team-seasons",
            },
            {
                "label": "Most durable duo",
                "value": f"{most_team_seasons['team_seasons']} team-seasons",
                "sub": f"{most_team_seasons['duo']}",
            },
            {
                "label": "Top OL-linked unit",
                "value": oddest_text,
                "sub": f"{len(cross_league)} cross-league duos found" if cross_league else "No multi-league repeats; showing repeat team-season duos",
            },
        ]
    else:
        headline_stats = [
            {"label": "Top cross-league QB-receiver duo", "value": "None yet", "sub": "No repeated team-season pairings in current data"},
            {"label": "Most durable duo", "value": "0", "sub": "Need at least two shared team-seasons"},
            {"label": "Top OL-linked unit", "value": "None", "sub": "No repeat pairings to score"},
        ]

    # Chart 1: total chemistry score leaderboard.
    top_score = leaderboard[:12]
    chart_score = {
        "id": "chart-qb-rec-score",
        "type": "bar",
        "title": "Best recurring QB-receiver combos (team-season chemistry score)",
        "labels": [r["duo"] for r in top_score],
        "datasets": [{
            "label": "Chemistry score",
            "data": [r["total_score"] for r in top_score],
        }],
        "indexAxis": "y",
        "note": "Score sums inferred QB and receiver season production while they shared a team-season.",
    }

    # Chart 2: league footprint (stacked by league across team-seasons).
    top_footprint = leaderboard[:8]
    leagues_used = sorted({i["league"] for r in top_footprint for i in r["instances"]})
    chart_footprint = {
        "id": "chart-qb-rec-footprint",
        "type": "stacked-bar",
        "title": "Where each recurring duo shows up (team-seasons by league)",
        "labels": [r["duo"] for r in top_footprint],
        "datasets": [
            {
                "label": lg,
                "data": [sum(1 for i in r["instances"] if i["league"] == lg) for r in top_footprint],
            }
            for lg in leagues_used
        ],
        "indexAxis": "y",
        "note": "Each stack segment is a shared team-season in that league.",
    }

    # Chart 3: unit network (QB -> receiver plus OL anchors).
    network_rows = leaderboard[:10]
    node_map: dict[str, dict] = {}
    edge_map: dict[tuple[str, str, bool], float] = {}

    def add_node(node_id: str, label: str, value: float):
        cur = node_map.get(node_id)
        if cur is None:
            node_map[node_id] = {"id": node_id, "label": label, "value": max(1.0, value), "is_nfl": False}
        else:
            cur["value"] += value

    def add_edge(src: str, dst: str, val: float, directed: bool):
        k = (src, dst, directed)
        edge_map[k] = edge_map.get(k, 0.0) + val

    for r in network_rows:
        qid = f"qb:{r['qb_id']}"
        rid = f"rec:{r['receiver_id']}"
        add_node(qid, f"QB {_short_name(r['qb_name'])}", r["team_seasons"])
        add_node(rid, f"REC {_short_name(r['receiver_name'])}", r["team_seasons"])
        add_edge(qid, rid, max(1.0, r["team_seasons"]), True)

        if r["best_ol"]:
            olid = f"ol:{r['best_ol']}"
            add_node(olid, f"OL {_short_name(r['best_ol'])}", r["best_ol_shared_team_seasons"])
            link_v = max(1.0, float(r["best_ol_shared_team_seasons"]))
            add_edge(qid, olid, link_v, False)
            add_edge(rid, olid, link_v, False)

    chart_network = {
        "id": "chart-qb-rec-network",
        "type": "network",
        "title": "Nomad chemistry graph: QB, receiver, and OL anchors",
        "nodes": list(node_map.values()),
        "edges": [
            {"source": s, "target": t, "value": round(v, 2), "directed": d}
            for (s, t, d), v in sorted(edge_map.items(), key=lambda x: x[1])
        ],
        "note": "Arrows are recurring QB -> receiver duos. Dashed links connect a duo to its most common shared OL teammate.",
    }

    sections = [
        {
            "heading": "What this study is trying to catch",
            "html": (
                "<p>We are looking for QB-to-receiver chemistry that survives context changes. "
                "Instead of a single team snapshot, we search for duos that reappear across multiple "
                "team-seasons, especially when they span multiple leagues.</p>"
            ),
        },
        {
            "heading": "How we define a combo",
            "html": (
                "<p>For each team-season, we pair every QB with every WR/RB/TE/FB/HB teammate. "
                "We then score that team-season pairing using the QB's passing production and the "
                "receiver's receiving production from the same league-season bucket.</p>"
                "<p>When the same QB-receiver pair shows up again in another team-season, we treat it "
                "as recurring chemistry and add those scores together.</p>"
            ),
        },
        {
            "heading": "OL-linked units",
            "html": (
                "<p>For each recurring QB-receiver duo, we also look for offensive linemen who repeatedly "
                "show up in those same team-seasons. This surfaces additional unit-level links: "
                "not just who caught passes, but who may have protected that connection.</p>"
            ),
        },
    ]

    methodology = (
        "<p>Data source: player JSON files in <code>docs/data/players/</code> plus season metadata from "
        "<code>docs/data/sports.json</code>.</p>"
        "<p>A team-season is identified by <code>(sport_id, team)</code> roster membership. Because many "
        "feeds do not include direct passer-target links, this study uses shared team-season co-membership "
        "as a proxy for QB-receiver connection.</p>"
        "<p>Chemistry score per team-season: <code>QB score + Receiver score</code>, where "
        "<code>QB score = passing_yards + 40*passing_tds - 45*interceptions</code> and "
        "<code>Receiver score = receiving_yards + 60*receiving_tds + 2*targets</code>.</p>"
        "<p>Scores are descriptive and ranking-oriented, not causal estimates. "
        "This study reruns on each build as new rosters and statlines land.</p>"
    )

    history_row = {
        "cross_league_duos": len(cross_league),
        "repeat_duos": len(candidates),
        "top_duo": leaderboard[0]["duo"] if leaderboard else None,
        "top_duo_leagues": leaderboard[0]["leagues_count"] if leaderboard else 0,
        "top_duo_team_seasons": leaderboard[0]["team_seasons"] if leaderboard else 0,
    }

    return {
        "headline_stats": headline_stats,
        "charts": [chart_score, chart_footprint, chart_network],
        "sections": sections,
        "methodology": methodology,
        "table": {
            "columns": [
                {"key": "duo", "label": "QB -> receiver duo"},
                {"key": "team_seasons", "label": "Shared team-seasons", "numeric": True},
                {"key": "leagues_count", "label": "Leagues", "numeric": True},
                {"key": "leagues", "label": "League footprint"},
                {"key": "total_score", "label": "Total chemistry score", "numeric": True},
                {"key": "avg_score", "label": "Avg score per team-season", "numeric": True},
                {"key": "pass_yards", "label": "QB passing yards", "numeric": True},
                {"key": "rec_yards", "label": "Receiver yards", "numeric": True},
                {"key": "best_team_season", "label": "Best team-season"},
                {"key": "best_ol", "label": "Most-shared OL"},
                {"key": "unit_context", "label": "Unit context"},
            ],
            "rows": rows,
        },
        "history_row": history_row,
    }

"""
Study: What leagues are best to get you to the NFL?

Looks at every football player in the archive and, for each non-NFL pro
football league, computes how many of that league's players also appear in
the NFL. Where season-year metadata is available, we further break out the
players whose alt-league appearance came BEFORE their NFL appearance — i.e.
the alt league plausibly served as a stepping stone, rather than a landing
spot for ex-NFL veterans.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

SLUG     = "nfl-pipeline-leagues"
TITLE    = "Which leagues feed the NFL?"
SUBTITLE = "Cross-league career trajectories of pro football players"
CATEGORY = "Football"
TAGS     = ["football", "nfl", "career"]

# Pro football leagues we track in this archive (sport_names values).
FOOTBALL_LEAGUES = {
    "NFL", "CFL", "USFL", "XFL", "UFL", "AAF", "AFL", "IFL", "MLFB",
    "ELF", "EFA", "AF1", "FCF", "NAL", "LFA", "X-LEAGUE",
}

# Display names + a short description for each alt league.
LEAGUE_INFO = {
    "CFL":      ("CFL",          "Canadian Football League — 9 teams, founded 1958."),
    "USFL":     ("USFL",         "United States Football League (revived 2022, merged with XFL into UFL in 2024)."),
    "XFL":      ("XFL",          "XFL (revived 2023, merged with USFL into UFL in 2024)."),
    "UFL":      ("UFL",          "United Football League — formed by the 2024 merger of USFL and XFL."),
    "AAF":      ("AAF",          "Alliance of American Football — single 2019 season."),
    "AFL":      ("AFL",          "Arena Football League (revived 2024)."),
    "IFL":      ("IFL",          "Indoor Football League."),
    "MLFB":     ("MLFB",         "Major League Football."),
    "ELF":      ("ELF",          "European League of Football."),
    "EFA":      ("EFA",          "European Football Alliance — six-team breakaway from the ELF (debut 2026)."),
    "AF1":      ("AF1",          "Arena Football One."),
    "FCF":      ("FCF",          "Fan Controlled Football."),
    "NAL":      ("NAL",          "National Arena League."),
    "LFA":      ("LFA",          "Liga de Fútbol Americano Profesional (Mexico)."),
    "X-LEAGUE": ("X-League",     "Japan's top-tier American football league."),
}


def _league_first_year(season_totals: dict) -> dict:
    """league -> earliest year recorded in season_totals keys like 'CFL-2023'."""
    out: dict[str, int] = {}
    for k in (season_totals or {}):
        if "-" not in k:
            continue
        lg, yr = k.rsplit("-", 1)
        if not yr.isdigit():
            continue
        y = int(yr)
        lg_upper = lg.upper()
        if lg_upper not in out or y < out[lg_upper]:
            out[lg_upper] = y
    return out


def _load_sport_id_map(data_dir: Path) -> dict:
    """sport_id -> normalized league code, used as a chronology fallback."""
    try:
        sports = json.loads((data_dir / "sports.json").read_text()).get("sports", [])
    except (json.JSONDecodeError, OSError):
        return {}
    out = {}
    for s in sports:
        sid = s.get("id")
        name = (s.get("name") or "").upper().strip()
        if sid is None or not name:
            continue
        # normalize a couple of common variants
        if name in ("50YARD",):
            name = "50 YARD"
        out[sid] = name
    return out


def _appearance_first_sportid(appearances, league_code: str, sport_id_map: dict):
    """Lowest sport_id whose normalized name == league_code (used as a chronology proxy)."""
    best = None
    for a in appearances or []:
        sid = a.get("sport_id")
        if sid is None:
            continue
        nm = sport_id_map.get(sid)
        if nm == league_code:
            if best is None or sid < best:
                best = sid
    return best


def compute(data_dir: Path) -> dict:
    players_dir = data_dir / "players"
    files = list(players_dir.glob("*.json"))
    sport_id_map = _load_sport_id_map(data_dir)

    # league -> aggregated counts
    agg: dict[str, dict] = {}
    for code in LEAGUE_INFO:
        agg[code] = {
            "players":          0,  # players ever in this league
            "to_nfl":           0,  # also in NFL (any time)
            "with_year_data":   0,  # both alt-year and NFL-year known
            "before_nfl":       0,  # alt-year strictly before NFL-year
            "after_nfl":        0,  # alt-year strictly after NFL-year (landing spot)
            "same_year":        0,
        }

    total_examined = 0
    total_football = 0
    total_nfl_only = 0

    # Player-counts per league + DIRECTED flow counts.
    # node_players[code] = count of players who ever appeared in `code`
    # flow_counts[(src, dst)] = count of players whose first appearance in `src`
    #   happened before their first appearance in `dst`
    node_players: dict[str, int] = {}
    flow_counts: dict[tuple[str, str], int] = {}
    flow_unknown: dict[tuple[str, str], int] = {}  # both leagues but no chronology

    valid_codes = set(LEAGUE_INFO) | {"NFL"}

    for pf in files:
        try:
            d = json.loads(pf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        sn = {s.upper() for s in (d.get("sport_names") or [])}
        if not (sn & FOOTBALL_LEAGUES):
            continue
        total_examined += 1
        if d.get("ambiguous"):
            # Skip ambiguous identity records — they can pollute counts.
            continue
        total_football += 1
        in_nfl = "NFL" in sn
        if sn <= {"NFL"}:
            total_nfl_only += 1

        # Network: count node players + DIRECTED flows among tracked leagues.
        player_codes = sorted(c for c in sn if c in valid_codes)
        for c in player_codes:
            node_players[c] = node_players.get(c, 0) + 1

        # Build a chronology key for each of this player's leagues.
        # Prefer season_totals year; fall back to sport_id ordering.
        years_local = _league_first_year(d.get("season_totals") or {})
        appearances_local = d.get("appearances") or []
        order_keys: dict[str, tuple] = {}
        for c in player_codes:
            yr = years_local.get(c)
            if yr is not None:
                order_keys[c] = (0, yr)  # year-data tier (more reliable)
            else:
                sid = _appearance_first_sportid(appearances_local, c, sport_id_map)
                if sid is not None:
                    order_keys[c] = (1, sid)  # sport_id-fallback tier
                # else: no chronology info — handled below

        for i in range(len(player_codes)):
            for j in range(i + 1, len(player_codes)):
                a, b = player_codes[i], player_codes[j]
                ka = order_keys.get(a)
                kb = order_keys.get(b)
                if ka is None or kb is None:
                    flow_unknown[(a, b)] = flow_unknown.get((a, b), 0) + 1
                    continue
                if ka < kb:
                    flow_counts[(a, b)] = flow_counts.get((a, b), 0) + 1
                elif ka > kb:
                    flow_counts[(b, a)] = flow_counts.get((b, a), 0) + 1
                else:
                    # Same first year/sport_id — split as half each so totals match.
                    # Use a separate "same" bucket via flow_unknown so it's clear in the data.
                    flow_unknown[(a, b)] = flow_unknown.get((a, b), 0) + 1

        years = _league_first_year(d.get("season_totals") or {})
        nfl_year = years.get("NFL")
        appearances = d.get("appearances") or []

        for lg in sn:
            if lg == "NFL" or lg not in LEAGUE_INFO:
                continue
            r = agg[lg]
            r["players"] += 1
            if in_nfl:
                r["to_nfl"] += 1
                ly = years.get(lg)
                # Prefer real season-year data; fall back to sport_id ordering.
                if ly is not None and nfl_year is not None:
                    r["with_year_data"] += 1
                    if ly < nfl_year:
                        r["before_nfl"] += 1
                    elif ly > nfl_year:
                        r["after_nfl"] += 1
                    else:
                        r["same_year"] += 1
                else:
                    nfl_sid = _appearance_first_sportid(appearances, "NFL", sport_id_map)
                    alt_sid = _appearance_first_sportid(appearances, lg, sport_id_map)
                    if nfl_sid is not None and alt_sid is not None:
                        r["with_year_data"] += 1
                        if alt_sid < nfl_sid:
                            r["before_nfl"] += 1
                        elif alt_sid > nfl_sid:
                            r["after_nfl"] += 1
                        else:
                            r["same_year"] += 1

    # Build per-league rows with rates.
    rows = []
    for code, r in agg.items():
        if r["players"] == 0:
            continue
        display, blurb = LEAGUE_INFO[code]
        rate = (r["to_nfl"] / r["players"]) if r["players"] else 0.0
        # Stepping-stone share among the year-data subset
        stepping_share = (
            r["before_nfl"] / r["with_year_data"]
            if r["with_year_data"] else None
        )
        rows.append({
            "league":          display,
            "code":            code,
            "blurb":           blurb,
            "players":         r["players"],
            "to_nfl":          r["to_nfl"],
            "rate":            round(rate, 4),
            "rate_pct":        round(rate * 100, 1),
            "before_nfl":      r["before_nfl"],
            "after_nfl":       r["after_nfl"],
            "same_year":       r["same_year"],
            "with_year_data":  r["with_year_data"],
            "alt_to_nfl_rate": round((r["before_nfl"] / r["players"]) if r["players"] else 0.0, 4),
            "alt_to_nfl_pct":  round(((r["before_nfl"] / r["players"]) if r["players"] else 0.0) * 100, 1),
            "stepping_share":  round(stepping_share, 4) if stepping_share is not None else None,
            "stepping_pct":    round(stepping_share * 100, 1) if stepping_share is not None else None,
        })

    rows.sort(key=lambda x: x["rate"], reverse=True)

    # Headline stats
    if rows:
        top_directional = max(rows, key=lambda x: x["alt_to_nfl_rate"])
        overall_players = sum(r["players"] for r in rows)
        overall_before = sum(r["before_nfl"] for r in rows)
        overall_pct = round((overall_before / overall_players) * 100, 1) if overall_players else 0.0
        median_pct = round(statistics.median(r["alt_to_nfl_pct"] for r in rows), 1)
        headline_stats = [
            {"label": "Best alt league → NFL rate",
             "value": f"{top_directional['alt_to_nfl_pct']}%",
             "sub":   f"{top_directional['league']} ({top_directional['before_nfl']:,} of {top_directional['players']:,} players)"},
            {"label": "Overall alt league → NFL rate",
             "value": f"{overall_pct}%",
             "sub":   f"{overall_before:,} of {overall_players:,} league-player records move alt→NFL"},
            {"label": "Median league alt → NFL rate",
             "value": f"{median_pct}%",
             "sub":   f"across {len(rows)} non-NFL leagues"},
        ]
    else:
        headline_stats = []

    # Chart 1: NFL conversion rate by league
    chart_rate = {
        "id":    "chart-rate",
        "type":  "bar",
        "title": "% of league's players who also appear in the NFL",
        "labels":   [r["league"] for r in rows],
        "datasets": [{
            "label": "% to NFL",
            "data":  [r["rate_pct"] for r in rows],
        }],
        "value_suffix": "%",
        "indexAxis": "y",
        "note": "Includes any NFL appearance, regardless of whether it came before or after the alt-league stint.",
    }

    # Chart 2: Stepping stone vs landing spot, for leagues with year data.
    rows_with_dir = [r for r in rows if r["with_year_data"] >= 5]
    chart_dir = {
        "id":    "chart-direction",
        "type":  "stacked-bar",
        "title": "Launching pad or landing spot?",
        "labels":   [r["league"] for r in rows_with_dir],
        "datasets": [
            {"label": "Alt league → NFL (before)",
             "data":  [r["before_nfl"] for r in rows_with_dir]},
            {"label": "NFL → alt league (after)",
             "data":  [r["after_nfl"] for r in rows_with_dir]},
            {"label": "Same year",
             "data":  [r["same_year"] for r in rows_with_dir]},
        ],
        "indexAxis": "y",
        "note": "Counts of NFL crossovers split by which league the player appeared in first. Useful for telling launching pads from landing spots, but does not by itself answer the selection-vs-development question above.",
    }

    # Chart 3: Network of player movement between leagues.
    # Nodes are leagues sized by player population; edges are weighted by the
    # number of players who appeared in both leagues.
    network_node_codes = sorted(node_players.keys())

    def _league_display(c: str) -> str:
        if c == "NFL":
            return "NFL"
        return LEAGUE_INFO[c][0] if c in LEAGUE_INFO else c

    # Color palette: NFL highlighted, alt leagues use a secondary tone.
    network_nodes = [
        {
            "id":      c,
            "label":   _league_display(c),
            "value":   node_players[c],
            "is_nfl":  c == "NFL",
        }
        for c in network_node_codes
    ]
    network_edges = []
    for (a, b), v in flow_counts.items():
        if v <= 0:
            continue
        network_edges.append({
            "source":   a,
            "target":   b,
            "value":    v,
            "directed": True,
        })
    # Add an undirected/unknown edge for pairs with no chronology data, so they
    # still show up but visually distinct (no arrow, dashed).
    for (a, b), v in flow_unknown.items():
        if v <= 0:
            continue
        # Skip if this pair already has a strong directed signal in either dir.
        if flow_counts.get((a, b), 0) + flow_counts.get((b, a), 0) >= v:
            continue
        network_edges.append({
            "source":   a,
            "target":   b,
            "value":    v,
            "directed": False,
        })
    # Sort edges by weight desc so heaviest are drawn last (on top).
    network_edges.sort(key=lambda e: e["value"])

    chart_network = {
        "id":     "chart-network",
        "type":   "network",
        "title":  "Player movement between leagues",
        "nodes":  network_nodes,
        "edges":  network_edges,
        "note":   "Each node is a league, sized by the number of unique players in it. Each arrow points from the league a player appeared in first to the league they appeared in next; thickness is proportional to the number of players who made that move. Dashed grey lines mark pairs of leagues where chronology can't be determined. Drag a node to reposition; hover for exact counts.",
    }

    # Sections of prose
    sections = [
        {
            "heading": "What we measured",
            "html": (
                "<p>Every player in the archive is tagged with the leagues they have appeared in. "
                "For each non-NFL professional football league we count two things:</p>"
                "<ol>"
                "<li>How many players have appeared in that league at all.</li>"
                "<li>How many of those players also appear in our NFL roster data.</li>"
                "</ol>"
                "<p>The ratio of the second to the first is the league's <em>NFL conversion rate</em>.</p>"
            ),
        },
        {
            "heading": "Top-line result",
            "html": (
                f"<p>The headline cards above now focus on directional movement: players who "
                f"appear in an alt league <em>before</em> the NFL. On that measure, "
                f"<strong>{max(rows, key=lambda x: x['alt_to_nfl_rate'])['league']}</strong> leads at "
                f"<strong>{max(rows, key=lambda x: x['alt_to_nfl_rate'])['alt_to_nfl_pct']}%</strong> "
                f"({max(rows, key=lambda x: x['alt_to_nfl_rate'])['before_nfl']:,} of "
                f"{max(rows, key=lambda x: x['alt_to_nfl_rate'])['players']:,}).</p>"
                f"<p>Important caveat: chart and table conversion columns still show "
                f"<em>any</em> NFL overlap (before or after) so you can compare both lenses.</p>"
                if rows else "<p>No data available.</p>"
            ),
        },
        {
            "heading": "Chicken or egg?",
            "html": (
                "<p>A high conversion rate does not mean a league <em>develops</em> NFL players. "
                "There are two competing explanations for why league A might send a higher share "
                "of its players to the NFL than league B:</p>"
                "<ol>"
                "<li><strong>Development:</strong> league A is genuinely a better proving ground — "
                "its coaching, competition, or visibility makes the players in it more likely to "
                "earn an NFL roster spot than they otherwise would.</li>"
                "<li><strong>Selection:</strong> league A simply gets first pick of the best "
                "non-NFL talent. Those players were always going to be the most likely to make "
                "the NFL; the league just happens to be where they landed first. The same players "
                "in league B would have made the NFL at the same rate.</li>"
                "</ol>"
                "<p>Nothing in this dataset can fully separate the two. To do that you would need "
                "a counterfactual: take a player who went to league A, run their career again with "
                "them in league B, and see what happens. We can only observe the world that "
                "actually happened.</p>"
                "<p>What we can do is flag the structural reasons each effect probably matters:</p>"
                "<ul>"
                "<li>The <strong>UFL</strong> (and its predecessor brands USFL/XFL) actively "
                "scouts and signs players who recently failed to stick on an NFL roster — a "
                "population that is already pre-selected for NFL-readiness. A high conversion "
                "rate there is mostly the selection story.</li>"
                "<li>The <strong>CFL</strong> has a much larger talent pool (over 4,000 tracked "
                "players) and a wider mix of pure-CFL career players. Its lower conversion rate "
                "partly reflects that it isn't filtering as aggressively for NFL-prospect types.</li>"
                "<li>Indoor leagues (<strong>AFL</strong>, <strong>IFL</strong>, "
                "<strong>AF1</strong>, <strong>NAL</strong>) play a different game on a smaller "
                "field, so the population there self-selects away from NFL-style talent in the "
                "first place.</li>"
                "</ul>"
                "<p>One thing the data <em>can</em> show directly is the direction of the move. "
                "Below we split each league's NFL crossovers into players who appeared in the "
                "alt league before reaching the NFL versus those who appeared after — i.e. "
                "ex-NFL veterans dropping down. This is not the same as answering selection-vs-"
                "development, but it does tell you whether a league is mostly a launching pad or "
                "mostly a landing spot.</p>"
            ),
        },
        {
            "heading": "League-by-league",
            "html": "<p>Full breakdown in the table at the bottom of the article.</p>",
        },
    ]

    methodology = (
        "<p>Source: every player JSON file in <code>docs/data/players/</code>. "
        "A player is counted in a league if that league appears in their <code>sport_names</code> "
        "list (derived from any roster appearance, not just statlines). "
        "Players flagged <code>ambiguous</code> are excluded because their cross-league identity is uncertain.</p>"
        "<p>Year ordering uses the earliest year recorded in <code>season_totals</code> per league. "
        "When season-year metadata is missing on either side, we fall back to the order of "
        "<code>sport_id</code> values on the player's roster appearances — a coarse but reliable "
        "proxy because sport IDs in this archive are issued chronologically.</p>"
        "<p>This study reruns on every site build; numbers will drift as the archive ingests "
        "more rosters and as players move between leagues.</p>"
    )

    history_row = {
        "leagues": {r["code"]: {"players": r["players"], "to_nfl": r["to_nfl"], "rate_pct": r["rate_pct"]} for r in rows},
        "total_football_players": total_football,
    }

    return {
        "headline_stats": headline_stats,
        "charts":         [chart_rate, chart_network, chart_dir],
        "sections":       sections,
        "methodology":    methodology,
        "table":          {
            "columns": [
                {"key": "league",        "label": "League"},
                {"key": "players",       "label": "Players in archive", "numeric": True},
                {"key": "to_nfl",        "label": "Also in NFL",         "numeric": True},
                {"key": "rate_pct",      "label": "% to NFL",            "numeric": True, "suffix": "%"},
                {"key": "before_nfl",    "label": "Pre-NFL appearance",  "numeric": True},
                {"key": "after_nfl",     "label": "Post-NFL appearance", "numeric": True},
                {"key": "stepping_pct",  "label": "Stepping-stone share","numeric": True, "suffix": "%"},
                {"key": "blurb",         "label": "Notes"},
            ],
            "rows": rows,
        },
        "history_row":    history_row,
    }

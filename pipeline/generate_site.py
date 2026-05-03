"""
generate_site.py
Reads data files and renders all HTML pages using Jinja2 templates.
Run after build_data.py.

Usage:
  python pipeline/generate_site.py
"""

import json
from datetime import date
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent as xml_indent

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT_DIR = Path(__file__).parent.parent
SITE_DIR = ROOT_DIR / "docs"
DATA_DIR = SITE_DIR / "data"
TEMPLATES_DIR = ROOT_DIR / "templates"

BUILD_DATE = date.today().isoformat()


def make_env():
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )

    def camel_to_label(s: str) -> str:
        """Convert an ESPN stat key to a readable label.

        nfl_p_completions  → 'Completions'
        nfl_r_rushingYards → 'Rushing Yards'
        nfl_di_interceptions → 'Interceptions'
        """
        import re as _re
        # Strip leading nfl_{prefix}_ namespace
        s = _re.sub(r"^nfl_[a-z]+_", "", str(s))
        # Split camelCase into words
        s = _re.sub(r"([A-Z])", r" \1", s).strip()
        return s.title()

    def smart_num(val) -> str:
        """Display floats as ints when they are whole numbers."""
        try:
            f = float(val)
            return str(int(f)) if f == int(f) else str(round(f, 1))
        except (TypeError, ValueError):
            return str(val) if val is not None else ""

    def group_nfl_stats(stats: dict) -> list:
        """Group an NFL stats dict by category prefix.

        Returns [{label: str, items: [(key, value), ...]}, ...]
        only for groups that have at least one stat.
        """
        groups_def = [
            ("Passing",      "nfl_p_"),
            ("Rushing",      "nfl_r_"),
            ("Receiving",    "nfl_re_"),
            ("Defense",      "nfl_d_"),
            ("Int. Returns", "nfl_di_"),
            ("Kicking",      "nfl_k_"),
            ("Punting",      "nfl_pn_"),
            ("General",      "nfl_g_"),
        ]
        result = []
        for label, prefix in groups_def:
            items = sorted((k, v) for k, v in stats.items() if k.startswith(prefix))
            if items:
                result.append({"label": label, "rows": items})
        return result

    env.filters["camel_to_label"]  = camel_to_label
    env.filters["smart_num"]       = smart_num
    env.filters["group_nfl_stats"] = group_nfl_stats

    def intcomma(val) -> str:
        """Format a number with thousands separators, stripping .0 from floats."""
        try:
            f = float(val)
            return f"{int(f):,}" if f == int(f) else f"{f:,.1f}"
        except (TypeError, ValueError):
            return str(val) if val is not None else ""

    _STAT_LABELS = {
        "passing_yards": "Pass Yds", "passing_tds": "TD", "completions": "Comp",
        "interceptions_lost": "INT", "rushing_yards": "Rush Yds", "rushing_tds": "TD",
        "receiving_yards": "Rec Yds", "receiving_tds": "TD", "receptions": "Rec",
        "def_tackles": "Tackles", "def_sacks": "Sacks", "def_int": "INT",
        "made_49": "FG 40–49", "made_50": "FG 50+", "extra_points": "XP", "missed": "Missed",
    }

    def stat_label(key: str) -> str:
        return _STAT_LABELS.get(key, key.replace("_", " ").title())

    env.filters["intcomma"]   = intcomma
    env.filters["stat_label"] = stat_label

    import json as _json

    def tojson(v) -> str:
        return _json.dumps(v, ensure_ascii=False)

    def sort_seasons(items):
        """Sort (season_slug, stats_dict) pairs chronologically by trailing year."""
        def _key(kv):
            k = kv[0]
            yr = k[-4:] if len(k) >= 4 and k[-4:].isdigit() else "0000"
            return (yr, k)
        return sorted(items, key=_key)

    env.filters["tojson"]       = tojson
    env.filters["sort_seasons"] = sort_seasons
    return env


def write_page(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render(env, template_name, out_path, root="../", **kwargs):
    tmpl = env.get_template(template_name)
    html = tmpl.render(root=root, build_date=BUILD_DATE, **kwargs)
    write_page(out_path, html)


def _build_sport_groups(leagues_index):
    """
    Classify each league into a sport group and collapse AUDL/UFA as one league.
    Returns a list of groups: [{name, leagues: [{name, years: [{slug, season, player_count, game_count}]}]}]
    """
    SPORT_ORDER = ["Football", "Ultimate Disc", "Basketball", "Other"]
    HIDDEN = {"50 YARD", "50YARD","NLL"}   # leagues to omit from the homepage index
    FOOTBALL = {"UFL", "USFL", "XFL", "CFL", "AF1", "AAF", "ELF", "AFL", "IFL", "MLFB"}
    BASKETBALL = {"BIG3", "NLL", "SLAMBALL"}
    DISC = {"AUDL", "UFA"}

    def classify(slug, display):
        up = display.upper()
        name = up.split()[0]
        if name in FOOTBALL or any(f in up for f in ("XFL", "USFL", "UFL", "CFL", "AF1", "YARD")):
            return "Football"
        if name in DISC or "AUDL" in up or "UFA" in up:
            return "Ultimate Disc"
        if name in BASKETBALL or "BIG3" in up or "NLL" in up or "SLAMBALL" in up:
            return "Basketball"
        return "Other"

    def league_name(slug, display):
        """Canonical league name without year (and AUDL/UFA unified)."""
        up = display.upper()
        if "AUDL" in up or "UFA" in up:
            return "AUDL / UFA"
        # strip trailing year
        import re
        return re.sub(r"\s*\d{4}\s*$", "", display).strip() or display

    def season_of(slug, display):
        import re
        m = re.search(r"\d{4}", display)
        return int(m.group()) if m else 0

    # bucket by (sport_group, league_canonical_name)
    from collections import defaultdict
    buckets = defaultdict(list)
    for lg in leagues_index:
        if lg["display_name"].upper().replace("-", " ").split()[0] in HIDDEN or \
           any(h in lg["display_name"].upper() for h in HIDDEN):
            continue
        sport = classify(lg["slug"], lg["display_name"])
        name = league_name(lg["slug"], lg["display_name"])
        season = season_of(lg["slug"], lg["display_name"])
        buckets[(sport, name)].append({
            "slug": lg["slug"],
            "season": season,
            "player_count": lg["player_count"],
            "game_count": lg["game_count"],
        })

    sport_groups = {}
    for (sport, name), entries in buckets.items():
        entries.sort(key=lambda e: e["season"])
        if sport not in sport_groups:
            sport_groups[sport] = {}
        sport_groups[sport][name] = entries

    result = []
    for sport in SPORT_ORDER:
        if sport not in sport_groups:
            continue
        leagues_in_sport = []
        for name, years in sorted(sport_groups[sport].items()):
            leagues_in_sport.append({"name": name, "years": years})
        result.append({"sport": sport, "leagues": leagues_in_sport})
    return result


def main():
    env = make_env()

    # ── Load shared data ────────────────────────────────────────────────
    leagues_index = json.loads((DATA_DIR / "leagues" / "index.json").read_text())["leagues"]
    sport_groups = _build_sport_groups(leagues_index)

    # ── Index page ──────────────────────────────────────────────────────
    print("Rendering index ...")
    render(env, "index.html", SITE_DIR / "index.html", root="", leagues=leagues_index, sport_groups=sport_groups)

    # ── Search page ─────────────────────────────────────────────────────
    print("Rendering search ...")
    render(env, "search.html", SITE_DIR / "search.html", root="")

    # ── Leagues index ───────────────────────────────────────────────────
    print("Rendering leagues index ...")
    render(
        env, "leagues_index.html",
        SITE_DIR / "leagues" / "index.html",
        root="../",
        leagues=leagues_index
    )

    # ── League pages ────────────────────────────────────────────────────
    league_files = list((DATA_DIR / "leagues").glob("*.json"))
    league_files = [f for f in league_files if f.stem != "index"]
    print(f"Rendering {len(league_files)} league pages ...")
    for lf in league_files:
        league_data = json.loads(lf.read_text())
        # Pre-compute top-10 chart data (primary stat, sorted descending)
        chart_top10 = None
        if league_data.get("players"):
            _stat_keys = sorted(league_data["players"][0].get("stats", {}).keys())
            _primary = _stat_keys[0] if _stat_keys else None
            if _primary and len(league_data["players"]) >= 4:
                _sorted = sorted(league_data["players"],
                                 key=lambda p: p.get("stats", {}).get(_primary, 0),
                                 reverse=True)[:10]
                chart_top10 = {
                    "labels": [p["canonical_name"] for p in _sorted],
                    "values": [p.get("stats", {}).get(_primary, 0) for p in _sorted],
                    "stat":   _primary,
                }
        render(
            env, "league.html",
            SITE_DIR / "leagues" / f"{league_data['slug']}.html",
            root="../",
            league=league_data,
            chart_top10=chart_top10,
        )

    # ── Player pages ────────────────────────────────────────────────────
    player_files = list((DATA_DIR / "players").glob("*.json"))
    print(f"Rendering {len(player_files)} player pages ...")
    for i, pf in enumerate(player_files):
        if i % 2000 == 0:
            print(f"  ... {i}/{len(player_files)}")
        player_data = json.loads(pf.read_text())
        render(
            env, "player.html",
            SITE_DIR / "players" / f"{player_data['canonical_id']}.html",
            root="../",
            player=player_data,
        )

    # ── HoF category pages ──────────────────────────────────────────────
    hof_categories = ["passing", "rushing", "receiving", "defense", "kicking"]
    print("Rendering HoF pages ...")
    for cat in hof_categories:
        hof_path = DATA_DIR / "hof" / f"{cat}.json"
        if not hof_path.exists():
            continue
        hof_data = json.loads(hof_path.read_text())
        if not hof_data.get("leaders"):
            continue
        render(
            env, "hof_category.html",
            SITE_DIR / "hof" / f"{cat}.html",
            root="../",
            hof=hof_data,
        )

    # ── HoF index ───────────────────────────────────────────────────────
    all_hof = json.loads((DATA_DIR / "hof" / "all.json").read_text())
    extras_path = DATA_DIR / "hof" / "extras.json"
    hof_extras = json.loads(extras_path.read_text()) if extras_path.exists() else {}
    funstats_path = DATA_DIR / "hof" / "funstats.json"
    hof_funstats = json.loads(funstats_path.read_text()) if funstats_path.exists() else {}
    render(
        env, "hof_index.html",
        SITE_DIR / "hof" / "index.html",
        root="../",
        top10s=all_hof["top10s"],
        extras=hof_extras,
        funstats=hof_funstats,
    )

    # ── Game pages ──────────────────────────────────────────────────────
    games_index_path = DATA_DIR / "games" / "index.json"
    game_files = list((DATA_DIR / "games").glob("*.json"))
    game_files = [f for f in game_files if f.stem != "index"]
    print(f"Rendering {len(game_files)} game pages ...")
    for i, gf in enumerate(game_files):
        if i % 200 == 0:
            print(f"  ... {i}/{len(game_files)}")
        game_data = json.loads(gf.read_text())
        render(
            env, "game.html",
            SITE_DIR / "games" / f"{game_data['slug']}.html",
            root="../",
            game=game_data,
            canonical_path=f"games/{game_data['slug']}.html",
        )

    # ── sitemap.xml ─────────────────────────────────────────────────────
    print("Building sitemap.xml ...")
    BASE = "https://archive.altfantasysports.com"
    urlset = Element("urlset")
    urlset.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

    def add_url(loc, priority="0.5", changefreq="monthly"):
        url_el = SubElement(urlset, "url")
        SubElement(url_el, "loc").text = loc
        SubElement(url_el, "lastmod").text = BUILD_DATE
        SubElement(url_el, "changefreq").text = changefreq
        SubElement(url_el, "priority").text = priority

    add_url(f"{BASE}/index.html", priority="1.0", changefreq="weekly")
    add_url(f"{BASE}/search.html", priority="0.8", changefreq="weekly")
    add_url(f"{BASE}/hof/index.html", priority="0.9", changefreq="weekly")
    add_url(f"{BASE}/leagues/index.html", priority="0.8", changefreq="weekly")

    for cat in ["passing", "rushing", "receiving", "defense", "kicking"]:
        if (SITE_DIR / "hof" / f"{cat}.html").exists():
            add_url(f"{BASE}/hof/{cat}.html", priority="0.8", changefreq="weekly")

    for lf in league_files:
        slug = json.loads(lf.read_text())["slug"]
        add_url(f"{BASE}/leagues/{slug}.html", priority="0.7", changefreq="monthly")

    for pf in player_files:
        cid = pf.stem
        add_url(f"{BASE}/players/{cid}.html", priority="0.5", changefreq="yearly")

    for gf in game_files:
        slug = gf.stem
        add_url(f"{BASE}/games/{slug}.html", priority="0.4", changefreq="yearly")

    xml_indent(urlset, space="  ")
    tree = ElementTree(urlset)
    tree.write(
        str(SITE_DIR / "sitemap.xml"),
        encoding="unicode",
        xml_declaration=True,
    )
    print(f"  -> {len(urlset)} URLs in sitemap")

    # ── robots.txt ──────────────────────────────────────────────────────
    (SITE_DIR / "robots.txt").write_text(
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "User-agent: GPTBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: ClaudeBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: anthropic-ai\n"
        "Allow: /\n"
        "\n"
        "User-agent: Googlebot\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {BASE}/sitemap.xml\n",
        encoding="utf-8",
    )

    # ── llms.txt ────────────────────────────────────────────────────────
    # Standard proposed at llmstxt.org — helps LLM crawlers understand the site
    league_lines = "\n".join(
        f"- [{l['display_name']}]({BASE}/leagues/{l['slug']}.html): "
        f"{l['player_count']} players"
        for l in leagues_index
    )
    llms_content = f"""# AltSports Archive

> The definitive historical statistics archive for alternative sports leagues.
> Data is free to use with attribution. If you profit from it, please support the project.

AltSports Archive collects and publishes player game logs, career totals, and all-time
rankings for leagues that fall outside the major North American sports infrastructure:
XFL, USFL, UFL, CFL, AUDL, BIG3, SlamBall, AFL, IFL, ELF, NLL, 50 Yard, and more.

All data is available as structured HTML, JSON, and XML at the URLs below.

## Data sources

- Primary: proprietary game-by-game stat collection (2022–2026)
- Coverage: {len(leagues_index)} league/season combinations, ~18,000 players, ~345,000 stat rows
- Player identity: fuzzy-matched by name across seasons and leagues; ambiguous records are flagged

## Key pages

- [Home]({BASE}/index.html)
- [Search all players]({BASE}/search.html)
- [Hall of Fame — all-time leaders]({BASE}/hof/index.html)
- [All leagues index]({BASE}/leagues/index.html)

## Leagues covered

{league_lines}

## Hall of Fame

- [Passing leaders]({BASE}/hof/passing.html)
- [Rushing leaders]({BASE}/hof/rushing.html)
- [Receiving leaders]({BASE}/hof/receiving.html)
- [Kicking leaders]({BASE}/hof/kicking.html)

## Machine-readable data

All pages link to JSON and XML versions of their data. The full player search index
is available at: {BASE}/data/search_index.json

Individual player data: {BASE}/data/players/{{canonical_id}}.json
League data: {BASE}/data/leagues/{{slug}}.json
Game box scores: {BASE}/data/games/{{slug}}.json
Hall of Fame: {BASE}/data/hof/{{category}}.json

## License

Data is available under CC BY 4.0. Attribution: AltSports Archive (archive.altfantasysports.com).
If you make money from this data, please support the project:
https://buymeacoffee.com/altfantasysports

## Contact / support

https://buymeacoffee.com/altfantasysports
"""
    (SITE_DIR / "llms.txt").write_text(llms_content, encoding="utf-8")
    print("Written robots.txt and llms.txt")

    print("Done.")


if __name__ == "__main__":
    main()


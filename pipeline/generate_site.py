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
SITE_DIR = ROOT_DIR / "site"
DATA_DIR = SITE_DIR / "data"
TEMPLATES_DIR = ROOT_DIR / "templates"

BUILD_DATE = date.today().isoformat()


def make_env():
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    return env


def write_page(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render(env, template_name, out_path, root="../", **kwargs):
    tmpl = env.get_template(template_name)
    html = tmpl.render(root=root, build_date=BUILD_DATE, **kwargs)
    write_page(out_path, html)


def main():
    env = make_env()

    # ── Load shared data ────────────────────────────────────────────────
    leagues_index = json.loads((DATA_DIR / "leagues" / "index.json").read_text())["leagues"]

    # ── Index page ──────────────────────────────────────────────────────
    print("Rendering index ...")
    render(env, "index.html", SITE_DIR / "index.html", root="", leagues=leagues_index)

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
        render(
            env, "league.html",
            SITE_DIR / "leagues" / f"{league_data['slug']}.html",
            root="../",
            league=league_data,
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
    hof_categories = ["passing", "rushing", "receiving", "kicking"]
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
    render(
        env, "hof_index.html",
        SITE_DIR / "hof" / "index.html",
        root="../",
        top10s=all_hof["top10s"],
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

    for cat in ["passing", "rushing", "receiving", "kicking"]:
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


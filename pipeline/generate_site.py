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
MERGED_DIR = ROOT_DIR / "pipeline" / "merged"

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
    from markupsafe import Markup

    def tojson(v):
        # Encode as JSON and mark safe so autoescape doesn't HTML-escape the
        # quotes/ampersands when embedded inside <script> blocks. We escape
        # the script-terminator just in case data contains "</script>".
        s = _json.dumps(v, ensure_ascii=False).replace("</", "<\\/")
        return Markup(s)

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
    SPORT_ORDER = ["Football", "Soccer", "Cricket", "Curling", "Lacrosse", "Ultimate Disc", "Basketball", "Disc Golf", "Other"]
    HIDDEN = {"50 YARD", "50YARD"}   # leagues to omit from the homepage index
    FOOTBALL = {"UFL", "USFL", "XFL", "CFL", "AF1", "AAF", "ELF", "AFL", "IFL", "NAL", "LFA", "X-League", "XLEAGUE", "MLFB", "FCF"}
    BASKETBALL = {"BIG3", "SLAMBALL", "UNRIVALED", "WNBA"}
    DISC = {"AUDL", "UFA", "PUL"}
    LACROSSE = {"NLL", "PLL"}
    DISCGOLF = {"DGPT"}
    SOCCER = {"MLS", "NWSL", "USLC", "USL1", "USLS", "MLSNP", "NASL"}
    CRICKET = {"T20I", "ODI", "TESTS", "IPL", "BBL", "WBBL", "PSL", "MLC", "CPL", "WPL", "BPL", "LPL", "HND", "ILT20", "SA20", "NPL"}
    CURLING = {"WCF-WORLD", "WCF-EUROPE", "WCF-PANCONT", "WCF-OLYMPIC", "WCF-QUAL", "WCF-OTHER", "CURLING-EVENTS"}

    def classify(slug, display):
        up = display.upper()
        name = up.split()[0]
        if name in LACROSSE or "NLL" in up or "PLL" in up:
            return "Lacrosse"
        if name in DISCGOLF or "DGPT" in up:
            return "Disc Golf"
        if name in FOOTBALL or any(f in up for f in ("XFL", "USFL", "UFL", "CFL", "AF1", "YARD", "FCF", "FAN CONTROLLED")):
            return "Football"
        if name in SOCCER:
            return "Soccer"
        if name in CRICKET or up in CRICKET or any(c in up for c in ("T20I", "ODI", "TESTS", "IPL", "BBL", "WBBL", "PSL", "MLC", "CPL", "WPL", "BPL", "LPL", "HND", "ILT20", "SA20", "NPL")):
            return "Cricket"
        if name in CURLING or name == "WCF" or up.startswith("WCF ") or any(c in up for c in ("WCF-WORLD", "WCF-EUROPE", "WCF-PANCONT", "WCF-OLYMPIC", "WCF-QUAL", "WCF-OTHER", "CURLING-EVENTS", "CURLING")):
            return "Curling"
        if name in DISC or "AUDL" in up or "UFA" in up or "PUL" in up or "PREMIER ULTIMATE" in up:
            return "Ultimate Disc"
        if name in BASKETBALL or "BIG3" in up or "SLAMBALL" in up or "UNRIVALED" in up or "WNBA" in up:
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


def build_player_timeline(player_data: dict) -> dict | None:
    """Return career timeline data for the visual header strip.

    Returns {"year_range": [2019..2024], "rows": [{"label", "type", "years"}, ...]}
    or None if there is not enough data to be useful.
    """
    season_totals = player_data.get("season_totals", {})
    college       = player_data.get("college")
    nfl           = player_data.get("nfl")

    league_years: dict[str, set] = {}
    all_years: set[int] = set()

    # season_totals keys are like "CFL-2023", "AAF-2019", "CFL-" (skip no-year)
    for key in season_totals:
        dash = key.rfind("-")
        if dash < 0:
            continue
        yr_str = key[dash + 1:]
        if not yr_str.isdigit():
            continue
        league = key[:dash]
        yr = int(yr_str)
        all_years.add(yr)
        league_years.setdefault(league, set()).add(yr)

    # College years
    if college:
        for yr_str in (college.get("seasons") or {}):
            try:
                yr = int(yr_str)
                all_years.add(yr)
                league_years.setdefault("College", set()).add(yr)
            except ValueError:
                pass

    if not all_years:
        return None

    year_range = list(range(min(all_years), max(all_years) + 1))

    # Build ordered rows: College first, then alt leagues sorted by first year
    rows = []
    if "College" in league_years:
        rows.append({"label": "College", "type": "college",
                     "years": sorted(league_years["College"])})

    if nfl:
        nfl_years = set()
        for row in (nfl.get("seasons") or []):
            try:
                yr = int(row.get("year"))
            except (TypeError, ValueError):
                continue
            nfl_years.add(yr)
            all_years.add(yr)
        rows.append({"label": "NFL", "type": "nfl", "years": sorted(nfl_years)})

    alt = [(lbl, yrs) for lbl, yrs in league_years.items() if lbl != "College"]
    alt.sort(key=lambda x: (min(x[1]) if x[1] else 9999, x[0]))
    for lbl, yrs in alt:
        rows.append({"label": lbl, "type": "alt", "years": sorted(yrs)})

    # Need at least 2 rows OR 2 distinct years to be useful
    if len(rows) < 2 and len(year_range) < 2:
        return None

    return {"year_range": year_range, "rows": rows}


def generate_player_og_image(player_data: dict, output_path: Path) -> bool:
    """
    Generate an OG image for social media sharing with career timeline grid.
    Returns True if image was generated, False otherwise.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False

    # Image dimensions (standard OG size: 1200x630)
    width, height = 1200, 630
    bg_color = (15, 23, 42)  # Dark blue
    text_color = (255, 255, 255)
    grid_color = (30, 41, 59)  # Slightly lighter for grid

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Try to use a nice font, fallback to default
    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 56)
        stat_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
        label_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        footer_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except (OSError, IOError):
        title_font = stat_font = label_font = footer_font = ImageFont.load_default()

    # Extract player info
    name = player_data.get("canonical_name", "Player")
    position = ", ".join(player_data.get("positions", ["Unknown"]))
    career_totals = player_data.get("career_totals", {})
    season_totals = player_data.get("season_totals", {})

    # Find top stat
    top_stat_key = None
    top_stat_value = 0
    if career_totals:
        for key, value in career_totals.items():
            try:
                if float(value) > top_stat_value:
                    top_stat_value = float(value)
                    top_stat_key = key
            except (TypeError, ValueError):
                pass

    # Extract league-year timeline
    league_years = {}
    all_years = set()
    for key in season_totals:
        dash = key.rfind("-")
        if dash < 0:
            continue
        yr_str = key[dash + 1:]
        if not yr_str.isdigit():
            continue
        league = key[:dash]
        yr = int(yr_str)
        all_years.add(yr)
        league_years.setdefault(league, set()).add(yr)

    # Draw accent bar at top
    accent_color = (59, 130, 246)
    draw.rectangle([(0, 0), (width, 60)], fill=accent_color)
    draw.text((30, 15), name, font=title_font, fill=text_color)

    # Position and top stat info (left side)
    y_pos = 80
    draw.text((30, y_pos), f"{position}", font=label_font, fill=(180, 180, 180))

    if top_stat_key:
        top_stat_label = top_stat_key.replace("_", " ").title()
        top_stat_display = f"{int(top_stat_value):,}" if top_stat_value == int(top_stat_value) else f"{top_stat_value:,.1f}"
        y_pos += 30
        draw.text((30, y_pos), top_stat_label, font=label_font, fill=text_color)
        y_pos += 25
        draw.text((30, y_pos), top_stat_display, font=stat_font, fill=accent_color)

    # Timeline grid section
    if league_years and all_years:
        timeline_y = 240
        
        # Sort years and leagues
        year_range = sorted(all_years)
        sorted_leagues = sorted(league_years.items(), 
                               key=lambda x: min(x[1]) if x[1] else 9999)

        # Grid parameters
        cell_width = 28
        label_width = 80
        grid_start_x = label_width + 30
        
        # Draw year headers
        year_y = timeline_y
        draw.text((30, year_y), "Career Timeline", font=label_font, fill=text_color)
        year_y += 28
        
        for year in year_range:
            yr_text = f"'{str(year)[-2:]}"
            x = grid_start_x + (year_range.index(year) * cell_width)
            draw.text((x - 8, year_y), yr_text, font=footer_font, fill=(120, 120, 120))
        
        # Draw league rows with colored cells
        league_colors = [
            (74, 222, 128),    # green
            (96, 165, 250),    # blue
            (249, 115, 22),    # orange
            (236, 72, 153),    # pink
            (168, 85, 247),    # purple
            (34, 197, 94),     # darker green
            (59, 130, 246),    # darker blue
            (217, 119, 6),     # darker orange
        ]
        
        row_y = year_y + 25
        for league_idx, (league, years) in enumerate(sorted_leagues):
            color = league_colors[league_idx % len(league_colors)]
            
            # League label
            draw.text((30, row_y + 5), league[:10], font=footer_font, fill=color)
            
            # Draw cells for years
            for year in year_range:
                x = grid_start_x + (year_range.index(year) * cell_width)
                if year in years:
                    # Draw filled cell
                    draw.rectangle([(x, row_y), (x + cell_width - 3, row_y + 18)], 
                                 fill=color, outline=(255, 255, 255))
            
            row_y += 22

    # Footer branding
    footer_y = height - 25
    draw.text((30, footer_y), "AltSports Archive", font=footer_font, fill=(100, 100, 100))
    draw.text((width - 290, footer_y), "archive.altfantasysports.com", font=footer_font, fill=(100, 100, 100))

    # Save image
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG")
    return True




def main():
    env = make_env()

    # ── Load shared data ────────────────────────────────────────────────
    leagues_index = json.loads((DATA_DIR / "leagues" / "index.json").read_text())["leagues"]
    sport_groups = _build_sport_groups(leagues_index)

    this_week_path = DATA_DIR / "this-week.json"
    this_week = json.loads(this_week_path.read_text()) if this_week_path.exists() else None

    # ── Index page ──────────────────────────────────────────────────────
    print("Rendering index ...")
    render(env, "index.html", SITE_DIR / "index.html", root="",
            leagues=leagues_index, sport_groups=sport_groups, this_week=this_week,
            canonical_path="index.html")

    # ── Search page ─────────────────────────────────────────────────────
    print("Rendering search ...")
    render(env, "search.html", SITE_DIR / "search.html", root="",
           canonical_path="search.html")

    # ── Leagues index ───────────────────────────────────────────────────
    print("Rendering leagues index ...")
    render(
        env, "leagues_index.html",
        SITE_DIR / "leagues" / "index.html",
        root="../",
        leagues=leagues_index,
        sport_groups=sport_groups,
        canonical_path="leagues/index.html",
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
        
        # Load coaches for this league if available
        coaches = []
        # Extract base league slug (remove year from slug like "xfl-2023" -> "xfl")
        import re
        base_league_slug = re.sub(r"-\d{4}$", "", league_data['slug'])
        coaches_file = DATA_DIR / "coaches" / f"{base_league_slug}.json"
        if coaches_file.exists():
            try:
                coaches_data = json.loads(coaches_file.read_text())
                coaches = coaches_data.get("coaches", [])
            except (json.JSONDecodeError, KeyError):
                pass
        
        render(
            env, "league.html",
            SITE_DIR / "leagues" / f"{league_data['slug']}.html",
            root="../",
            league=league_data,
            coaches=coaches,
            chart_top10=chart_top10,
            canonical_path=f"leagues/{league_data['slug']}.html",
        )

    # ── Studies ─────────────────────────────────────────────────────────
    studies_dir = DATA_DIR / "studies"
    studies_index_path = studies_dir / "index.json"
    if studies_index_path.exists():
        try:
            studies_index = json.loads(studies_index_path.read_text()).get("studies", [])
        except (json.JSONDecodeError, OSError):
            studies_index = []

        print(f"Rendering studies index + {len(studies_index)} study pages ...")
        render(
            env, "studies_index.html",
            SITE_DIR / "studies" / "index.html",
            root="../",
            studies=studies_index,
            canonical_path="studies/index.html",
        )

        for entry in studies_index:
            slug = entry.get("slug")
            if not slug:
                continue
            study_path = studies_dir / f"{slug}.json"
            if not study_path.exists():
                continue
            try:
                study_data = json.loads(study_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            history_path = studies_dir / f"{slug}-history.json"
            history_data = None
            if history_path.exists():
                try:
                    history_data = json.loads(history_path.read_text())
                except (json.JSONDecodeError, OSError):
                    history_data = None
            render(
                env, "study.html",
                SITE_DIR / "studies" / f"{slug}.html",
                root="../",
                study=study_data,
                history=history_data,
                canonical_path=f"studies/{slug}.html",
            )

    # ── Player pages ────────────────────────────────────────────────────
    player_images_path = DATA_DIR / "player-images.json"
    player_images: dict = (
        json.loads(player_images_path.read_text())
        if player_images_path.exists()
        else {}
    )
    
    # Load coaches merged for is_coach check
    coaches_merged: dict = {}
    coaches_merged_file = MERGED_DIR / "coaches_merged.json"
    if coaches_merged_file.exists():
        try:
            coaches_list = json.loads(coaches_merged_file.read_text())
            coaches_merged = {c.get("canonical_id"): c for c in coaches_list}
        except (json.JSONDecodeError, KeyError):
            pass

    # OG images disabled — not needed for current use case
    # og_images_manifest_path = SITE_DIR / "assets" / "og-images" / ".manifest.json"
    # og_images_generated: set = set()
    # if og_images_manifest_path.exists():
    #     try:
    #         og_images_generated = set(json.loads(og_images_manifest_path.read_text()).get("generated", []))
    #     except (json.JSONDecodeError, KeyError):
    #         pass

    player_files = list((DATA_DIR / "players").glob("*.json"))
    print(f"Rendering {len(player_files)} player pages ...")
    
    # og_images_batch_limit = 50
    # og_images_generated_count = 0
    
    for i, pf in enumerate(player_files):
        if i % 2000 == 0:
            print(f"  ... {i}/{len(player_files)}")
        player_data = json.loads(pf.read_text())
        cid = player_data["canonical_id"]
        img_meta = player_images.get(cid)  # None if no image
        
        # Check if this player is a coach
        is_coach = cid in coaches_merged
        
        # OG image generation disabled
        # og_image_path = SITE_DIR / "assets" / "og-images" / f"{cid}.png"
        # og_image_exists = og_image_path.exists() or (cid in og_images_generated)
        # if cid not in og_images_generated and og_images_generated_count < og_images_batch_limit:
        #     if generate_player_og_image(player_data, og_image_path):
        #         og_images_generated.add(cid)
        #         og_images_generated_count += 1
        #         og_image_exists = True
        og_image_exists = False
        
        render(
            env, "player.html",
            SITE_DIR / "players" / f"{cid}.html",
            root="../",
            player=player_data,
            player_image=img_meta,
            player_timeline=build_player_timeline(player_data),
            is_coach=is_coach,
            og_image_exists=og_image_exists,
            canonical_path=f"players/{cid}.html",
        )
    
    # OG manifest save disabled
    # if og_images_generated_count > 0:
    #     og_images_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    #     og_images_manifest_path.write_text(
    #         json.dumps({"generated": sorted(list(og_images_generated))}, indent=2),
    #         encoding="utf-8"
    #     )
    #     print(f"Generated {og_images_generated_count} OG images ({len(og_images_generated)} total)")


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
            canonical_path=f"hof/{cat}.html",
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
        canonical_path="hof/index.html",
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

    studies_dir = DATA_DIR / "studies"
    studies_index_path = studies_dir / "index.json"
    if studies_index_path.exists():
        try:
            _studies = json.loads(studies_index_path.read_text()).get("studies", [])
        except (json.JSONDecodeError, OSError):
            _studies = []
        if _studies:
            add_url(f"{BASE}/studies/index.html", priority="0.8", changefreq="weekly")
        for _s in _studies:
            _slug = _s.get("slug")
            if _slug and (SITE_DIR / "studies" / f"{_slug}.html").exists():
                add_url(f"{BASE}/studies/{_slug}.html", priority="0.7", changefreq="weekly")

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


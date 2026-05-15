#!/usr/bin/env python3
"""
Scrape CFL and UFL coaching staffs from per-team Wikipedia season pages.

Wikipedia hosts a per-team-season article for every team in both the CFL
(e.g. "2024_BC_Lions_season") and the UFL (e.g. "2025_DC_Defenders_season").
Each of these pages contains a "Coaching staff" section that lists the full
staff (head coach, coordinators, position coaches, etc.) as a flat run of
"<Role> – <Name>" pairs separated by an en-dash (–).

This is much richer than the league-level season page (which usually only
shows the head coach), so we fetch the team-season pages directly.

Outputs:
  pipeline/raw/cfl_ufl_coaches.json     — synthetic coach records
  pipeline/raw/cfl_ufl_coaches_cache.json — Wikipedia HTML cache
  pipeline/raw/cfl_ufl_coaches_state.json — processed-season state

Synthetic coach IDs start at 2_500_000 to avoid colliding with the
football_coaches.json range (which starts at 2_000_000).
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Constants ─────────────────────────────────────────────────────────────
SYNTHETIC_ID_START = 2_500_000
WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "AltSportsArchive/1.0 (https://altdata.archive)"}

RAW = Path(__file__).parent / "raw"
RAW.mkdir(exist_ok=True)
COACHES_FILE = RAW / "cfl_ufl_coaches.json"
CACHE_FILE = RAW / "cfl_ufl_coaches_cache.json"
STATE_FILE = RAW / "cfl_ufl_coaches_state.json"


# ── Team season-page configs ──────────────────────────────────────────────
# (league, year, team_abbr, wiki_page_title)

CFL_TEAMS = [
    ("BC",  "BC_Lions"),
    ("CGY", "Calgary_Stampeders"),
    ("EDM", "Edmonton_Elks"),
    ("HAM", "Hamilton_Tiger-Cats"),
    ("MTL", "Montreal_Alouettes"),
    ("OTT", "Ottawa_Redblacks"),
    ("SSK", "Saskatchewan_Roughriders"),
    ("TOR", "Toronto_Argonauts"),
    ("WPG", "Winnipeg_Blue_Bombers"),
]

# 2020 was canceled (COVID); start at 2021.
CFL_YEARS = [2021, 2022, 2023, 2024, 2025]

UFL_TEAMS = [
    ("ARL", "Arlington_Renegades"),
    ("BHM", "Birmingham_Stallions"),
    ("DC",  "DC_Defenders"),
    ("HOU", "Houston_Roughnecks"),
    ("MEM", "Memphis_Showboats"),
    ("MIC", "Michigan_Panthers"),
    ("SA",  "San_Antonio_Brahmas"),
    ("STL", "St._Louis_Battlehawks"),
]

UFL_YEARS = [2024, 2025]


def build_seasons() -> list[tuple[str, int, str, str]]:
    """Return list of (league, year, team_abbr, wiki_title)."""
    seasons: list[tuple[str, int, str, str]] = []
    for year in CFL_YEARS:
        for abbr, slug in CFL_TEAMS:
            seasons.append(("CFL", year, abbr, f"{year}_{slug}_season"))
    for year in UFL_YEARS:
        for abbr, slug in UFL_TEAMS:
            seasons.append(("UFL", year, abbr, f"{year}_{slug}_season"))
    return seasons


# ── Coach role recognition ────────────────────────────────────────────────
# Order matters: longer / more specific phrases first so regex prefers them.
ROLE_KEYWORDS = [
    "Assistant Head Coach",
    "Head Coach",
    "Offensive Coordinator",
    "Defensive Coordinator",
    "Special Teams Coordinator",
    "Co-Offensive Coordinator",
    "Co-Defensive Coordinator",
    "Passing Game Coordinator",
    "Running Game Coordinator",
    "Run Game Coordinator",
    "Pass Game Coordinator",
    "Quarterbacks Coach",
    "Quarterbacks",
    "Running Backs Coach",
    "Running Backs",
    "Wide Receivers Coach",
    "Wide Receivers",
    "Receivers Coach",
    "Receivers",
    "Tight Ends Coach",
    "Tight Ends",
    "Offensive Line Coach",
    "Offensive Line",
    "Defensive Line Coach",
    "Defensive Line",
    "Linebackers Coach",
    "Linebackers",
    "Defensive Backs Coach",
    "Defensive Backs",
    "Cornerbacks Coach",
    "Cornerbacks",
    "Safeties Coach",
    "Safeties",
    "Special Teams Coach",
    "Special Teams Assistant",
    "Special Teams",
    "Strength and Conditioning",
    "Strength & Conditioning",
    "Assistant Defensive Backs",
    "Assistant Offensive Line",
    "Assistant Linebackers",
    "Assistant Defensive Line",
    "Assistant Special Teams",
    "Assistant Quarterbacks",
    "Assistant Receivers",
    "Assistant Running Backs",
    "Assistant Tight Ends",
    "Offensive Assistant",
    "Defensive Assistant",
    "Offensive Quality Control",
    "Defensive Quality Control",
]

# Sort longest first so regex alternation prefers longer matches.
_ROLE_ALT = "|".join(re.escape(r) for r in sorted(ROLE_KEYWORDS, key=len, reverse=True))

# First word of every role keyword (e.g. "Head", "Offensive", "Running",
# "Wide"). Used to trim a trailing token that's actually the start of the
# next role bleeding into the captured name.
_ROLE_FIRST_WORDS = {kw.split()[0] for kw in ROLE_KEYWORDS}
# Plus a few standalone words that commonly appear as section breaks in the
# Wikipedia coaching cell ("Front Office and Support Staff", "Head Coaches",
# "Offensive Coaches", "Defensive Coaches", "Special Teams Coaches").
_ROLE_FIRST_WORDS.update({"Front", "Coaches", "Co"})

# Match optional "Role/Subrole" before the dash, then up to 4 capitalized
# name tokens after the dash. The role part is matched case-insensitively
# (Wikipedia infoboxes write "Head coach" with a lowercase 'c'); the name
# part keeps its strict capitalization requirement.
PAIR_RE = re.compile(
    r"(?P<role>(?:" + _ROLE_ALT + r")(?:\s*/\s*[A-Za-z][\w\s\-&]*?)?)"
    r"\s+[–-]\s+"
    r"(?P<name>[A-Z][A-Za-z'\.\-]+"
    r"(?:\s+(?:[A-Z][A-Za-z'\.\-]+|Jr\.?|Sr\.?|II|III|IV)){1,2})",
    re.IGNORECASE,
)
# Same alternation, used for trim/cleanup logic.
_ROLE_ALT_RE = re.compile(r"\s+(?:" + _ROLE_ALT + r")\b", re.IGNORECASE)
_ROLE_FIRST_WORDS_LC = {w.lower() for w in _ROLE_FIRST_WORDS}
_ROLE_CANON = {kw.lower(): kw for kw in ROLE_KEYWORDS}


def _canonicalize_role(role: str) -> str:
    """Title-case a captured role, preferring the canonical ROLE_KEYWORDS spelling."""
    parts = re.split(r"\s*/\s*", role.strip())
    out = []
    for p in parts:
        canon = _ROLE_CANON.get(p.lower())
        out.append(canon if canon else p.strip().title())
    return "/".join(out)


def extract_coaches_from_html(html: str, league: str, year: int, team_abbr: str) -> list[dict]:
    """Pull "Role – Name" pairs from the team-season page's Coaching staff cell."""
    soup = BeautifulSoup(html, "html.parser")

    # Find a heading whose text contains "Coaching staff" (case-insensitive),
    # then walk forward until we hit the next heading, collecting text.
    coaches: list[dict] = []
    candidates: list[str] = []

    for header in soup.find_all(["h2", "h3", "h4"]):
        title = header.get_text(" ", strip=True).lower()
        # UFL pages use just "Staff"; CFL pages use "Coaching staff" or
        # "Coaches". Match any of these.
        if not (
            "coaching staff" in title
            or "coaches" in title
            or title.strip() == "staff"
        ):
            continue

        # Collect the text from siblings until the next same-or-higher heading.
        chunks: list[str] = []
        for sib in header.find_all_next():
            if sib.name in ("h2", "h3", "h4") and sib is not header:
                # Stop when we hit another section heading at the same level or higher.
                if sib.name <= header.name:
                    break
            chunks.append(sib.get_text(" ", strip=True))
        candidates.append(" ".join(chunks))

    # If the section walker found nothing, fall back to brute-force scan
    # over the whole article body. (When the walker DOES find something,
    # avoid the second pass — it tends to produce dup captures with one
    # extra token bleeding in.)
    if not candidates:
        candidates.append(soup.get_text(" ", strip=True))

    raw_pairs: list[tuple[str, str]] = []
    for text in candidates:
        # Collapse runs of whitespace to a single space.
        text = re.sub(r"\s+", " ", text)
        for m in PAIR_RE.finditer(text):
            role = _canonicalize_role(m.group("role"))
            name = m.group("name").strip()

            # Truncate the name at the first occurrence of any role keyword
            # bleeding in from the next pair (e.g. "Stephen Sorrells Running
            # Backs" → "Stephen Sorrells").
            cut = _ROLE_ALT_RE.search(" " + name)  # leading space so a name
                                # that *starts* with a role-word isn't mangled
            if cut:
                name = (" " + name)[: cut.start()].strip()

            # Trim trailing tokens that are the first word of a role keyword
            # (catches single-word bleed like "Jason Tucker Running" where
            # only the leading token of "Running Backs" got captured).
            tokens = name.split()
            while len(tokens) >= 2 and tokens[-1].lower() in _ROLE_FIRST_WORDS_LC:
                tokens.pop()
            name = " ".join(tokens)

            # Detect and collapse doubled names ("Jordan Maksymic Jordan
            # Maksymic" → "Jordan Maksymic").
            tokens = name.split()
            n = len(tokens)
            if n >= 4 and n % 2 == 0 and tokens[: n // 2] == tokens[n // 2 :]:
                name = " ".join(tokens[: n // 2])

            # Drop a trailing token that's a repeat of the first token
            # ("Jabari Arthur Jabari" → "Jabari Arthur"). This is bleed from
            # the next entry's first name landing as a stray name token.
            tokens = name.split()
            if len(tokens) >= 3 and tokens[-1] == tokens[0]:
                name = " ".join(tokens[:-1])

            if not name or len(name.split()) < 2:
                continue
            raw_pairs.append((role, name))

    # Dedup: bucket by (role, first_token); within each bucket prefer the
    # shortest cleanest name (fewer trailing junk tokens).
    by_key: dict[tuple[str, str], str] = {}
    for role, name in raw_pairs:
        toks = name.split()
        key = (role, toks[0])
        prev = by_key.get(key)
        if prev is None or len(name) < len(prev):
            by_key[key] = name

    for (role, _), name in by_key.items():
        coaches.append({
            "name": name,
            "role": role,
            "team": team_abbr,
            "league": league,
            "_year": year,
            "_source": "wikipedia_team_season",
        })

    return coaches


# ── Wikipedia fetch ───────────────────────────────────────────────────────
def fetch_wiki_html(page_title: str, cache: dict, reset: bool = False) -> str | None:
    if not reset and page_title in cache:
        return cache[page_title]

    params = {
        "action": "parse",
        "page": page_title,
        "format": "json",
        "prop": "text",
        "redirects": 1,
    }
    try:
        print(f"  Fetching {page_title} …")
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR: {e}")
        return None

    data = r.json()
    if "error" in data:
        print(f"  API error: {data['error'].get('info', 'unknown')}")
        cache[page_title] = ""  # negative cache so we don't retry forever
        return None

    html = data.get("parse", {}).get("text", {}).get("*", "")
    cache[page_title] = html
    return html


# ── Main ──────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Ignore cache, fetch fresh")
    parser.add_argument("--cache-only", action="store_true", help="Use cached pages only")
    parser.add_argument("--limit", type=int, help="Limit to N team-seasons (for testing)")
    args = parser.parse_args()

    cache: dict = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    state: dict = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"processed": []}
    processed: set[str] = set(state.get("processed", []))

    seasons = build_seasons()
    if args.limit:
        seasons = seasons[: args.limit]

    all_coaches: list[dict] = []
    synthetic_id = SYNTHETIC_ID_START

    for league, year, team_abbr, wiki_title in seasons:
        season_key = f"{league}_{year}_{team_abbr}"

        if args.cache_only and wiki_title not in cache:
            print(f"Skipping {season_key} (not cached)")
            continue

        html = fetch_wiki_html(wiki_title, cache, reset=args.reset)
        if not html:
            continue

        coaches = extract_coaches_from_html(html, league, year, team_abbr)
        print(f"  {season_key}: {len(coaches)} coach entries")

        for c in coaches:
            name = c["name"]
            parts = name.split()
            record = {
                "id": synthetic_id,
                "full_name": name,
                "short_name": name,
                "first_name": parts[0] if parts else "",
                "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
                "position": c["role"],
                "team": c["team"],
                "sport_id": None,
                "league": c["league"],
                "jersey": None,
                "college": None,
                "height": None,
                "weight": None,
                "is_coach": True,
                "_year": c["_year"],
                "_source": c["_source"],
                "_source_url": f"https://en.wikipedia.org/wiki/{wiki_title}",
            }
            all_coaches.append(record)
            synthetic_id += 1

        processed.add(season_key)

        if not args.cache_only:
            time.sleep(0.5)  # be polite to Wikipedia

    # Persist outputs.
    COACHES_FILE.write_text(json.dumps(all_coaches, indent=2), encoding="utf-8")
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    STATE_FILE.write_text(
        json.dumps({"processed": sorted(processed)}, indent=2),
        encoding="utf-8",
    )

    # Summary by league.
    by_league: dict[str, int] = {}
    for c in all_coaches:
        by_league[c["league"]] = by_league.get(c["league"], 0) + 1
    print(f"\nWrote {len(all_coaches)} coach records to {COACHES_FILE}")
    for league, n in sorted(by_league.items()):
        print(f"  {league}: {n}")


if __name__ == "__main__":
    main()

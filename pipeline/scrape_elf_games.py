"""
scrape_elf_games.py
~~~~~~~~~~~~~~~~~~~

Scrape the full ELF (European League of Football) per-game schedule from
https://europeanleague.football/games/schedule .

The Next.js App Router payload embeds the entire historical + current schedule
as a single JSON array under `self.__next_f.push` chunks. We decode the chunks,
locate the game array, and emit `pipeline/raw/elf_games.json` in the same shape
as `efa_games.json` so `build_data.py`'s football-games seed loop can surface
the schedule on each league-year page.

Fields per game (matching efa_games.json):
    game_id, sport_id, league, season, _year, week, start_date, home_team,
    away_team, home_score, away_score, completed, venue, _elf_slug
"""

from __future__ import annotations

import codecs
import json
import re
import sys
import urllib.request
from pathlib import Path

URL  = "https://europeanleague.football/games/schedule"
HDRS = {"User-Agent": "Mozilla/5.0 (altdata-archive scraper)"}
RAW  = Path(__file__).parent / "raw"
RAW.mkdir(parents=True, exist_ok=True)

CACHE = RAW / "elf_games_schedule.html"
OUT   = RAW / "elf_games.json"


def fetch_html() -> str:
    if CACHE.exists() and CACHE.stat().st_size > 50_000:
        return CACHE.read_text(encoding="utf-8")
    req = urllib.request.Request(URL, headers=HDRS)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", errors="replace")
    CACHE.write_text(body, encoding="utf-8")
    return body


_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
_ID_RE    = re.compile(r'"_id":"[a-f0-9]{24}"')


def _extract_object_at(text: str, start: int) -> str | None:
    """Return the JSON-object substring that starts at the '{' preceding `start`.

    Scans backward to find the opening brace, then walks forward tracking
    string and brace state to find the matching close.
    """
    i = start
    while i > 0 and text[i] != "{":
        i -= 1
    if text[i] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    j = i
    while j < len(text):
        ch = text[j]
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif in_str:
            if ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[i:j + 1]
        j += 1
    return None


def extract_games(html: str) -> list[dict]:
    chunks = _CHUNK_RE.findall(html)
    if not chunks:
        return []
    big = max(chunks, key=len)
    text = codecs.decode(big, "unicode_escape")
    out: list[dict] = []
    seen_ids: set[str] = set()
    for m in _ID_RE.finditer(text):
        blob = _extract_object_at(text, m.start())
        if not blob:
            continue
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        # Filter: must look like a game (has slug + gamename or gamedate)
        if not isinstance(obj, dict):
            continue
        if "slug" not in obj or ("gamename" not in obj and "gamedate" not in obj):
            continue
        _id = obj.get("_id")
        if _id in seen_ids:
            continue
        seen_ids.add(_id)
        out.append(obj)
    return out


def normalize(games: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for g in games:
        season = g.get("Season")
        try:
            season = int(season) if season is not None else None
        except (TypeError, ValueError):
            season = None
        slug = (g.get("slug") or "").strip()
        if not slug:
            continue
        game_id = f"FOOTBALL_ELF_{season}_{slug}" if season else f"FOOTBALL_ELF_{slug}"
        away_score = g.get("awayScore")
        home_score = g.get("homeScore")
        completed = isinstance(away_score, (int, float)) and isinstance(home_score, (int, float)) \
            and (away_score or home_score)
        rows.append({
            "game_id":     game_id,
            "sport_id":    None,
            "league":      "ELF",
            "season":      season,
            "_year":       season,
            "week":        str(g.get("gameweek") or "").strip(),
            "start_date":  g.get("gamedate") or g.get("date") or "",
            "home_team":   g.get("homename") or "",
            "away_team":   g.get("awayname") or "",
            "home_score":  home_score if isinstance(home_score, (int, float)) else None,
            "away_score":  away_score if isinstance(away_score, (int, float)) else None,
            "completed":   bool(completed),
            "venue":       g.get("Location") or g.get("stadium") or "",
            "_elf_slug":   slug,
            "_elf_id":     g.get("_id"),
            "_elf_gametype": g.get("gametype") or "",
        })
    return rows


def main() -> int:
    html = fetch_html()
    raw_games = extract_games(html)
    if not raw_games:
        print("[ELF] no games extracted; bailing", file=sys.stderr)
        return 1
    rows = normalize(raw_games)
    OUT.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    by_season: dict = {}
    for r in rows:
        by_season[r["season"]] = by_season.get(r["season"], 0) + 1
    print(f"[ELF] wrote {len(rows)} games to {OUT}")
    for s in sorted(k for k in by_season if k is not None):
        print(f"  {s}: {by_season[s]} games")
    return 0


if __name__ == "__main__":
    sys.exit(main())

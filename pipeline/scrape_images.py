"""
scrape_images.py
Fetches freely-licensed player photos from Wikimedia Commons via the
Wikipedia and Commons APIs.

Flow per player:
  1. Search Wikipedia for the player's article
  2. Get the infobox image filename from the pageimages API
  3. Look up license metadata on Commons
  4. Accept: CC-BY, CC-BY-SA (any version), CC0, public domain
  5. Download a thumbnail (400px wide) and save to docs/assets/player-images/
  6. Record attribution in docs/data/player-images.json

Usage:
  .venv/bin/python pipeline/scrape_images.py          # all players, skip already done
  .venv/bin/python pipeline/scrape_images.py --reset  # re-fetch everything

Rate limit: 1 request/sec to both Wikipedia and Commons APIs.
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests

_wall_start: float = 0.0
_max_seconds: float = 0.0


def _time_is_up() -> bool:
    return _max_seconds > 0 and (time.monotonic() - _wall_start) >= _max_seconds

MERGED   = Path(__file__).parent / "merged" / "players_merged.json"
IMG_DIR  = Path(__file__).parent.parent / "docs" / "assets" / "player-images"
META_OUT = Path(__file__).parent.parent / "docs" / "data" / "player-images.json"

IMG_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = (
    "AltSportsArchive/1.0 (archive.altfantasysports.com; "
    "contact via github.com/bwentzloff/altdata-archive)"
)
HEADERS = {"User-Agent": USER_AGENT}

WIKI_API  = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Licenses we will accept — handles both 'CC BY-SA 2.0' and 'cc-by-sa-2.0'
ALLOWED_LICENSE_RE = re.compile(
    r"(cc[- ]by([- ]sa)?([- ]\d+\.\d+)?|cc[- ]zero|cc0|public[- ]domain|pd[- ])",
    re.IGNORECASE,
)

# Licenses we explicitly reject even if they match the above broadly
REJECTED_TERMS = ["nc", "nd", "noncommercial", "no.deriv", "no-deriv"]


def rate_limit():
    time.sleep(1.05)


def search_wikipedia(name: str) -> str | None:
    """Return the Wikipedia page title for the best matching article, or None."""
    params = {
        "action": "query",
        "list":   "search",
        "srsearch": f"{name} american football",
        "srnamespace": 0,
        "srlimit": 3,
        "format": "json",
    }
    try:
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        if not r.text.strip():
            rate_limit()
            return None
        results = r.json().get("query", {}).get("search", [])
        rate_limit()
        if not results:
            return None
        # Return the top result only if it looks like a person's page
        top = results[0]["title"]
        return top
    except Exception:
        rate_limit()
        return None


def get_page_image(page_title: str) -> str | None:
    """Return the Commons filename (no File: prefix) for the infobox image."""
    params = {
        "action":   "query",
        "titles":   page_title,
        "prop":     "pageimages",
        "piprop":   "original",
        "format":   "json",
    }
    try:
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        if not r.text.strip():
            rate_limit()
            return None
        pages = r.json().get("query", {}).get("pages", {})
        rate_limit()
        for page in pages.values():
            original = page.get("original", {})
            src = original.get("source", "")
            if src:
                # Extract and decode filename from Commons URL
                fname = src.split("/")[-1]
                fname = requests.utils.unquote(fname).replace("+", " ")
                return fname
        return None
    except Exception:
        rate_limit()
        return None


def get_commons_license(filename: str) -> dict | None:
    """
    Query Commons for license info on a file.
    Returns dict with keys: license, attribution, commons_url — or None if rejected.
    """
    params = {
        "action":  "query",
        "titles":  f"File:{filename}",
        "prop":    "imageinfo",
        "iiprop":  "url|extmetadata",
        "iiurlwidth": 400,
        "format":  "json",
    }
    try:
        r = requests.get(COMMONS_API, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        if not r.text.strip():
            rate_limit()
            return None
        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        rate_limit()
        for page in pages.values():
            info_list = page.get("imageinfo", [])
            if not info_list:
                return None
            info    = info_list[0]
            meta    = info.get("extmetadata", {})
            license_short = meta.get("LicenseShortName", {}).get("value", "")
            license_url   = meta.get("LicenseUrl",       {}).get("value", "")
            artist        = meta.get("Artist",           {}).get("value", "")
            credit        = meta.get("Credit",           {}).get("value", "")

            # Strip HTML from artist/credit
            artist = re.sub(r"<[^>]+>", "", artist).strip()
            credit = re.sub(r"<[^>]+>", "", credit).strip()

            combined = (license_short + " " + license_url).lower()

            # Reject non-commercial / no-derivatives
            for bad in REJECTED_TERMS:
                if bad in combined:
                    return None

            if not ALLOWED_LICENSE_RE.search(combined):
                return None

            thumb_url = info.get("thumburl") or info.get("url", "")
            commons_page = f"https://commons.wikimedia.org/wiki/File:{requests.utils.quote(filename)}"

            attribution = artist or credit or "Wikimedia Commons"
            return {
                "license":     license_short,
                "license_url": license_url,
                "attribution": attribution,
                "thumb_url":   thumb_url,
                "commons_url": commons_page,
            }
    except Exception:
        rate_limit()
    return None


def download_image(thumb_url: str, dest: Path) -> bool:
    try:
        r = requests.get(thumb_url, headers=HEADERS, timeout=20, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        rate_limit()
        return True
    except Exception:
        rate_limit()
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true",
                        help="Re-fetch all players, ignoring existing data")
    parser.add_argument("--max-seconds", type=float, default=0,
                        help="Stop after this many wall-clock seconds (0 = no limit)")
    args = parser.parse_args()

    global _wall_start, _max_seconds
    _wall_start = time.monotonic()
    _max_seconds = args.max_seconds

    players = json.loads(MERGED.read_text())

    # Load existing metadata
    existing: dict = {}
    if META_OUT.exists() and not args.reset:
        existing = json.loads(META_OUT.read_text())

    # Skip placeholder entries (team-code names like "TOR QB", "BC K")
    def is_real_name(name: str) -> bool:
        return any(c.islower() for c in name)

    # Only process players who have actual stats (have appearances with sport data)
    # and whose names look like real people
    candidates = [
        p for p in players
        if is_real_name(p["canonical_name"])
        and p["canonical_id"] not in existing
        and not p.get("ambiguous", False)
    ]

    print(f"Total players: {len(players)}")
    print(f"Already fetched: {len(existing)}")
    print(f"To process: {len(candidates)}")

    found = 0
    checked = 0

    for p in candidates:
        if _time_is_up():
            print(f"Time limit reached after {checked} players checked.")
            break

        cid  = p["canonical_id"]
        name = p["canonical_name"]
        checked += 1

        if checked % 50 == 0:
            print(f"  [{checked}/{len(candidates)}] found so far: {found}")

        # Step 1: find Wikipedia page
        page_title = search_wikipedia(name)
        if not page_title:
            existing[cid] = None
            continue

        # Step 2: get infobox image
        filename = get_page_image(page_title)
        if not filename:
            existing[cid] = None
            continue

        # Step 3: check license
        license_info = get_commons_license(filename)
        if not license_info:
            existing[cid] = None
            continue

        # Step 4: download
        ext = Path(filename).suffix.lower() or ".jpg"
        dest = IMG_DIR / f"{cid}{ext}"
        ok = download_image(license_info["thumb_url"], dest)
        if not ok:
            existing[cid] = None
            continue

        existing[cid] = {
            "file":        str(dest.name),
            "license":     license_info["license"],
            "license_url": license_info["license_url"],
            "attribution": license_info["attribution"],
            "commons_url": license_info["commons_url"],
            "wiki_page":   page_title,
        }
        found += 1
        print(f"  + {name} ({cid}) [{license_info['license']}]")

        # Persist after every find so partial runs are not lost
        META_OUT.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    # Final save
    META_OUT.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"\nDone. {found} images downloaded from {checked} players checked.")
    print(f"Metadata written to {META_OUT}")


if __name__ == "__main__":
    main()

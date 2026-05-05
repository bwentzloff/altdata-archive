#!/usr/bin/env python3
"""
scrape_player_socials.py — Gradual social media enrichment for players

Tries multiple trusted sources (in order):
  1. ESPN player profiles (Twitter/X, Instagram, etc.)
  2. Official league websites (NFL, NBA, CFL, etc.)
  3. Wikipedia player pages (links to official profiles)

Runs slowly in chunks (--batch 10) to avoid overwhelming APIs.
Caches fetched profiles to avoid re-scraping.
Updates docs/data/players/{canonical-id}.json with social_media field.

Field format:
  "social_media": {
    "twitter": "@handle",
    "instagram": "@handle",
    "facebook": "facebook-url",
    ...
    "_source": "espn|league|wikipedia|none",
    "_checked": "2026-05-04T14:30:00Z"
  }
"""

import argparse
import json
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
DOCS = BASE.parent / "docs"
PLAYERS_DIR = DOCS / "data" / "players"
RAW = BASE / "raw"
RAW.mkdir(exist_ok=True)

CACHE_FILE = RAW / "player_socials_cache.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# Social media handle patterns
SOCIAL_PATTERNS = {
    "twitter": [
        r"twitter\.com/([a-zA-Z0-9_]+)",
        r"x\.com/([a-zA-Z0-9_]+)",
        r"@([a-zA-Z0-9_]+)(?:\s|$|\")",
    ],
    "instagram": [r"instagram\.com/([a-zA-Z0-9_.]+)"],
    "facebook": [r"facebook\.com/([a-zA-Z0-9.-]+)"],
    "tiktok": [r"tiktok\.com/@([a-zA-Z0-9_.-]+)"],
    "youtube": [r"youtube\.com/@?([a-zA-Z0-9_-]+)"],
}


def load_cache():
    """Load existing cache of scraped players."""
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache):
    """Save cache to disk."""
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def extract_handles(text, player_name=""):
    """Extract social media handles from text using regex patterns."""
    if not text:
        return {}

    handles = {}
    for platform, patterns in SOCIAL_PATTERNS.items():
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                # Take first match
                handle = matches[0].lower()
                # Filter out common false positives
                if len(handle) > 2 and handle not in ["com", "en", "www"]:
                    handles[platform] = handle
                    break

    return handles


def scrape_espn(player_name, player_team=""):
    """Try to find player on ESPN and extract social handles."""
    try:
        # Search for player on ESPN
        search_url = f"https://www.espn.com/search?query={player_name.replace(' ', '+')}"
        r = requests.get(search_url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "html.parser")

        # Look for player profile links (NFL or general sports)
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if ("/player/" in href or "/athlete/" in href) and player_name.lower() in link.get_text().lower():
                # Found a potential player profile
                profile_url = urljoin("https://www.espn.com", href)
                profile_r = requests.get(
                    profile_url, headers=HEADERS, timeout=10
                )
                if profile_r.status_code == 200:
                    profile_soup = BeautifulSoup(profile_r.text, "html.parser")
                    
                    # Look for social media links in multiple places
                    # 1. Dedicated social section
                    social_section = profile_soup.find(
                        "div", class_=re.compile("social|follow|share", re.I)
                    )
                    if social_section:
                        handles = extract_handles(
                            social_section.get_text() + str(social_section.find_all("a"))
                        )
                        if handles:
                            return handles
                    
                    # 2. Look for social links anywhere on the page
                    all_links = profile_soup.find_all("a", href=True)
                    all_hrefs = [link.get("href", "") for link in all_links]
                    handles = extract_handles(" ".join(all_hrefs))
                    if handles:
                        return handles

        return {}
    except Exception as e:
        # Silent fail — ESPN might be blocking or timing out
        return {}


def scrape_league_site(player_name, league="NFL"):
    """Try to find player on official league website."""
    try:
        league_urls = {
            "NFL": "https://www.nfl.com/search?q=",
            "NBA": "https://www.nba.com/search?query=",
            "CFL": "https://www.cfl.ca/search?q=",
        }

        url = league_urls.get(league, "")
        if not url:
            return {}

        search_url = url + player_name.replace(" ", "+")
        r = requests.get(search_url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "html.parser")

        # Extract all text and look for social links
        all_text = soup.get_text() + str(soup.find_all("a"))
        handles = extract_handles(all_text, player_name)

        return handles
    except Exception:
        return {}


def scrape_wikidata(player_name):
    """Try to find player on Wikidata and extract social handles."""
    try:
        # Search Wikidata via SPARQL
        search_url = f"https://www.wikidata.org/w/api.php?action=query&list=search&srsearch={player_name.replace(' ', '+')}&format=json"
        r = requests.get(search_url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}

        data = r.json()
        search_results = data.get("query", {}).get("search", [])

        if not search_results:
            return {}

        # Get first result (should be the player)
        first_result = search_results[0]
        entity_id = first_result.get("title", "")

        if not entity_id:
            return {}

        # Fetch entity details from Wikidata API
        entity_url = f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
        entity_r = requests.get(entity_url, headers=HEADERS, timeout=10)
        if entity_r.status_code != 200:
            return {}

        entity_data = entity_r.json()
        entities = entity_data.get("entities", {})
        
        if not entities:
            return {}

        entity = list(entities.values())[0]
        claims = entity.get("claims", {})

        handles = {}
        # P2003 = Twitter, P2013 = Facebook, P3185 = YouTube, etc.
        handle_mapping = {
            "P2003": "twitter",      # Twitter username
            "P2013": "facebook",     # Facebook ID
            "P3185": "youtube",      # YouTube ID
            "P4003": "tiktok",       # TikTok ID
        }

        for wikidata_prop, platform in handle_mapping.items():
            if wikidata_prop in claims:
                claim = claims[wikidata_prop][0]
                # Navigate the nested structure: mainsnak.datavalue.value
                if "mainsnak" in claim:
                    mainsnak = claim["mainsnak"]
                    if "datavalue" in mainsnak:
                        value = mainsnak["datavalue"].get("value", "")
                        if value and platform not in handles:
                            handles[platform] = value

        return handles
    except Exception:
        return {}


def scrape_pfr(player_name):
    """Try to find player on Pro Football Reference and extract social handles."""
    try:
        # Search PFR
        search_url = f"https://www.pro-football-reference.com/search/search.fcgi?search={player_name.replace(' ', '+')}"
        r = requests.get(search_url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "html.parser")

        # Look for player links in search results
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if "/players/" in href:
                # Found a potential player profile
                profile_url = urljoin("https://www.pro-football-reference.com", href)
                profile_r = requests.get(
                    profile_url, headers=HEADERS, timeout=10
                )
                if profile_r.status_code == 200:
                    profile_soup = BeautifulSoup(profile_r.text, "html.parser")
                    
                    # Look for social media links in the page
                    # PFR typically has social links in the player info section
                    info_section = profile_soup.find("div", id=re.compile("info|player-meta", re.I))
                    if info_section:
                        handles = extract_handles(
                            info_section.get_text() + str(info_section.find_all("a"))
                        )
                        if handles:
                            return handles
                    
                    # Also look for any social links on the page
                    all_links = profile_soup.find_all("a", href=True)
                    all_hrefs = [link.get("href", "") for link in all_links]
                    handles = extract_handles(" ".join(all_hrefs))
                    if handles:
                        return handles

        return {}
    except Exception:
        return {}


def scrape_wikipedia(player_name):
    """Try to find player on Wikipedia and extract social links."""
    try:
        # Search Wikipedia
        search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={player_name.replace(' ', '+')}&format=json"
        r = requests.get(search_url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}

        data = r.json()
        search_results = data.get("query", {}).get("search", [])

        if not search_results:
            return {}

        # Get first result
        first_result = search_results[0]
        page_title = first_result.get("title", "")

        # Fetch the page
        page_url = f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"
        page_r = requests.get(page_url, headers=HEADERS, timeout=10)
        if page_r.status_code != 200:
            return {}

        soup = BeautifulSoup(page_r.text, "html.parser")

        # Look for Infobox with social links
        infobox = soup.find("table", class_=re.compile("infobox", re.I))
        if infobox:
            all_links = infobox.find_all("a", href=True)
            for link in all_links:
                href = link.get("href", "")
                for platform in SOCIAL_PATTERNS:
                    if platform in href:
                        handles = extract_handles(href, player_name)
                        if handles:
                            return handles

        # Also check external links section
        ext_links = soup.find("div", id="External_links")
        if ext_links:
            handles = extract_handles(ext_links.get_text())
            if handles:
                return handles

        return {}
    except Exception:
        return {}


def fetch_social_handles(player_name, player_id=""):
    """Try multiple sources to find social media handles for a player."""
    handles = {}

    # Try each source in order of likelihood to have good coverage
    for source_name, source_func in [
        ("espn", scrape_espn),
        ("pfr", scrape_pfr),
        ("wikidata", scrape_wikidata),
        ("wikipedia", scrape_wikipedia),
        ("league", lambda pn: scrape_league_site(pn, "NFL")),
    ]:
        handles = source_func(player_name)
        if handles:
            return handles, source_name
        time.sleep(0.5)  # Polite rate limiting

    # If nothing found, mark as checked
    return {}, "none"


def get_all_players():
    """Get list of all player JSON files to potentially enrich."""
    if not PLAYERS_DIR.exists():
        return []

    return sorted(PLAYERS_DIR.glob("*.json"))


def is_name_too_vague(player_name):
    """Check if a name is too vague to match reliably (e.g., 'A. Fitzpatrick')."""
    if not player_name:
        return True
    parts = player_name.strip().split()
    if not parts:
        return True
    # If first name is a single character or initial (with or without dot), it's too vague
    first_part = parts[0].rstrip(".")
    return len(first_part) <= 1


def update_player_socials(cache, batch_limit=0):
    """Update player JSON files with social media handles."""
    player_files = get_all_players()
    print(f"Found {len(player_files)} player files to check")

    processed = 0
    updated = 0

    for player_file in player_files:
        if batch_limit and processed >= batch_limit:
            break

        canonical_id = player_file.stem

        # Skip if already processed
        if canonical_id in cache:
            continue

        try:
            player_data = json.loads(player_file.read_text(encoding="utf-8"))
            player_name = player_data.get("canonical_name") or player_data.get("name", "")

            if not player_name:
                cache[canonical_id] = {"_source": "none", "_checked": datetime.now(timezone.utc).isoformat()}
                processed += 1
                continue

            # Skip names that are too vague (e.g., "A. Fitzpatrick")
            if is_name_too_vague(player_name):
                cache[canonical_id] = {"_source": "skipped", "_checked": datetime.now(timezone.utc).isoformat()}
                processed += 1
                continue

            print(f"  [{processed + 1}] {player_name} … ", end="", flush=True)

            handles, source = fetch_social_handles(player_name, canonical_id)

            if handles:
                # Update player record
                player_data["social_media"] = {
                    **handles,
                    "_source": source,
                    "_checked": datetime.now(timezone.utc).isoformat(),
                }
                player_file.write_text(json.dumps(player_data, indent=2), encoding="utf-8")
                print(f"✓ {source}: {', '.join(handles.keys())}")
                updated += 1
            else:
                print("— no handles found")

            # Cache this attempt
            cache[canonical_id] = {
                "_source": source,
                "_checked": datetime.now(timezone.utc).isoformat(),
            }

            processed += 1

        except Exception as e:
            print(f"✗ ERROR: {e}")
            processed += 1

    return processed, updated


def main():
    ap = argparse.ArgumentParser(
        description="Scrape social media handles from trusted sources (ESPN, leagues, Wikipedia)"
    )
    ap.add_argument(
        "--reset", action="store_true", help="Clear cache and re-scrape all players"
    )
    ap.add_argument(
        "--batch", type=int, default=0, help="Max players to process per run (0 = unlimited)"
    )
    args = ap.parse_args()

    print("Scraping player social media profiles …")
    print()

    cache = {} if args.reset else load_cache()
    print(f"Cache: {len(cache)} players already checked")

    processed, updated = update_player_socials(cache, batch_limit=args.batch)
    save_cache(cache)

    print(f"\nProcessed {processed} players, updated {updated} with social handles")
    print(f"Cache now has {len(cache)} entries")


if __name__ == "__main__":
    main()

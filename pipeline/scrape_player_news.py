#!/usr/bin/env python3
"""
Player News & Articles Scraper
Collects news and article links for players from Google News RSS feeds.
Uses league/topic search queries and player name matching.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

try:
    import feedparser
except ImportError:
    print("Error: feedparser not installed. Install with: pip install feedparser")
    sys.exit(1)

GOOGLE_NEWS_BASE = "https://news.google.com/rss/search"

# News queries focused on currently indexed non-AU leagues/sports.
GOOGLE_NEWS_QUERIES = {
    "football_alt": '"UFL" OR "XFL" OR "USFL" OR "CFL" OR "ELF" OR "IFL" OR "NAL" OR "X-League" OR "AAF" OR "AF1"',
    "lacrosse_alt": '"NLL" OR "PLL" OR "box lacrosse" OR "premier lacrosse"',
    "ultimate_alt": '"UFA ultimate" OR "AUDL" OR "PUL" OR "ultimate frisbee pro"',
    "basketball_alt": '"BIG3" OR "SlamBall" OR "3-on-3 basketball"',
    "disc_golf": '"DGPT" OR "Disc Golf Pro Tour"',
}

# Domain scoring for lightweight source quality signal.
HIGH_TRUST_DOMAINS = {
    "cfl.ca",
    "ufl.com",
    "xfl.com",
    "theufl.com",
    "nll.com",
    "premierlacrosseleague.com",
    "stats.premierlacrosseleague.com",
    "big3.com",
    "discgolfprotour.com",
    "pdga.com",
    "usaultimate.org",
    "watchufa.com",
}

MID_TRUST_DOMAINS = {
    "espn.com",
    "apnews.com",
    "reuters.com",
    "sports.yahoo.com",
    "theathletic.com",
    "cbssports.com",
    "foxsports.com",
    "si.com",
    "profootballnetwork.com",
}


class PlayerNewsCollector:
    """Collects and processes news articles for players."""

    def __init__(
        self,
        output_file: Path = Path("pipeline/raw/articles_raw.json"),
        entries_per_feed: int = 100,
    ):
        self.output_file = output_file
        self.entries_per_feed = entries_per_feed
        self.articles: List[Dict[str, Any]] = []
        self.seen_keys: set[tuple[str, str, str]] = set()
        self.stats = {
            "feeds_processed": 0,
            "feeds_successful": 0,
            "feeds_failed": 0,
            "total_articles": 0,
            "articles_with_matches": 0,
            "unique_players_matched": set(),
        }

    def build_google_news_url(self, query: str) -> str:
        params = (
            f"q={quote_plus(query)}"
            "&hl=en-US"
            "&gl=US"
            "&ceid=US:en"
        )
        return f"{GOOGLE_NEWS_BASE}?{params}"

    def domain_trust(self, link: str) -> float:
        host = (urlparse(link).netloc or "").lower()
        host = host[4:] if host.startswith("www.") else host
        if host in HIGH_TRUST_DOMAINS:
            return 3.0
        if host in MID_TRUST_DOMAINS:
            return 2.0
        return 1.0

    def fetch_feed(self, feed_key: str, query: str) -> Optional[List[Dict]]:
        """Fetch and parse RSS feed, return list of articles."""
        feed_url = self.build_google_news_url(query)
        print(f"  Fetching Google News query '{feed_key}'...", end=" ")
        try:
            feed = feedparser.parse(feed_url)
            
            if feed.bozo:
                print("parse warning (continuing)")
            else:
                print("ok")
            
            articles = []
            for entry in feed.entries[: self.entries_per_feed]:
                link = entry.get("link", "")
                trust = self.domain_trust(link)
                article = {
                    "source": feed_key,
                    "source_name": "Google News",
                    "source_type": "google_news_rss",
                    "query": query,
                    "trust_level": trust,
                    "title": entry.get("title", "Untitled"),
                    "link": link,
                    "date": self._parse_date(entry),
                    "summary": entry.get("summary", "")[:500] if entry.get("summary") else "",
                    "cached_text": entry.get("content", [{}])[0].get("value", "")[:1000] if entry.get("content") else "",
                    "indexed_at": datetime.now().isoformat(),
                }
                articles.append(article)
            
            self.stats["feeds_successful"] += 1
            return articles
            
        except Exception as e:
            print(f"✗ Error: {e}")
            self.stats["feeds_failed"] += 1
            return None

    def _parse_date(self, entry: Dict) -> str:
        """Extract and normalize publication date from feed entry."""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6])
            return dt.strftime("%Y-%m-%d")
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            dt = datetime(*entry.updated_parsed[:6])
            return dt.strftime("%Y-%m-%d")
        return datetime.now().strftime("%Y-%m-%d")
    
    def match_players(self, article: Dict, player_names: List[str]) -> List[str]:
        """Find matching player names in article title/summary."""
        matches = []
        article_text = (article["title"] + " " + article["summary"]).lower()
        
        for player in player_names:
            # Simple name matching: check if player name appears in article
            if player.lower() in article_text:
                matches.append(player)
                # Confidence: high if in title, lower if only in summary
                confidence = 0.95 if player.lower() in article["title"].lower() else 0.75
                article["confidence"] = confidence
        
        return matches

    def is_duplicate(self, article: Dict[str, Any]) -> bool:
        title = re.sub(r"\s+", " ", (article.get("title") or "").strip().lower())
        link = (article.get("link") or "").strip().lower()
        date = (article.get("date") or "").strip()
        key = (title, link, date)
        if key in self.seen_keys:
            return True
        self.seen_keys.add(key)
        return False
    
    def run(self):
        """Main collection process."""
        print("=" * 60)
        print("Player News Collection - Google News RSS")
        print("=" * 60)
        
        # Load list of all player names from individual player files
        print("\nLoading player list...")
        try:
            from pathlib import Path
            players_dir = Path("docs/data/players")
            player_names = set()
            
            if players_dir.exists():
                for player_file in players_dir.glob("*.json"):
                    try:
                        with open(player_file) as f:
                            player_data = json.load(f)
                            canonical_name = player_data.get("canonical_name")
                            if canonical_name:
                                player_names.add(canonical_name)
                    except:
                        pass
            
            player_names = sorted(list(player_names))
            print(f"  Loaded {len(player_names)} player names")
        except Exception as e:
            print(f"Error loading players: {e}")
            return
        
        # Fetch from Google News query feeds
        print(f"\nScraping {len(GOOGLE_NEWS_QUERIES)} Google News queries:")
        for feed_key, query in GOOGLE_NEWS_QUERIES.items():
            self.stats["feeds_processed"] += 1
            articles = self.fetch_feed(feed_key, query)
            
            if articles:
                for article in articles:
                    if self.is_duplicate(article):
                        continue

                    # Try to match player names
                    matches = self.match_players(article, player_names)
                    if matches:
                        article["players_matched"] = matches
                        self.stats["articles_with_matches"] += 1
                        for player in matches:
                            self.stats["unique_players_matched"].add(player)
                    
                    self.articles.append(article)
                    self.stats["total_articles"] += 1
        
        # Save to articles_raw.json
        self._save_articles()
        self._print_report()
    
    def _save_articles(self):
        """Save collected articles to output file."""
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        
        output_data = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "sources_count": len(GOOGLE_NEWS_QUERIES),
                "source_type": "google_news_rss",
                "total_articles": self.stats["total_articles"],
                "articles_with_player_matches": self.stats["articles_with_matches"],
                "unique_players_matched": len(self.stats["unique_players_matched"]),
            },
            "articles": self.articles,
        }
        
        with open(self.output_file, "w") as f:
            json.dump(output_data, f, indent=2)
        
        print(f"\n✓ Saved {len(self.articles)} articles to {self.output_file}")
    
    def _print_report(self):
        """Print collection statistics."""
        print("\n" + "=" * 60)
        print("SCRAPE SUMMARY")
        print("=" * 60)
        print(f"Feeds processed:        {self.stats['feeds_processed']}")
        print(f"Feeds successful:       {self.stats['feeds_successful']} ✓")
        print(f"Feeds failed:           {self.stats['feeds_failed']} ✗")
        print(f"Total articles:         {self.stats['total_articles']}")
        print(f"Articles with matches:  {self.stats['articles_with_matches']}")
        print(f"Unique players matched: {len(self.stats['unique_players_matched'])}")
        print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect player news from Google News RSS")
    parser.add_argument("--entries-per-feed", type=int, default=100, help="Max RSS entries to process per query")
    parser.add_argument("--output", default="pipeline/raw/articles_raw.json", help="Output JSON file path")
    args = parser.parse_args()

    collector = PlayerNewsCollector(
        output_file=Path(args.output),
        entries_per_feed=max(1, args.entries_per_feed),
    )
    collector.run()

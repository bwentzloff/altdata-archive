#!/usr/bin/env python3
"""
Player News & Articles Scraper
Collects news and article links for players from RSS feeds using feedparser.
Implements trust hierarchy and player name matching.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import feedparser
except ImportError:
    print("Error: feedparser not installed. Install with: pip install feedparser")
    sys.exit(1)

# Trust hierarchy - higher numbers = more trusted sources
TRUST_SOURCES = {
    # Primary sources (Level 3.0)
    "altsports_news": {"url": "https://news.altfantasysports.com/feed", "name": "AltSports News", "trust": 3.0},
    "sgp": {"url": "https://www.sportsgamblingpodcast.com/feed", "name": "Sports Gambling Podcast", "trust": 2.5},
    
    # Hacker News (Level 2.5)
    "hackernews": {"url": "https://news.ycombinator.com/rss", "name": "Hacker News", "trust": 2.5},
}


class PlayerNewsCollector:
    """Collects and processes news articles for players."""
    
    def __init__(self, output_file: Path = Path("pipeline/raw/articles_raw.json")):
        self.output_file = output_file
        self.articles: List[Dict[str, Any]] = []
        self.stats = {
            "feeds_processed": 0,
            "feeds_successful": 0,
            "feeds_failed": 0,
            "total_articles": 0,
            "articles_with_matches": 0,
            "unique_players_matched": set(),
        }
    
    def fetch_feed(self, feed_key: str, feed_config: Dict) -> Optional[List[Dict]]:
        """Fetch and parse RSS feed, return list of articles."""
        print(f"  Fetching {feed_config['name']}...", end=" ")
        try:
            feed = feedparser.parse(feed_config["url"])
            
            if feed.bozo:
                print(f"⚠️  Parse warning (continuing)")
            else:
                print(f"✓")
            
            articles = []
            for entry in feed.entries[:100]:  # Limit to 100 most recent per feed
                article = {
                    "source": feed_key,
                    "source_name": feed_config["name"],
                    "trust_level": feed_config["trust"],
                    "title": entry.get("title", "Untitled"),
                    "link": entry.get("link", ""),
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
    
    def run(self):
        """Main collection process."""
        print("=" * 60)
        print("Player News Collection - Phase 1 (RSS Feeds)")
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
        
        # Fetch from all configured feeds
        print(f"\nScraping {len(TRUST_SOURCES)} feeds:")
        for feed_key, feed_config in TRUST_SOURCES.items():
            self.stats["feeds_processed"] += 1
            articles = self.fetch_feed(feed_key, feed_config)
            
            if articles:
                for article in articles:
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
                "sources_count": len(TRUST_SOURCES),
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
    collector = PlayerNewsCollector()
    collector.run()

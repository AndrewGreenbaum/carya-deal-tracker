"""
Google Alerts RSS Scraper - Monitor funding news via Google Alerts.

Google Alerts can be configured to output RSS feeds for any search query.
This scraper polls those RSS feeds for new funding announcements.

SETUP:
1. Go to https://google.com/alerts
2. Create alerts for each tracked fund (e.g., "Sequoia Capital" "led" "funding")
3. Set delivery to "RSS feed" (click "Show options")
4. Copy the RSS feed URL
5. Add to GOOGLE_ALERTS_FEEDS in this file or via environment variable

Example alert queries:
- "Sequoia Capital" "led by" "funding"
- "a16z" OR "Andreessen Horowitz" "led" "Series"
- "Founders Fund" "investment" "led"
"""

import asyncio
import logging
import feedparser
import httpx

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import NormalizedArticle
from ..fund_matcher import match_fund_name
from ...config.settings import settings


# Default Google Alerts RSS feeds for tracked funds
# Users should create their own alerts and add URLs here
DEFAULT_ALERT_QUERIES = {
    "sequoia": '"Sequoia Capital" "led" "funding"',
    "a16z": '"Andreessen Horowitz" OR "a16z" "led" "Series"',
    "founders_fund": '"Founders Fund" "led" "investment"',
    "benchmark": '"Benchmark" "led" "venture"',
    "khosla": '"Khosla Ventures" "led" "funding"',
    "index": '"Index Ventures" "led" "Series"',
    "insight": '"Insight Partners" "led" "funding"',
    "bessemer": '"Bessemer" OR "BVP" "led" "venture"',
    "greylock": '"Greylock" "led" "funding"',
    "gv": '"Google Ventures" OR "GV" "led" "investment"',
    "menlo": '"Menlo Ventures" "led" "funding"',
    "thrive": '"Thrive Capital" "led" "Series"',
    "accel": '"Accel" "led" "funding"',
    "felicis": '"Felicis" "led" "investment"',
    "general_catalyst": '"General Catalyst" "led" "funding"',
    "first_round": '"First Round Capital" "led" "seed"',
    "redpoint": '"Redpoint" "led" "venture"',
    "usv": '"Union Square Ventures" "led" "funding"',
}


@dataclass
class GoogleAlertItem:
    """Single item from Google Alerts RSS feed."""
    title: str
    url: str
    description: str
    published: Optional[datetime]
    source: str


class GoogleAlertsScraper:
    """
    Scraper for Google Alerts RSS feeds.

    Polls configured RSS feeds for new funding news.
    """

    def __init__(self, feed_urls: Optional[List[str]] = None):
        """
        Initialize with list of Google Alerts RSS feed URLs.

        Args:
            feed_urls: List of RSS feed URLs from Google Alerts.
                      If None, uses feeds from settings or defaults.
        """
        self.feed_urls = feed_urls or self._get_configured_feeds()
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "BudTracker/1.0 (Investment Research)"}
        )

    def _get_configured_feeds(self) -> List[str]:
        """Get feed URLs from settings or environment."""
        # Check for comma-separated feed URLs in settings
        feeds_str = getattr(settings, 'google_alerts_feeds', '')
        if feeds_str:
            feeds = []
            for f in feeds_str.split(','):
                url = f.strip()
                if url:
                    # Validate URL starts with http
                    if url.startswith('http://') or url.startswith('https://'):
                        feeds.append(url)
                    else:
                        logger.warning(f"Invalid Google Alerts feed URL (must start with http): {url}")
            return feeds
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def fetch_feed(self, feed_url: str) -> List[GoogleAlertItem]:
        """Fetch and parse a single RSS feed."""
        try:
            response = await self.client.get(feed_url)
            response.raise_for_status()

            # Parse RSS feed
            feed = feedparser.parse(response.text)

            items = []
            for entry in feed.entries:
                # Parse publication date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6])

                # Clean up description (remove HTML)
                description = entry.get('summary', '')
                if description:
                    soup = BeautifulSoup(description, 'lxml')
                    description = soup.get_text(strip=True)

                # Extract source from title if present
                title = entry.get('title', '')
                source = ''
                if ' - ' in title:
                    parts = title.rsplit(' - ', 1)
                    if len(parts) == 2:
                        title = parts[0]
                        source = parts[1]

                items.append(GoogleAlertItem(
                    title=title,
                    url=entry.get('link', ''),
                    description=description[:500],  # Truncate long descriptions
                    published=pub_date,
                    source=source,
                ))

            return items

        except Exception as e:
            logger.error(f"Error fetching Google Alert feed: {e}")
            return []

    async def fetch_all_feeds(self) -> List[GoogleAlertItem]:
        """Fetch all configured RSS feeds."""
        all_items = []
        seen_urls = set()

        for feed_url in self.feed_urls:
            items = await self.fetch_feed(feed_url)
            for item in items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    all_items.append(item)

            # Small delay between feeds
            await asyncio.sleep(0.5)

        return all_items

    async def fetch_full_article(self, url: str) -> Optional[str]:
        """Fetch full article content from URL."""
        try:
            response = await self.client.get(
                url,
                follow_redirects=True,
                timeout=15,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'lxml')

            # Remove unwanted elements
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'ads']):
                tag.decompose()

            # Try common article selectors
            for selector in ['article', '.article-body', '.post-content', 'main', '.content']:
                content = soup.select_one(selector)
                if content:
                    text = content.get_text(separator='\n', strip=True)
                    if len(text) > 200:
                        return text[:5000]

            # Fallback to body
            if soup.body:
                return soup.body.get_text(separator='\n', strip=True)[:5000]

            return None

        except Exception as e:
            logger.error(f"Error fetching article {url}: {e}")
            return None

    async def scrape_all(self) -> List[NormalizedArticle]:
        """
        Full scraping pipeline.

        Returns list of normalized articles ready for extraction.
        """
        if not self.feed_urls:
            logger.warning("No Google Alerts RSS feeds configured. Set GOOGLE_ALERTS_FEEDS environment variable.")
            return []

        # Fetch all alert items
        items = await self.fetch_all_feeds()

        articles = []
        for item in items:
            # Fetch full article content
            full_text = await self.fetch_full_article(item.url)

            text = full_text or f"{item.title}\n\n{item.description}"

            # Match fund from article content using centralized fund matcher
            full_content = f"{item.title} {item.description} {text}"
            fund_slug = match_fund_name(full_content)

            articles.append(NormalizedArticle(
                url=item.url,
                title=item.title,
                text=text,
                published_date=item.published.date() if item.published else date.today(),
                author=None,
                tags=['google_alerts', item.source] if item.source else ['google_alerts'],
                fund_slug=fund_slug,
                fetched_at=datetime.utcnow(),
            ))

            # Rate limit
            await asyncio.sleep(0.3)

        return articles


def get_alert_setup_instructions() -> str:
    """Return instructions for setting up Google Alerts."""
    instructions = """
=== GOOGLE ALERTS SETUP INSTRUCTIONS ===

1. Go to https://google.com/alerts

2. Create alerts for each tracked fund. Suggested queries:
"""
    for fund, query in DEFAULT_ALERT_QUERIES.items():
        instructions += f"   - {fund}: {query}\n"

    instructions += """
3. For each alert:
   - Click "Show options"
   - Set "Deliver to" to "RSS feed"
   - Click "Create Alert"

4. Copy the RSS feed URLs (right-click the RSS icon)

5. Add to Railway environment variable:
   GOOGLE_ALERTS_FEEDS=url1,url2,url3,...

Or update src/harvester/scrapers/google_alerts.py with your feed URLs.
"""
    return instructions


# Convenience function
async def run_google_alerts_scraper(feed_urls: Optional[List[str]] = None) -> List[NormalizedArticle]:
    """Run Google Alerts scraper and return articles."""
    async with GoogleAlertsScraper(feed_urls) as scraper:
        return await scraper.scrape_all()

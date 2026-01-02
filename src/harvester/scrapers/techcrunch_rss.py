"""
TechCrunch RSS Scraper - Monitor TechCrunch Startups feed for funding news.

TechCrunch Startups RSS feed is one of the best sources for early funding
announcements. This scraper polls the RSS feed for new articles mentioning
tracked VC funds.

RSS Feeds:
- Startups: https://techcrunch.com/category/startups/feed/
- Venture: https://techcrunch.com/category/venture/feed/
"""

import asyncio
import feedparser
import httpx
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
import logging

logger = logging.getLogger(__name__)
from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import NormalizedArticle
from ...config.settings import settings
from ...config.funds import FUND_REGISTRY


# TechCrunch RSS feeds
TECHCRUNCH_FEEDS = {
    "startups": "https://techcrunch.com/category/startups/feed/",
    "venture": "https://techcrunch.com/category/venture/feed/",
    "fundraising": "https://techcrunch.com/tag/fundraising/feed/",
}

# Fund name patterns for matching in articles
TC_FUND_PATTERNS = {
    "founders_fund": ["founders fund", "peter thiel"],
    "benchmark": ["benchmark capital", "benchmark partners", "benchmark"],
    "sequoia": ["sequoia capital", "sequoia"],
    "khosla": ["khosla ventures", "khosla"],
    "index": ["index ventures"],
    "a16z": ["andreessen horowitz", "a16z"],
    "insight": ["insight partners"],
    "bessemer": ["bessemer venture", "bessemer", "bvp"],
    "redpoint": ["redpoint ventures", "redpoint"],
    "greylock": ["greylock partners", "greylock"],
    "gv": ["google ventures", "gv "],  # Note: space after GV to avoid false positives
    "menlo": ["menlo ventures"],
    "usv": ["union square ventures", "usv"],
    "thrive": ["thrive capital"],
    "accel": ["accel partners", "accel"],
    "felicis": ["felicis ventures", "felicis"],
    "general_catalyst": ["general catalyst"],
    "first_round": ["first round capital", "first round"],
}


@dataclass
class TechCrunchArticle:
    """Single article from TechCrunch RSS feed."""
    title: str
    url: str
    description: str
    published: Optional[datetime]
    author: Optional[str]
    categories: List[str]


class TechCrunchScraper:
    """
    Scraper for TechCrunch RSS feeds.

    Monitors startup and venture feeds for funding announcements.
    """

    def __init__(self, feeds: Optional[List[str]] = None):
        """
        Initialize with list of RSS feed URLs.

        Args:
            feeds: List of feed URLs. If None, uses default TechCrunch feeds.
        """
        self.feeds = feeds or list(TECHCRUNCH_FEEDS.values())
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "BudTracker/1.0 (Investment Research)"}
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def fetch_feed(self, feed_url: str) -> List[TechCrunchArticle]:
        """Fetch and parse a single RSS feed."""
        try:
            response = await self.client.get(feed_url)
            response.raise_for_status()

            feed = feedparser.parse(response.text)

            items = []
            for entry in feed.entries:
                # FIX: Skip entries without URLs (empty URLs cause downstream issues)
                if not entry.get('link'):
                    continue

                # Parse publication date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    # FIX: Check tuple length before unpacking (crash prevention)
                    if len(entry.published_parsed) >= 6:
                        pub_date = datetime(*entry.published_parsed[:6])

                # Clean description
                description = entry.get('summary', '')
                if description:
                    soup = BeautifulSoup(description, 'lxml')
                    description = soup.get_text(strip=True)

                # Extract categories/tags
                categories = []
                if hasattr(entry, 'tags'):
                    categories = [tag.term for tag in entry.tags if hasattr(tag, 'term')]

                # Get author
                author = None
                if hasattr(entry, 'author'):
                    author = entry.author

                items.append(TechCrunchArticle(
                    title=entry.get('title', ''),
                    url=entry.get('link', ''),
                    description=description[:1000],
                    published=pub_date,
                    author=author,
                    categories=categories,
                ))

            return items

        except httpx.HTTPError as e:
            # FIX: Specific exception + logger instead of print()
            logger.error(f"HTTP error fetching TechCrunch feed {feed_url}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching TechCrunch feed {feed_url}: {e}", exc_info=True)
            return []

    async def fetch_all_feeds(self, hours_back: int = 168) -> List[TechCrunchArticle]:
        """Fetch all configured RSS feeds."""
        all_items = []
        seen_urls = set()
        # FIX: Use timezone-aware datetime (utcnow() deprecated in Python 3.12+)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        for feed_url in self.feeds:
            items = await self.fetch_feed(feed_url)
            for item in items:
                # Skip duplicates and old articles
                if item.url in seen_urls:
                    continue
                if item.published and item.published < cutoff:
                    continue

                seen_urls.add(item.url)
                all_items.append(item)

            await asyncio.sleep(0.5)

        return all_items

    def match_tracked_fund(self, article: TechCrunchArticle) -> Optional[str]:
        """Check if article mentions any tracked fund."""
        search_text = f"{article.title} {article.description}".lower()

        for slug, patterns in TC_FUND_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in search_text:
                    return slug

        return None

    def is_funding_article(self, article: TechCrunchArticle) -> bool:
        """Check if article is about funding."""
        funding_keywords = [
            "raises", "raised", "funding", "series a", "series b", "series c",
            "series d", "seed", "million", "billion", "led by", "investment",
            "round", "valuation", "venture", "capital"
        ]
        text = f"{article.title} {article.description}".lower()
        return any(kw in text for kw in funding_keywords)

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
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
                tag.decompose()

            # TechCrunch article selectors
            for selector in ['.article-content', '.entry-content', 'article', '.post-content', 'main']:
                content = soup.select_one(selector)
                if content:
                    text = content.get_text(separator='\n', strip=True)
                    if len(text) > 200:
                        return text[:8000]

            return None

        except httpx.HTTPError as e:
            # FIX: Specific exception + logger instead of print()
            logger.error(f"HTTP error fetching TechCrunch article {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching TechCrunch article {url}: {e}", exc_info=True)
            return None

    async def scrape_all(self, hours_back: int = 168) -> List[NormalizedArticle]:
        """
        Full scraping pipeline.

        Args:
            hours_back: Look back this many hours (default 7 days)

        Returns:
            List of normalized articles ready for extraction.
        """
        articles = await self.fetch_all_feeds(hours_back=hours_back)

        normalized = []
        for article in articles:
            # Only process funding-related articles
            if not self.is_funding_article(article):
                continue

            # FIX: Skip articles without publication dates
            # (defaulting to today corrupts data and defeats hours_back filter)
            if not article.published:
                logger.debug(f"Skipping article without date: {article.title}")
                continue

            # Check for tracked fund mentions
            fund_slug = self.match_tracked_fund(article)

            # Fetch full article content
            full_text = await self.fetch_full_article(article.url)
            text = full_text or f"{article.title}\n\n{article.description}"

            normalized.append(NormalizedArticle(
                url=article.url,
                title=article.title,
                text=text,
                published_date=article.published.date(),
                author=article.author,
                tags=['techcrunch'] + article.categories,
                fund_slug=fund_slug or "",
                # FIX: Use timezone-aware datetime (utcnow() deprecated in Python 3.12+)
                fetched_at=datetime.now(timezone.utc),
            ))

            # Rate limit
            await asyncio.sleep(0.3)

        return normalized


# Convenience function
async def run_techcrunch_scraper(hours_back: int = 168) -> List[NormalizedArticle]:
    """Run TechCrunch scraper and return articles."""
    async with TechCrunchScraper() as scraper:
        return await scraper.scrape_all(hours_back=hours_back)

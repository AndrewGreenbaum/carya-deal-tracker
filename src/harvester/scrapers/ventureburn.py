"""
Ventureburn Scraper - Emerging Markets Startup News.

Ventureburn (ventureburn.com) covers startup news for emerging markets,
with focus on Africa, cryptocurrency, AI, and venture capital.

Key advantages:
- Emerging markets coverage (often missed by US-focused sources)
- AI and crypto startup funding
- Venture capital news
- WordPress RSS feed available

RSS Feed: https://ventureburn.com/feed/

Note: Contains significant crypto/price prediction content.
Filtered for funding-related articles only.
"""

import asyncio
import logging
import feedparser
import httpx
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import NormalizedArticle

logger = logging.getLogger(__name__)
from ..fund_matcher import match_fund_name
from ...config.settings import settings


# Ventureburn RSS feeds
VENTUREBURN_FEEDS = {
    "main": "https://ventureburn.com/feed/",
}

# Additional category feeds (if available)
VENTUREBURN_CATEGORIES = [
    "https://ventureburn.com/category/startups/feed/",
    "https://ventureburn.com/category/venture-capital/feed/",
    "https://ventureburn.com/category/ai/feed/",
]

# FIX #47: Fund patterns now consolidated in fund_matcher.py

# Funding keywords
FUNDING_KEYWORDS = [
    "raises", "raised", "funding", "series", "seed", "million", "billion",
    "led by", "investment", "round", "valuation", "venture", "capital",
    "secures", "closes", "bags", "snaps"
]

# Exclude crypto noise (price predictions, exchange reviews)
EXCLUDE_KEYWORDS = [
    "price prediction", "exchange review", "best crypto exchange",
    "how to buy", "trading guide", "price analysis", "referral code",
    "cloud mining", "daily returns"
]


@dataclass
class VentureburnArticle:
    """Single article from Ventureburn."""
    title: str
    url: str
    description: str
    published: Optional[datetime]
    author: Optional[str]
    categories: List[str]


class VentureburnScraper:
    """
    Scraper for Ventureburn RSS feed.

    Covers emerging markets startup and VC news.
    Filters out crypto price prediction noise.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            }
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def fetch_feed(self, feed_url: str) -> List[VentureburnArticle]:
        """Fetch and parse RSS feed with retry logic."""
        for attempt in range(3):
            try:
                response = await self.client.get(feed_url)
                response.raise_for_status()

                feed = feedparser.parse(response.text)

                # Check for malformed feed
                if feed.bozo:
                    logger.warning(f"Malformed Ventureburn feed: {feed.bozo_exception}")
                    return []

                articles = []

                for entry in feed.entries:
                    # Parse publication date
                    pub_date = None
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

                    # Clean description
                    description = entry.get('summary', '') or entry.get('description', '')
                    if description:
                        soup = BeautifulSoup(description, 'lxml')
                        description = soup.get_text(strip=True)[:2000]

                    # Extract categories
                    categories = []
                    if hasattr(entry, 'tags'):
                        categories = [tag.term for tag in entry.tags if hasattr(tag, 'term')]

                    articles.append(VentureburnArticle(
                        title=entry.get('title', ''),
                        url=entry.get('link', ''),
                        description=description,
                        published=pub_date,
                        author=entry.get('author', None),
                        categories=categories,
                    ))

                return articles

            except httpx.HTTPError as e:
                if attempt < 2:
                    delay = 2 ** attempt
                    logger.warning(f"HTTP error fetching Ventureburn feed (attempt {attempt + 1}/3), retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"HTTP error fetching Ventureburn feed after 3 attempts: {e}")
                    return []
            except Exception as e:
                logger.error(f"Unexpected error fetching Ventureburn feed: {e}", exc_info=True)
                return []

        return []

    async def fetch_all_feeds(self, hours_back: int = 168) -> List[VentureburnArticle]:
        """Fetch main feed and category feeds in parallel."""
        all_articles = []
        seen_urls = set()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        # OPTIMIZATION: Fetch all feeds in parallel (main + categories)
        all_feed_urls = [VENTUREBURN_FEEDS["main"]] + VENTUREBURN_CATEGORIES
        tasks = [self.fetch_feed(url) for url in all_feed_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                # Category feeds may not exist - log at debug level
                logger.debug(f"Feed fetch failed (may not exist): {result}")
                continue
            for article in result:
                if article.url not in seen_urls:
                    if article.published and article.published < cutoff:
                        continue
                    seen_urls.add(article.url)
                    all_articles.append(article)

        return all_articles

    def is_funding_article(self, article: VentureburnArticle) -> bool:
        """Check if article is about funding (not crypto noise)."""
        text = f"{article.title} {article.description}".lower()

        # Exclude crypto noise
        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            return False

        # Must have funding keywords
        return any(kw in text for kw in FUNDING_KEYWORDS)

    def match_tracked_fund(self, article: VentureburnArticle) -> Optional[str]:
        """Check if article mentions any tracked fund."""
        search_text = f"{article.title} {article.description}"
        # FIX #47: Use centralized fund_matcher instead of local patterns
        return match_fund_name(search_text)

    async def scrape_all(self, hours_back: int = 168) -> List[NormalizedArticle]:
        """
        Full scraping pipeline.

        Args:
            hours_back: Look back this many hours (default 7 days)

        Returns:
            List of NormalizedArticle objects for funding news.
        """
        articles = await self.fetch_all_feeds(hours_back=hours_back)

        normalized = []
        for article in articles:
            # Only process funding-related articles (filter crypto noise)
            if not self.is_funding_article(article):
                continue

            fund_slug = self.match_tracked_fund(article)

            # Build tags
            tags = ['ventureburn', 'emerging_markets']
            tags.extend([c.lower().replace(' ', '_') for c in article.categories[:3]])

            normalized.append(NormalizedArticle(
                url=article.url,
                title=article.title,
                text=f"{article.title}\n\n{article.description}",
                published_date=article.published.date() if article.published else date.today(),
                author=article.author or "Ventureburn",
                tags=tags,
                fund_slug=fund_slug or "",
                fetched_at=datetime.now(timezone.utc),
            ))

        return normalized


# Convenience function
async def run_ventureburn_scraper(hours_back: int = 168) -> List[NormalizedArticle]:
    """Run Ventureburn scraper and return articles."""
    async with VentureburnScraper() as scraper:
        return await scraper.scrape_all(hours_back=hours_back)

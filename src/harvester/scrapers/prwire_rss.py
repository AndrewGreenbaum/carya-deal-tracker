"""
PR Wire RSS Feed Scraper.

Scrapes RSS feeds from PR distribution services where funding announcements are published:
- PRNewswire (prnewswire.com)
- GlobeNewswire (globenewswire.com)

Note: BusinessWire deprecated their public RSS feeds (require authenticated PressPass).

These services are the primary distribution channels for official funding press releases.
The Brave News API often misses these - RSS feeds provide direct access.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

import feedparser
import httpx
from dataclasses import dataclass

from ..base_scraper import NormalizedArticle
from ...config.settings import settings

logger = logging.getLogger(__name__)


# PR Wire RSS Feed URLs
# PRNewswire venture capital category
PRNEWSWIRE_FEEDS = [
    "https://www.prnewswire.com/rss/financial-services-latest-news/venture-capital-list.rss",
]

# GlobeNewswire financing and M&A feeds
GLOBENEWSWIRE_FEEDS = [
    "https://www.globenewswire.com/RssFeed/subjectcode/23-Financing%20Agreements/feedTitle/GlobeNewswire%20-%20Financing%20Agreements",
    "https://www.globenewswire.com/RssFeed/subjectcode/26-Mergers%20and%20Acquisitions/feedTitle/GlobeNewswire%20-%20Mergers%20and%20Acquisitions",
]

# All feeds combined (BusinessWire deprecated public RSS - requires PressPass auth)
ALL_PR_FEEDS = PRNEWSWIRE_FEEDS + GLOBENEWSWIRE_FEEDS


# Keywords to filter funding-related press releases
FUNDING_KEYWORDS = [
    "raises", "raised", "funding", "series a", "series b", "series c", "series d",
    "seed round", "seed funding", "pre-seed", "venture", "investment", "million",
    "financing", "capital", "investors", "led by", "leads", "backed by",
]

# Keywords to identify tracked VC involvement
TRACKED_FUND_KEYWORDS = [
    # Fund names
    "founders fund", "benchmark", "sequoia", "khosla", "index ventures",
    "andreessen horowitz", "a16z", "insight partners", "bessemer",
    "redpoint", "greylock", "google ventures", "gv", "menlo ventures",
    "union square", "thrive capital", "accel", "felicis", "general catalyst",
    "first round",
    # Notable partners
    "peter thiel", "bill gurley", "sarah tavel", "vinod khosla", "reid hoffman",
    "tomasz tunguz", "logan bartlett", "josh kopelman", "josh kushner",
]


@dataclass
class PRWireArticle:
    """Article from PR wire RSS feed."""
    title: str
    url: str
    description: str
    published_date: Optional[datetime]
    source: str  # prnewswire, globenewswire, businesswire


class PRWireRSSScraper:
    """
    Scrape funding press releases from PR wire RSS feeds.

    These feeds are the primary source for official funding announcements.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BudTracker/1.0)"}
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _parse_date(self, entry: Dict[str, Any]) -> Optional[datetime]:
        """Parse publication date from feed entry."""
        # Try different date fields
        for field in ["published_parsed", "updated_parsed", "created_parsed"]:
            if hasattr(entry, field) and getattr(entry, field):
                try:
                    import time
                    ts = time.mktime(getattr(entry, field))
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
                except (TypeError, ValueError):
                    continue

        # Try string date fields
        for field in ["published", "updated", "created"]:
            if entry.get(field):
                try:
                    from dateutil import parser
                    return parser.parse(entry[field])
                except Exception:
                    continue

        return None

    def _is_funding_related(self, title: str, description: str) -> bool:
        """Check if article is related to startup funding."""
        text = f"{title} {description}".lower()
        return any(kw in text for kw in FUNDING_KEYWORDS)

    def _has_tracked_fund(self, title: str, description: str) -> bool:
        """Check if article mentions a tracked VC fund or partner."""
        text = f"{title} {description}".lower()
        return any(kw in text for kw in TRACKED_FUND_KEYWORDS)

    def _get_source_name(self, feed_url: str) -> str:
        """Get source name from feed URL."""
        if "prnewswire" in feed_url:
            return "prnewswire"
        elif "globenewswire" in feed_url:
            return "globenewswire"
        elif "businesswire" in feed_url:
            return "businesswire"
        return "prwire"

    async def fetch_feed(
        self,
        feed_url: str,
        hours_back: int = 168,  # 7 days
    ) -> List[PRWireArticle]:
        """Fetch and parse a single RSS feed."""
        try:
            response = await self.client.get(feed_url)
            response.raise_for_status()
            feed = feedparser.parse(response.text)

            if feed.bozo:
                logger.warning(f"Malformed RSS feed: {feed_url} - {feed.bozo_exception}")

            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
            source = self._get_source_name(feed_url)
            articles = []

            for entry in feed.entries:
                title = entry.get("title", "")
                link = entry.get("link", "")
                description = entry.get("summary", entry.get("description", ""))
                pub_date = self._parse_date(entry)

                # Skip old articles
                if pub_date and pub_date < cutoff:
                    continue

                # Skip non-funding articles
                if not self._is_funding_related(title, description):
                    continue

                articles.append(PRWireArticle(
                    title=title,
                    url=link,
                    description=description,
                    published_date=pub_date,
                    source=source,
                ))

            logger.info(f"Found {len(articles)} funding articles from {source}")
            return articles

        except Exception as e:
            logger.error(f"Error fetching PR wire feed {feed_url}: {e}")
            return []

    async def scrape_all_feeds(
        self,
        hours_back: int = 168,
        fund_filter: bool = True,
    ) -> List[NormalizedArticle]:
        """
        Scrape all PR wire RSS feeds.

        Args:
            hours_back: How far back to look (default 7 days)
            fund_filter: If True, only return articles mentioning tracked funds

        Returns:
            List of NormalizedArticle objects ready for extraction
        """
        all_articles: List[PRWireArticle] = []
        seen_urls = set()

        # Fetch all feeds in parallel
        tasks = [self.fetch_feed(url, hours_back) for url in ALL_PR_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"PR wire feed error: {result}")
                continue
            for article in result:
                if article.url not in seen_urls:
                    seen_urls.add(article.url)
                    all_articles.append(article)

        logger.info(f"Total funding articles from PR wires: {len(all_articles)}")

        # Filter for tracked funds if requested
        if fund_filter:
            filtered = [
                a for a in all_articles
                if self._has_tracked_fund(a.title, a.description)
            ]
            logger.info(f"Articles mentioning tracked funds: {len(filtered)}")
            all_articles = filtered

        # Convert to NormalizedArticle
        normalized: List[NormalizedArticle] = []
        for article in all_articles:
            # Use description as text since we don't have full content yet
            # The full content will be fetched via Firecrawl or direct scrape
            normalized.append(NormalizedArticle(
                title=article.title,
                url=article.url,
                text=f"{article.title}\n\n{article.description}",
                published_date=article.published_date.date() if article.published_date else None,
                fund_slug=article.source,  # Use source as fund_slug for tracking
            ))

        return normalized


async def scrape_prwire_feeds(
    hours_back: int = 168,
    fund_filter: bool = True,
) -> List[NormalizedArticle]:
    """
    Convenience function to scrape all PR wire RSS feeds.

    Args:
        hours_back: How far back to look (default 7 days)
        fund_filter: If True, only return articles mentioning tracked funds

    Returns:
        List of NormalizedArticle objects
    """
    async with PRWireRSSScraper() as scraper:
        return await scraper.scrape_all_feeds(hours_back, fund_filter)

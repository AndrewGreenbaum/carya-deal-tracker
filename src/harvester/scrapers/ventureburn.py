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
import feedparser
import httpx
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import NormalizedArticle
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

# Fund patterns for matching
FUND_PATTERNS = {
    "founders_fund": ["founders fund", "peter thiel"],
    "benchmark": ["benchmark capital", "benchmark"],
    "sequoia": ["sequoia capital", "sequoia"],
    "khosla": ["khosla ventures", "khosla"],
    "index": ["index ventures"],
    "a16z": ["andreessen horowitz", "a16z"],
    "insight": ["insight partners"],
    "bessemer": ["bessemer venture", "bessemer", "bvp"],
    "redpoint": ["redpoint ventures", "redpoint"],
    "greylock": ["greylock partners", "greylock"],
    "gv": ["google ventures", "gv "],
    "menlo": ["menlo ventures"],
    "usv": ["union square ventures", "usv"],
    "thrive": ["thrive capital"],
    "accel": ["accel partners", "accel"],
    "felicis": ["felicis ventures", "felicis"],
    "general_catalyst": ["general catalyst"],
    "first_round": ["first round capital", "first round"],
}

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
        """Fetch and parse RSS feed."""
        try:
            response = await self.client.get(feed_url)
            response.raise_for_status()

            feed = feedparser.parse(response.text)
            articles = []

            for entry in feed.entries:
                # Parse publication date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6])

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

        except Exception as e:
            print(f"Error fetching Ventureburn feed: {e}")
            return []

    async def fetch_all_feeds(self, hours_back: int = 168) -> List[VentureburnArticle]:
        """Fetch main feed and category feeds."""
        all_articles = []
        seen_urls = set()
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        # Main feed
        main_articles = await self.fetch_feed(VENTUREBURN_FEEDS["main"])
        for article in main_articles:
            if article.url not in seen_urls:
                if article.published and article.published < cutoff:
                    continue
                seen_urls.add(article.url)
                all_articles.append(article)

        # Try category feeds (may not all exist)
        for feed_url in VENTUREBURN_CATEGORIES:
            await asyncio.sleep(0.3)
            try:
                articles = await self.fetch_feed(feed_url)
                for article in articles:
                    if article.url not in seen_urls:
                        if article.published and article.published < cutoff:
                            continue
                        seen_urls.add(article.url)
                        all_articles.append(article)
            except Exception:
                continue  # Category feed may not exist

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
        search_text = f"{article.title} {article.description}".lower()

        for slug, patterns in FUND_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in search_text:
                    return slug

        return None

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
                fetched_at=datetime.utcnow(),
            ))

        return normalized


# Convenience function
async def run_ventureburn_scraper(hours_back: int = 168) -> List[NormalizedArticle]:
    """Run Ventureburn scraper and return articles."""
    async with VentureburnScraper() as scraper:
        return await scraper.scrape_all(hours_back=hours_back)

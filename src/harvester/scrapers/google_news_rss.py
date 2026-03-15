"""
Google News RSS Scraper - Coverage for External-Only Funds.

Google News provides much better coverage than Brave Search API for finding
funding announcements about specific VC funds. This scraper creates RSS
feeds for funds that don't have scrapable portfolio pages.

Target funds (external-only or low coverage):
- Thrive Capital (0 deals - no portfolio page)
- Benchmark (0 deals - no portfolio page)
- First Round (0 deals - site doesn't announce deals)
- Greylock (1 deal - Playwright issues)
- GV (2 deals - limited site announcements)
- Founders Fund (3 deals - partial coverage)
- Redpoint (4 deals - React site issues)

RSS Format:
https://news.google.com/rss/search?q="Fund+Name"+funding&hl=en-US&gl=US&ceid=US:en
"""

import asyncio
import feedparser
import httpx
import logging
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlencode
from bs4 import BeautifulSoup

from ..base_scraper import NormalizedArticle

logger = logging.getLogger(__name__)


# External-only funds that need Google News coverage
# These funds either have no portfolio page or their scrapers aren't working
GOOGLE_NEWS_FUNDS = {
    # EXTERNAL scraper type (no portfolio page)
    "thrive": {
        "name": "Thrive Capital",
        "queries": [
            '"Thrive Capital" funding',
            '"Thrive Capital" leads investment',
            '"Thrive Capital" Series',
            '"Josh Kushner" investment startup',
        ],
        "exclude": ["Thrive Global", "Thrive IT", "Thrive Market", "Thrive Wellness"],
    },
    "benchmark": {
        "name": "Benchmark Capital",
        "queries": [
            '"Benchmark Capital" funding',
            '"Benchmark" leads investment startup',
            '"Benchmark" Series A',
            '"Bill Gurley" investment',
            '"Peter Fenton" investment',
            '"Sarah Tavel" investment',
        ],
        "exclude": ["Benchmark Electronics", "Benchmark International"],
    },
    # HTML scraper but site doesn't announce deals
    "first_round": {
        "name": "First Round Capital",
        "queries": [
            '"First Round Capital" funding',
            '"First Round" leads seed',
            '"Josh Kopelman" investment',
        ],
        "exclude": ["first round pick", "NFL", "NBA"],
    },
    # PLAYWRIGHT but may have issues
    "greylock": {
        "name": "Greylock",
        "queries": [
            '"Greylock" funding startup',
            '"Greylock Partners" leads',
            '"Greylock" Series A',
            '"Reid Hoffman" investment startup',
        ],
        "exclude": [],
    },
    "gv": {
        "name": "GV (Google Ventures)",
        "queries": [
            '"Google Ventures" funding',
            '"Google Ventures" leads investment',
            '"GV" venture capital funding',
        ],
        "exclude": ["NYSE:GV", "Visionary Holdings"],
    },
    "founders_fund": {
        "name": "Founders Fund",
        "queries": [
            '"Founders Fund" funding',
            '"Founders Fund" leads investment',
            '"Keith Rabois" investment startup',
            '"Peter Thiel" investment startup',
        ],
        "exclude": [],
    },
    "redpoint": {
        "name": "Redpoint Ventures",
        "queries": [
            '"Redpoint Ventures" funding',
            '"Redpoint" leads investment',
            '"Redpoint" Series A',
        ],
        "exclude": ["Redpoint Bio"],
    },
}


@dataclass
class GoogleNewsArticle:
    """Single article from Google News RSS."""
    title: str
    url: str  # Google News URL (can't resolve to actual article)
    source_url: str  # Source domain URL (e.g., https://www.bisnow.com)
    description: str
    published: Optional[datetime]
    source: str
    fund_slug: str


class GoogleNewsRSSScraper:
    """
    Scraper for Google News RSS feeds targeting specific VC funds.

    Google News RSS provides better coverage than Brave Search API because:
    1. Indexes more news sources
    2. Better freshness/recency
    3. More comprehensive for specific entity searches
    """

    def __init__(self, funds: Optional[List[str]] = None):
        """
        Initialize with list of fund slugs to monitor.

        Args:
            funds: List of fund slugs. If None, uses all GOOGLE_NEWS_FUNDS.
        """
        self.funds = funds or list(GOOGLE_NEWS_FUNDS.keys())
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BudTracker/1.0)"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _build_feed_url(self, query: str) -> str:
        """Build Google News RSS URL for a query."""
        params = {
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
        return f"https://news.google.com/rss/search?{urlencode(params)}"

    def _should_exclude(self, text: str, fund_slug: str) -> bool:
        """Check if article should be excluded based on fund-specific rules."""
        fund_config = GOOGLE_NEWS_FUNDS.get(fund_slug, {})
        exclude_terms = fund_config.get("exclude", [])

        text_lower = text.lower()
        for term in exclude_terms:
            if term.lower() in text_lower:
                return True
        return False

    async def fetch_feed(self, query: str, fund_slug: str) -> List[GoogleNewsArticle]:
        """Fetch and parse a Google News RSS feed."""
        feed_url = self._build_feed_url(query)

        try:
            response = await self.client.get(feed_url)
            response.raise_for_status()

            feed = feedparser.parse(response.text)

            if feed.bozo:
                logger.warning(f"Malformed Google News feed for '{query}': {feed.bozo_exception}")
                return []

            items = []
            for entry in feed.entries:
                # Skip entries without URLs
                link = entry.get('link', '')
                if not link:
                    continue

                title = entry.get('title', '')
                description = entry.get('summary', '')

                # Clean HTML from description
                if description:
                    soup = BeautifulSoup(description, 'lxml')
                    description = soup.get_text(strip=True)

                # Check exclusions
                combined_text = f"{title} {description}"
                if self._should_exclude(combined_text, fund_slug):
                    logger.debug(f"Excluded article for {fund_slug}: {title[:50]}...")
                    continue

                # Parse publication date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    if len(entry.published_parsed) >= 6:
                        pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

                # Extract source from title (Google News format: "Title - Source")
                source = ""
                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    if len(parts) == 2:
                        title = parts[0]
                        source = parts[1]

                # Get source URL from RSS entry (e.g., https://www.bisnow.com)
                source_entry = entry.get('source', {})
                source_url = ""
                if isinstance(source_entry, dict):
                    source_url = source_entry.get('href', '')

                items.append(GoogleNewsArticle(
                    title=title,
                    url=link,
                    source_url=source_url,
                    description=description[:1000],
                    published=pub_date,
                    source=source,
                    fund_slug=fund_slug,
                ))

            return items

        except httpx.HTTPError as e:
            logger.warning(f"HTTP error fetching Google News for '{query}': {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching Google News for '{query}': {e}", exc_info=True)
            return []

    async def fetch_all_funds(self, days_back: int = 30) -> List[GoogleNewsArticle]:
        """Fetch Google News for all configured funds with rate limiting."""
        all_items: List[GoogleNewsArticle] = []
        seen_urls: set = set()
        seen_titles: set = set()  # Also dedupe by title (same article, different queries)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

        # Build all queries with fund context
        queries_with_context = []
        for fund_slug in self.funds:
            fund_config = GOOGLE_NEWS_FUNDS.get(fund_slug, {})
            queries = fund_config.get("queries", [])
            for query in queries:
                queries_with_context.append((query, fund_slug))

        # Rate-limited parallel fetching using semaphore
        MAX_CONCURRENT = 5  # Limit concurrent requests to avoid rate limiting
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def fetch_with_limit(query: str, fund_slug: str) -> List[GoogleNewsArticle]:
            async with semaphore:
                result = await self.fetch_feed(query, fund_slug)
                await asyncio.sleep(0.2)  # Small delay between requests
                return result

        tasks = [fetch_with_limit(q, f) for q, f in queries_with_context]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Google News fetch failed: {result}")
                continue

            for item in result:
                # Skip duplicates by URL
                if item.url in seen_urls:
                    continue

                # Skip duplicates by title (same article from different queries)
                title_key = item.title.lower().strip()
                if title_key in seen_titles:
                    continue

                # Skip old articles
                if item.published and item.published < cutoff:
                    continue

                seen_urls.add(item.url)
                seen_titles.add(title_key)
                all_items.append(item)

        logger.info(f"Google News: Found {len(all_items)} unique articles for {len(self.funds)} funds")
        return all_items

    async def scrape_all(self, days_back: int = 30) -> List[NormalizedArticle]:
        """
        Full scraping pipeline for Google News RSS.

        NOTE: Google News URLs use JavaScript redirects that httpx can't follow.
        Instead of trying to fetch full articles (which fails), we use the RSS
        data directly. The title + description is usually sufficient for the
        LLM to extract deal information.

        Args:
            days_back: How many days back to look (default 30)

        Returns:
            List of normalized articles ready for extraction.
        """
        articles = await self.fetch_all_funds(days_back=days_back)

        if not articles:
            return []

        normalized = []
        for article in articles:
            # Use today as fallback for missing dates
            published_date = article.published.date() if article.published else date.today()

            # Build text from RSS data - include fund name context for better matching
            # Format: Title + Source + Description (provides enough context for LLM)
            fund_config = GOOGLE_NEWS_FUNDS.get(article.fund_slug, {})
            fund_name = fund_config.get("name", article.fund_slug)

            text_parts = [
                f"Headline: {article.title}",
                f"Source: {article.source}" if article.source else "",
                f"Fund: {fund_name}",  # Add fund context for LLM
                "",
                article.description if article.description else "",
            ]
            text = "\n".join(part for part in text_parts if part)

            # IMPORTANT: Use Google News URL (unique per article), NOT source_url
            # source_url is just the domain (e.g., "https://fortune.com")
            # Google News URL is unique: "https://news.google.com/rss/articles/CBMi..."
            # This ensures proper deduplication in the database
            normalized.append(NormalizedArticle(
                url=article.url,  # Google News URL - unique per article
                title=article.title,
                text=text,
                published_date=published_date,
                author=article.source,
                tags=['google_news', article.fund_slug],
                fund_slug=article.fund_slug,
                fetched_at=datetime.now(timezone.utc),
            ))

        logger.info(f"Google News: Normalized {len(normalized)} articles (no full-text fetch, using RSS data)")
        return normalized


# Convenience function
async def run_google_news_scraper(days_back: int = 30) -> List[NormalizedArticle]:
    """Run Google News RSS scraper and return articles."""
    async with GoogleNewsRSSScraper() as scraper:
        return await scraper.scrape_all(days_back=days_back)

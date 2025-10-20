"""
Brave Search API Scraper - Broad News Coverage.

Uses Brave Search API to find funding news articles with
"led by" language for tracked VC funds.

Advantages over NewsAPI:
- Better recent article coverage
- No source domain restrictions
- Includes more niche tech publications
"""

import asyncio
import httpx
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any

from ..base_scraper import NormalizedArticle
from ...config.settings import settings
from ...config.funds import FUND_REGISTRY


# Brave Search API endpoint
BRAVE_NEWS_API = "https://api.search.brave.com/res/v1/news/search"
BRAVE_WEB_API = "https://api.search.brave.com/res/v1/web/search"

# Fund name variations for search queries
FUND_SEARCH_NAMES = {
    "founders_fund": "Founders Fund",
    "benchmark": "Benchmark Capital",
    "sequoia": "Sequoia Capital",
    "khosla": "Khosla Ventures",
    "index": "Index Ventures",
    "a16z": "Andreessen Horowitz",
    "insight": "Insight Partners",
    "bessemer": "Bessemer Venture Partners",
    "redpoint": "Redpoint Ventures",
    "greylock": "Greylock Partners",
    "gv": "Google Ventures",
    "menlo": "Menlo Ventures",
    "usv": "Union Square Ventures",
    "thrive": "Thrive Capital",
    "accel": "Accel",
    "felicis": "Felicis Ventures",
    "general_catalyst": "General Catalyst",
    "first_round": "First Round Capital",
}

# Enterprise AI keywords
ENTERPRISE_KEYWORDS = [
    "enterprise AI",
    "B2B AI",
    "AI infrastructure",
    "LLMOps",
    "AI security",
    "cybersecurity AI",
    "AI SaaS",
    "AI platform",
    "agentic AI",
    "AI workflow",
]


@dataclass
class BraveSearchResult:
    """Single search result from Brave API."""
    title: str
    url: str
    description: str
    published_date: Optional[date]
    source: str
    extra_snippets: List[str]


class BraveSearchScraper:
    """
    Search for funding news via Brave Search API.

    Executes targeted queries for each tracked fund with "led by" language
    to find new deals where they are the lead investor.
    """

    def __init__(self):
        self.api_key = settings.brave_search_key
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            }
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _build_fund_query(self, fund_slug: str) -> str:
        """Build search query for a specific fund's lead deals."""
        fund_name = FUND_SEARCH_NAMES.get(fund_slug, "")
        if not fund_name:
            return ""

        # Query: Fund name + lead language + funding keywords
        return f'"{fund_name}" AND ("led by" OR "led the" OR "leading the round") AND ("funding" OR "raises" OR "Series")'

    def _build_enterprise_query(self) -> str:
        """Build broad Enterprise AI funding query."""
        keywords = " OR ".join([f'"{kw}"' for kw in ENTERPRISE_KEYWORDS[:5]])
        return f'({keywords}) AND ("funding" OR "raises" OR "Series") AND ("led by")'

    async def search_news(
        self,
        query: str,
        count: int = 20,
        freshness: str = "pw",  # Past week
    ) -> List[BraveSearchResult]:
        """
        Execute news search on Brave API.

        Args:
            query: Search query string
            count: Number of results (max 100)
            freshness: pd=past day, pw=past week, pm=past month

        Returns:
            List of BraveSearchResult objects
        """
        if not self.api_key:
            raise ValueError("BRAVE_SEARCH_KEY not configured")

        params = {
            "q": query,
            "count": count,
            "freshness": freshness,
            "text_decorations": False,
            "safesearch": "off",
        }

        try:
            response = await self.client.get(BRAVE_NEWS_API, params=params)
            response.raise_for_status()
            data = response.json()

            return self._parse_news_results(data)

        except httpx.HTTPStatusError as e:
            print(f"Brave Search HTTP error: {e.response.status_code}")
            return []
        except Exception as e:
            print(f"Brave Search error: {e}")
            return []

    def _parse_news_results(self, data: Dict[str, Any]) -> List[BraveSearchResult]:
        """Parse Brave news API response."""
        results = []

        for item in data.get("results", []):
            try:
                # Parse date
                pub_date = None
                age = item.get("age", "")
                if age:
                    # Brave returns relative time like "2 days ago"
                    pub_date = self._parse_relative_date(age)

                results.append(BraveSearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    description=item.get("description", ""),
                    published_date=pub_date,
                    source=item.get("meta_url", {}).get("hostname", ""),
                    extra_snippets=item.get("extra_snippets", []),
                ))
            except Exception as e:
                print(f"Error parsing Brave result: {e}")
                continue

        return results

    def _parse_relative_date(self, age_str: str) -> Optional[date]:
        """Parse relative date string like '2 days ago'."""
        try:
            age_lower = age_str.lower()

            if "hour" in age_lower or "minute" in age_lower:
                return date.today()
            elif "day" in age_lower:
                days = int("".join(filter(str.isdigit, age_str)) or "1")
                return date.today() - timedelta(days=days)
            elif "week" in age_lower:
                weeks = int("".join(filter(str.isdigit, age_str)) or "1")
                return date.today() - timedelta(weeks=weeks)
            elif "month" in age_lower:
                months = int("".join(filter(str.isdigit, age_str)) or "1")
                return date.today() - timedelta(days=months * 30)
            else:
                return date.today()
        except Exception:
            return None

    async def search_for_fund(
        self,
        fund_slug: str,
        freshness: str = "pw",
    ) -> List[BraveSearchResult]:
        """
        Search for funding news about a specific tracked fund.

        Args:
            fund_slug: The fund to search for (e.g., 'a16z', 'sequoia')
            freshness: pd=past day, pw=past week, pm=past month

        Returns:
            List of search results
        """
        query = self._build_fund_query(fund_slug)
        if not query:
            return []

        return await self.search_news(query, count=20, freshness=freshness)

    async def search_all_funds(
        self,
        freshness: str = "pw",
    ) -> Dict[str, List[BraveSearchResult]]:
        """
        Search for all 18 tracked funds.

        Returns:
            Dict mapping fund_slug to list of results
        """
        results: Dict[str, List[BraveSearchResult]] = {}

        for fund_slug in FUND_SEARCH_NAMES.keys():
            fund_results = await self.search_for_fund(fund_slug, freshness)
            results[fund_slug] = fund_results

            # Rate limit
            await asyncio.sleep(0.3)

        return results

    async def search_enterprise_ai(
        self,
        freshness: str = "pw",
    ) -> List[BraveSearchResult]:
        """
        Search for broad Enterprise AI funding news.

        Finds deals led by any investor (not just tracked funds).
        """
        query = self._build_enterprise_query()
        return await self.search_news(query, count=50, freshness=freshness)

    async def to_normalized_article(
        self,
        result: BraveSearchResult,
        fund_slug: str = "",
    ) -> NormalizedArticle:
        """Convert search result to NormalizedArticle."""
        # Combine description with extra snippets
        text_parts = [result.description]
        text_parts.extend(result.extra_snippets)
        text = "\n\n".join(filter(None, text_parts))

        return NormalizedArticle(
            url=result.url,
            title=result.title,
            text=text,
            published_date=result.published_date,
            author=result.source,
            tags=["brave_search", result.source],
            fund_slug=fund_slug,
            fetched_at=datetime.utcnow(),
        )

    async def scrape_all(
        self,
        freshness: str = "pw",
        include_enterprise: bool = True,
    ) -> List[NormalizedArticle]:
        """
        Full scraping pipeline.

        Args:
            freshness: pd=past day, pw=past week, pm=past month
            include_enterprise: Also search for general Enterprise AI deals

        Returns:
            Deduplicated list of NormalizedArticle objects
        """
        all_articles: List[NormalizedArticle] = []
        seen_urls: set = set()

        # Search for each tracked fund
        fund_results = await self.search_all_funds(freshness)

        for fund_slug, results in fund_results.items():
            for result in results:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)

                article = await self.to_normalized_article(result, fund_slug)
                all_articles.append(article)

        # Also search for broad Enterprise AI deals
        if include_enterprise:
            enterprise_results = await self.search_enterprise_ai(freshness)

            for result in enterprise_results:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)

                article = await self.to_normalized_article(result, "")
                all_articles.append(article)

        return all_articles


# Convenience function
async def run_brave_search_scraper(freshness: str = "pw") -> List[NormalizedArticle]:
    """Run Brave Search scraper and return normalized articles."""
    async with BraveSearchScraper() as scraper:
        return await scraper.scrape_all(freshness=freshness)

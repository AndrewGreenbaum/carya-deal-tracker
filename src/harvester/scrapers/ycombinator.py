"""
Y Combinator Scraper - Monitor YC for Demo Day companies and funding news.

Y Combinator runs 2 batches per year (Winter & Summer), each with ~200 companies.
Demo Day = 200+ companies launching twice per year, often raising funding soon after.

Data Sources:
1. YC Company Directory via Algolia API
2. YC Blog RSS: https://www.ycombinator.com/blog/feed
3. YC Top Companies: https://www.ycombinator.com/topcompanies
"""

import asyncio
import re
import httpx
import feedparser
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from bs4 import BeautifulSoup

from ..base_scraper import NormalizedArticle
from ...config.settings import settings


# YC Algolia API configuration (public, extracted from their website)
ALGOLIA_APP_ID = "45BWZJ1SGC"
ALGOLIA_API_KEY = "<SET_IN_ENV>"
ALGOLIA_INDEX = "YCCompany_production"

# YC batch identifiers (format: S/W + Year, e.g., S24, W24)
def get_current_batches() -> List[str]:
    """Get recent YC batch identifiers."""
    now = datetime.utcnow()
    year = now.year % 100  # 2024 -> 24
    batches = []

    # Current and previous 2 batches (Winter and Summer)
    for y in range(year + 1, year - 2, -1):
        batches.extend([f"W{y}", f"S{y}"])

    return batches[:6]  # Last 6 batches


YC_URLS = {
    "blog_rss": "https://www.ycombinator.com/blog/rss/",
    "top_companies": "https://www.ycombinator.com/topcompanies",
}

# YC has its own partners who sometimes lead/participate
YC_INVESTORS = [
    "Y Combinator", "YC", "YC Continuity",
]


@dataclass
class YCCompany:
    """Y Combinator company information."""
    name: str
    url: str
    description: str
    batch: str  # e.g., "S24", "W24"
    industry: str
    location: Optional[str]
    website: Optional[str]
    status: Optional[str]  # Active, Acquired, Public, etc.


@dataclass
class YCBlogPost:
    """YC Blog post."""
    title: str
    url: str
    description: str
    published: Optional[datetime]
    author: Optional[str]


class YCombinatorScraper:
    """
    Scraper for Y Combinator companies and news.

    Uses Algolia API for company data (same as YC website).
    Tracks:
    - Demo Day batches (200+ companies twice/year)
    - YC blog RSS for announcements
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
            }
        )
        self.current_batches = get_current_batches()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def fetch_companies_algolia(self, batch: str, hits_per_page: int = 100) -> List[YCCompany]:
        """Fetch companies from Algolia API for a specific batch."""
        try:
            url = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"

            headers = {
                "X-Algolia-Application-Id": ALGOLIA_APP_ID,
                "X-Algolia-API-Key": ALGOLIA_API_KEY,
                "Content-Type": "application/json",
            }

            # Search for companies in specific batch
            payload = {
                "query": "",
                "hitsPerPage": hits_per_page,
                "facetFilters": [f"batch:{batch}"],
            }

            response = await self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            data = response.json()
            companies = []

            for hit in data.get("hits", []):
                companies.append(YCCompany(
                    name=hit.get("name", ""),
                    url=f"https://www.ycombinator.com/companies/{hit.get('slug', '')}",
                    description=hit.get("one_liner", ""),
                    batch=hit.get("batch", batch),
                    industry=", ".join(hit.get("industries", [])) if hit.get("industries") else "",
                    location=hit.get("all_locations", ""),
                    website=hit.get("website", ""),
                    status=hit.get("status", ""),
                ))

            return companies

        except Exception as e:
            print(f"Error fetching YC companies from Algolia for batch {batch}: {e}")
            return []

    async def fetch_recent_batch_companies(self) -> List[YCCompany]:
        """Fetch companies from recent YC batches using Algolia."""
        all_companies = []
        seen_names = set()

        for batch in self.current_batches:
            companies = await self.fetch_companies_algolia(batch)

            for company in companies:
                if company.name and company.name not in seen_names:
                    seen_names.add(company.name)
                    all_companies.append(company)

            print(f"YC batch {batch}: {len(companies)} companies")
            await asyncio.sleep(0.5)  # Rate limit

        return all_companies

    async def fetch_blog_posts(self) -> List[YCBlogPost]:
        """Fetch recent YC blog posts from RSS feed."""
        posts = []

        try:
            response = await self.client.get(YC_URLS["blog_rss"])
            response.raise_for_status()

            feed = feedparser.parse(response.text)

            for entry in feed.entries[:20]:  # Last 20 posts
                # Parse publication date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6])

                # Get description
                description = entry.get('summary', '')
                if description:
                    soup = BeautifulSoup(description, 'lxml')
                    description = soup.get_text(strip=True)[:500]

                posts.append(YCBlogPost(
                    title=entry.get('title', ''),
                    url=entry.get('link', ''),
                    description=description,
                    published=pub_date,
                    author=entry.get('author', None),
                ))

        except Exception as e:
            print(f"Error fetching YC blog RSS: {e}")

        return posts

    def is_enterprise_ai_company(self, company: YCCompany) -> bool:
        """Check if company is Enterprise AI focused."""
        enterprise_keywords = [
            "b2b", "enterprise", "saas", "infrastructure", "api", "platform",
            "developer", "devtools", "ai", "ml", "llm", "automation",
            "workflow", "agent", "copilot"
        ]
        text = f"{company.name} {company.description} {company.industry}".lower()
        return any(kw in text for kw in enterprise_keywords)

    async def company_to_article(self, company: YCCompany) -> NormalizedArticle:
        """Convert YC company to NormalizedArticle for tracking."""
        text_parts = [
            f"Y Combinator Company: {company.name}",
            f"Batch: {company.batch}",
            f"Description: {company.description}",
        ]

        if company.industry:
            text_parts.append(f"Industry: {company.industry}")
        if company.location:
            text_parts.append(f"Location: {company.location}")
        if company.website:
            text_parts.append(f"Website: {company.website}")

        text_parts.append(f"\nYC Profile: {company.url}")
        text_parts.append("\nNote: Y Combinator Demo Day company - potential early-stage investment opportunity")

        return NormalizedArticle(
            url=company.url,
            title=f"YC {company.batch}: {company.name} - {company.description[:100]}",
            text="\n".join(text_parts),
            published_date=date.today(),
            author="Y Combinator",
            tags=['ycombinator', 'demo_day', company.batch, company.industry.lower()] if company.industry else ['ycombinator', 'demo_day', company.batch],
            fund_slug="",  # YC is the fund here
            fetched_at=datetime.utcnow(),
        )

    async def scrape_all(self, include_all_batches: bool = False) -> List[NormalizedArticle]:
        """
        Full scraping pipeline for Y Combinator.

        Args:
            include_all_batches: If True, fetch all recent batches. If False, only current.

        Returns:
            List of NormalizedArticle objects for companies and blog posts.
        """
        articles = []

        # Get companies from recent batches
        companies = await self.fetch_recent_batch_companies()

        # Filter for Enterprise AI if needed
        for company in companies:
            article = await self.company_to_article(company)
            articles.append(article)
            await asyncio.sleep(0.1)

        # Get blog posts for announcements
        blog_posts = await self.fetch_blog_posts()
        for post in blog_posts:
            articles.append(NormalizedArticle(
                url=post.url,
                title=post.title,
                text=f"{post.title}\n\n{post.description}",
                published_date=post.published.date() if post.published else date.today(),
                author=post.author,
                tags=['ycombinator', 'blog'],
                fund_slug="",
                fetched_at=datetime.utcnow(),
            ))

        return articles


# Convenience function
async def run_ycombinator_scraper() -> List[NormalizedArticle]:
    """Run Y Combinator scraper and return articles."""
    async with YCombinatorScraper() as scraper:
        return await scraper.scrape_all()

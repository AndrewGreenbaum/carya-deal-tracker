"""
Y Combinator Scraper - Monitor YC for Demo Day companies and funding news.

Y Combinator runs 2 batches per year (Winter & Summer), each with ~200 companies.
Demo Day = 200+ companies launching twice per year, often raising funding soon after.

Data Sources:
1. YC Company Directory: https://www.ycombinator.com/companies
2. YC Blog: https://www.ycombinator.com/blog
3. YC Top Companies: https://www.ycombinator.com/topcompanies
4. Hacker News "Launch HN" posts
"""

import asyncio
import re
import httpx
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from bs4 import BeautifulSoup

from ..base_scraper import NormalizedArticle
from ...config.settings import settings


# YC batch identifiers (format: S/W + Year, e.g., S24, W24)
def get_current_batches() -> List[str]:
    """Get recent YC batch identifiers."""
    now = datetime.utcnow()
    year = now.year % 100  # 2024 -> 24
    batches = []

    # Current and previous 2 batches
    for y in range(year, year - 2, -1):
        batches.extend([f"S{y}", f"W{y}"])

    return batches[:4]  # Last 4 batches


YC_URLS = {
    "companies": "https://www.ycombinator.com/companies",
    "blog": "https://www.ycombinator.com/blog",
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

    Tracks:
    - Demo Day batches (200+ companies twice/year)
    - YC blog for announcements
    - Top companies list
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        self.current_batches = get_current_batches()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def fetch_companies_page(self, batch: Optional[str] = None) -> str:
        """Fetch YC companies page."""
        try:
            url = YC_URLS["companies"]
            if batch:
                url = f"{url}?batch={batch}"

            response = await self.client.get(url)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"Error fetching YC companies: {e}")
            return ""

    async def parse_companies(self, html: str) -> List[YCCompany]:
        """Parse companies from YC directory page."""
        companies = []
        soup = BeautifulSoup(html, 'lxml')

        # YC uses React/Next.js with JSON data in script tags
        script_tags = soup.find_all('script', type='application/json')
        for script in script_tags:
            try:
                import json
                data = json.loads(script.string)
                # Navigate through the nested structure
                if isinstance(data, dict) and 'props' in data:
                    page_props = data.get('props', {}).get('pageProps', {})
                    company_list = page_props.get('companies', [])

                    for c in company_list:
                        companies.append(YCCompany(
                            name=c.get('name', ''),
                            url=f"https://www.ycombinator.com/companies/{c.get('slug', '')}",
                            description=c.get('one_liner', ''),
                            batch=c.get('batch', ''),
                            industry=c.get('industry', ''),
                            location=c.get('location', ''),
                            website=c.get('website', ''),
                            status=c.get('status', ''),
                        ))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        # Fallback: parse HTML directly if JSON not found
        if not companies:
            for company_el in soup.select('[class*="company-"]'):
                name_el = company_el.select_one('h2, .company-name')
                desc_el = company_el.select_one('p, .company-description')
                link_el = company_el.select_one('a[href*="/companies/"]')

                if name_el and link_el:
                    companies.append(YCCompany(
                        name=name_el.get_text(strip=True),
                        url=link_el.get('href', ''),
                        description=desc_el.get_text(strip=True) if desc_el else '',
                        batch='',
                        industry='',
                        location=None,
                        website=None,
                        status=None,
                    ))

        return companies

    async def fetch_recent_batch_companies(self) -> List[YCCompany]:
        """Fetch companies from recent YC batches."""
        all_companies = []
        seen_names = set()

        for batch in self.current_batches:
            html = await self.fetch_companies_page(batch=batch)
            companies = await self.parse_companies(html)

            for company in companies:
                if company.name not in seen_names:
                    seen_names.add(company.name)
                    all_companies.append(company)

            await asyncio.sleep(1)  # Rate limit

        return all_companies

    async def fetch_blog_posts(self) -> List[YCBlogPost]:
        """Fetch recent YC blog posts."""
        posts = []

        try:
            response = await self.client.get(YC_URLS["blog"])
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'lxml')

            for article in soup.select('article, .post, .blog-post'):
                title_el = article.select_one('h1, h2, h3, .title')
                link_el = article.select_one('a')
                desc_el = article.select_one('p, .excerpt, .summary')
                date_el = article.select_one('time, .date')

                if title_el and link_el:
                    pub_date = None
                    if date_el:
                        date_str = date_el.get('datetime') or date_el.get_text(strip=True)
                        try:
                            from dateutil import parser as date_parser
                            pub_date = date_parser.parse(date_str)
                        except (ValueError, TypeError):
                            pass

                    url = link_el.get('href', '')
                    if not url.startswith('http'):
                        url = f"https://www.ycombinator.com{url}"

                    posts.append(YCBlogPost(
                        title=title_el.get_text(strip=True),
                        url=url,
                        description=desc_el.get_text(strip=True) if desc_el else '',
                        published=pub_date,
                        author=None,
                    ))

        except Exception as e:
            print(f"Error fetching YC blog: {e}")

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

"""
Stealth Startup Detector - Catch Deals Before They're Announced.

Monitors the web for signals of stealth/newly-funded companies
that haven't made public announcements yet.

Key signals:
- "Emerges from stealth" / "exits stealth" announcements
- Companies "backed by [VC name]" in stealth mode
- Stealth startups hiring (founding teams)
- Early funding news before official PR

Uses Brave Search API to find stealth startup signals.
"""

import asyncio
import logging
import httpx
from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional, Dict, Any

from ..base_scraper import NormalizedArticle
from ..fund_matcher import match_fund_name
from ...config.settings import settings

logger = logging.getLogger(__name__)


# Brave Search API (reuse existing key)
BRAVE_WEB_API = "https://api.search.brave.com/res/v1/web/search"

# Stealth job search queries - broader search without site: restriction
# Brave Search doesn't index LinkedIn Jobs well, so we search for stealth company news instead
STEALTH_JOB_QUERIES = [
    # Stealth company hiring news (catches PR about hiring)
    '"stealth startup" hiring',
    '"stealth mode" startup hiring engineers',
    '"stealth startup" founding team AI',
    '"well-funded startup" hiring "AI"',

    # VC-backed stealth (most valuable signals)
    '"backed by Sequoia" stealth',
    '"backed by a16z" stealth',
    '"backed by Andreessen Horowitz" stealth',
    '"backed by Founders Fund" stealth',
    '"backed by Benchmark" stealth',
    '"backed by Greylock" stealth',

    # Stealth funding announcements
    '"emerges from stealth" funding',
    '"exits stealth" raises',
    '"comes out of stealth" million',
    '"launches from stealth" series',

    # Enterprise AI stealth
    '"stealth" "enterprise AI" startup',
    '"stealth" "B2B" AI company',
    '"stealth" "AI infrastructure" startup',
]

# NOTE: Fund matching now uses centralized fund_matcher.py
# which handles name variants, negative keywords, and disambiguation


@dataclass
class LinkedInJob:
    """Parsed LinkedIn job posting."""
    title: str
    company_name: str
    url: str
    description: str
    location: Optional[str]
    posted_date: Optional[date]
    is_stealth: bool
    matched_fund: Optional[str]


class LinkedInJobsScraper:
    """
    Scraper for LinkedIn job posts indicating stealth startups.

    Uses Brave Search API to find LinkedIn job posts that mention:
    - Stealth startup/mode
    - VC backing (tracked funds)
    - Recent funding

    This catches companies BEFORE they announce publicly.
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

    async def search_brave(
        self,
        query: str,
        count: int = 20,
    ) -> List[Dict[str, Any]]:
        """Execute web search via Brave API."""
        if not self.api_key:
            return []

        try:
            params = {
                "q": query,
                "count": count,
                "freshness": "pm",  # Past month
                "safesearch": "off",
            }

            response = await self.client.get(BRAVE_WEB_API, params=params)
            response.raise_for_status()
            data = response.json()

            return data.get("web", {}).get("results", [])

        except Exception as e:
            logger.error(f"LinkedIn Jobs Brave search error: {e}")
            return []

    def parse_job_result(self, result: Dict[str, Any]) -> Optional[LinkedInJob]:
        """Parse Brave search result into LinkedInJob (or stealth signal)."""
        try:
            url = result.get("url", "")
            title = result.get("title", "")
            description = result.get("description", "")

            # Skip if no content
            if not title or not url:
                return None

            # Extract company name from title or description
            # Try common patterns in job titles
            company_name = ""
            if " at " in title:
                parts = title.split(" at ")
                if len(parts) >= 2:
                    company_name = parts[-1].split(" | ")[0].strip()
            elif " - " in title:
                parts = title.split(" - ")
                if len(parts) >= 2:
                    company_name = parts[1].split(" | ")[0].strip()
            elif ":" in title:
                company_name = title.split(":")[0].strip()

            # Fallback: use truncated title if no pattern matched
            if not company_name and title:
                company_name = title[:50] + "..." if len(title) > 50 else title

            # Check if stealth
            text_lower = f"{title} {description}".lower()
            is_stealth = any(kw in text_lower for kw in [
                "stealth", "stealth mode", "stealth startup",
                "well-funded startup", "recently funded",
                "pre-launch", "unannounced"
            ])

            # Check for fund matches using centralized fund_matcher
            # (handles name variants, negative keywords, and disambiguation)
            matched_fund = match_fund_name(f"{title} {description}")

            return LinkedInJob(
                title=title,
                company_name=company_name,
                url=url,
                description=description,
                location=None,  # Not easily available from search
                posted_date=date.today(),  # Approximate
                is_stealth=is_stealth,
                matched_fund=matched_fund,
            )

        except Exception as e:
            logger.error(f"Error parsing LinkedIn job: {e}")
            return None

    def job_to_article(self, job: LinkedInJob) -> NormalizedArticle:
        """Convert LinkedIn job to NormalizedArticle for processing."""
        # Build text with all stealth signals
        text_parts = [
            f"STEALTH SIGNAL: LinkedIn Job Posting",
            f"Company: {job.company_name}",
            f"Job: {job.title}",
            f"",
            f"Description: {job.description}",
            f"",
            f"LinkedIn URL: {job.url}",
        ]

        if job.is_stealth:
            text_parts.insert(0, "[STEALTH STARTUP DETECTED]")

        if job.matched_fund:
            text_parts.append(f"\nMatched Fund: {job.matched_fund}")

        tags = ["linkedin_jobs", "stealth_signal"]
        if job.is_stealth:
            tags.append("stealth")
        if job.matched_fund:
            tags.append(f"fund:{job.matched_fund}")

        return NormalizedArticle(
            url=job.url,
            title=f"Stealth Signal: {job.company_name} - {job.title[:50]}",
            text="\n".join(text_parts),
            published_date=job.posted_date or date.today(),
            author="LinkedIn Jobs",
            tags=tags,
            fund_slug=job.matched_fund or "",
            fetched_at=datetime.utcnow(),
        )

    async def scrape_all(self) -> List[NormalizedArticle]:
        """
        Full scraping pipeline for LinkedIn stealth jobs.

        Runs queries against Brave Search API to find LinkedIn job posts
        indicating stealth/funded startups.

        Returns:
            List of NormalizedArticle objects for stealth signals.
        """
        if not self.api_key:
            logger.warning("BRAVE_SEARCH_KEY not configured - skipping LinkedIn Jobs")
            return []

        all_jobs: List[LinkedInJob] = []
        seen_urls: set = set()

        for query in STEALTH_JOB_QUERIES:
            results = await self.search_brave(query, count=10)

            for result in results:
                job = self.parse_job_result(result)
                if job and job.url not in seen_urls:
                    seen_urls.add(job.url)
                    all_jobs.append(job)

            await asyncio.sleep(0.3)  # Rate limit

        # Convert to articles
        articles = []
        for job in all_jobs:
            # Only include if stealth or has fund match
            if job.is_stealth or job.matched_fund:
                articles.append(self.job_to_article(job))

        return articles


# Convenience function
async def run_linkedin_jobs_scraper() -> List[NormalizedArticle]:
    """Run LinkedIn Jobs scraper and return articles."""
    async with LinkedInJobsScraper() as scraper:
        return await scraper.scrape_all()

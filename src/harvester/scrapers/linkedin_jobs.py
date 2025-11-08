"""
LinkedIn Jobs Scraper - Stealth Startup Detection.

Stealth startups HIRE before they ANNOUNCE. This scraper monitors
LinkedIn job postings for signals of stealth/newly-funded companies.

Key signals:
- Jobs mentioning "stealth startup" or "stealth mode"
- Jobs at companies "backed by [VC name]"
- Recent company founding + aggressive hiring
- Job descriptions mentioning recent funding

Uses Brave Search API to find LinkedIn job posts (avoids LinkedIn blocks).
"""

import asyncio
import re
import httpx
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any

from ..base_scraper import NormalizedArticle
from ...config.settings import settings


# Brave Search API (reuse existing key)
BRAVE_WEB_API = "https://api.search.brave.com/res/v1/web/search"

# Stealth job search queries
STEALTH_JOB_QUERIES = [
    # Direct stealth mentions
    'site:linkedin.com/jobs "stealth startup"',
    'site:linkedin.com/jobs "stealth mode" startup',
    'site:linkedin.com/jobs "well-funded startup" AI',
    'site:linkedin.com/jobs "recently funded" startup',

    # VC-backed company jobs (most valuable)
    'site:linkedin.com/jobs "backed by Sequoia"',
    'site:linkedin.com/jobs "backed by a16z"',
    'site:linkedin.com/jobs "backed by Andreessen"',
    'site:linkedin.com/jobs "backed by Founders Fund"',
    'site:linkedin.com/jobs "backed by Benchmark"',
    'site:linkedin.com/jobs "backed by Greylock"',
    'site:linkedin.com/jobs "backed by Thrive"',
    'site:linkedin.com/jobs "backed by Accel"',
    'site:linkedin.com/jobs "backed by Index"',
    'site:linkedin.com/jobs "backed by Bessemer"',

    # Enterprise AI stealth
    'site:linkedin.com/jobs "stealth" "enterprise AI"',
    'site:linkedin.com/jobs "stealth" "B2B SaaS"',
    'site:linkedin.com/jobs "stealth" "AI infrastructure"',
]

# Fund slug mapping for detection
FUND_PATTERNS = {
    "founders_fund": ["founders fund", "peter thiel"],
    "benchmark": ["benchmark"],
    "sequoia": ["sequoia"],
    "khosla": ["khosla"],
    "index": ["index ventures"],
    "a16z": ["a16z", "andreessen"],
    "insight": ["insight partners"],
    "bessemer": ["bessemer", "bvp"],
    "redpoint": ["redpoint"],
    "greylock": ["greylock"],
    "gv": ["google ventures", "gv "],
    "menlo": ["menlo ventures"],
    "usv": ["union square"],
    "thrive": ["thrive capital"],
    "accel": ["accel"],
    "felicis": ["felicis"],
    "general_catalyst": ["general catalyst"],
    "first_round": ["first round"],
}


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
            print(f"LinkedIn Jobs Brave search error: {e}")
            return []

    def parse_job_result(self, result: Dict[str, Any]) -> Optional[LinkedInJob]:
        """Parse Brave search result into LinkedInJob."""
        try:
            url = result.get("url", "")
            title = result.get("title", "")
            description = result.get("description", "")

            # Only process LinkedIn job URLs
            if "linkedin.com/jobs" not in url.lower():
                return None

            # Extract company name from title (usually "Job Title at Company")
            company_name = ""
            if " at " in title:
                parts = title.split(" at ")
                if len(parts) >= 2:
                    company_name = parts[-1].split(" | ")[0].strip()
            elif " - " in title:
                parts = title.split(" - ")
                if len(parts) >= 2:
                    company_name = parts[1].split(" | ")[0].strip()

            # Check if stealth
            text_lower = f"{title} {description}".lower()
            is_stealth = any(kw in text_lower for kw in [
                "stealth", "stealth mode", "stealth startup",
                "well-funded startup", "recently funded",
                "pre-launch", "unannounced"
            ])

            # Check for fund matches
            matched_fund = None
            for fund_slug, patterns in FUND_PATTERNS.items():
                for pattern in patterns:
                    if pattern.lower() in text_lower:
                        matched_fund = fund_slug
                        break
                if matched_fund:
                    break

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
            print(f"Error parsing LinkedIn job: {e}")
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
            text_parts.insert(0, "⚠️ STEALTH STARTUP DETECTED")

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
            print("BRAVE_SEARCH_KEY not configured - skipping LinkedIn Jobs")
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

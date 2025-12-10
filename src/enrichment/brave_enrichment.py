"""
Company Enrichment via Brave Search.

Uses Brave Search to find:
- Company official website
- CEO/Founder LinkedIn profiles

This is a FREE approach using existing Brave Search infrastructure.
"""

import asyncio
import re
import httpx
from dataclasses import dataclass
from typing import Optional, List, Dict
from urllib.parse import urlparse

from ..config.settings import settings


@dataclass
class BraveEnrichmentResult:
    """Result of company enrichment."""
    company_name: str
    website: Optional[str] = None
    ceo_name: Optional[str] = None
    ceo_linkedin: Optional[str] = None
    founder_linkedin: Optional[str] = None  # Fallback if no CEO found
    enrichment_source: str = "brave_search"


# Common website patterns to identify official sites
OFFICIAL_SITE_PATTERNS = [
    r"^https?://(?:www\.)?([a-z0-9-]+)\.(com|io|ai|co|dev|app|tech|cloud)/?$",
]

# LinkedIn profile URL pattern
LINKEDIN_PROFILE_PATTERN = r"https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+"

# Skip these domains (not official company sites)
SKIP_DOMAINS = {
    "crunchbase.com",
    "linkedin.com",
    "twitter.com",
    "facebook.com",
    "bloomberg.com",
    "techcrunch.com",
    "forbes.com",
    "reuters.com",
    "wikipedia.org",
    "pitchbook.com",
    "cbinsights.com",
    "tracxn.com",
    "owler.com",
    "zoominfo.com",
    "apollo.io",
    "glassdoor.com",
    "indeed.com",
    "ycombinator.com",
    "producthunt.com",
    "github.com",
    "youtube.com",
    "medium.com",
    "substack.com",
    "sec.gov",
}


class BraveEnrichmentClient:
    """Client for enriching company data via Brave Search."""

    def __init__(self):
        self.api_key = settings.brave_search_key
        self.base_url = "https://api.search.brave.com/res/v1/web/search"
        self.client = httpx.AsyncClient(
            timeout=15,
            headers={
                "X-Subscription-Token": self.api_key or "",
                "Accept": "application/json",
            },
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _is_official_website(self, url: str, company_name: str) -> bool:
        """Check if URL looks like an official company website."""
        try:
            parsed = urlparse(url.lower())
            domain = parsed.netloc.replace("www.", "")

            # Skip known non-official domains
            for skip in SKIP_DOMAINS:
                if skip in domain:
                    return False

            # Check if company name appears in domain
            company_slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
            domain_slug = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])

            # Fuzzy match: company name should be similar to domain
            if company_slug in domain_slug or domain_slug in company_slug:
                return True

            # Check for common TLDs with short paths
            if parsed.path in ("", "/") and domain.split(".")[-1] in ("com", "io", "ai", "co", "dev", "app"):
                return True

            return False
        except Exception:
            return False

    def _extract_linkedin_url(self, text: str) -> Optional[str]:
        """Extract LinkedIn profile URL from text."""
        match = re.search(LINKEDIN_PROFILE_PATTERN, text)
        if match:
            url = match.group(0)
            # Skip search/company pages
            if "/search" not in url and "/company/" not in url:
                return url
        return None

    async def _search(self, query: str, count: int = 5) -> List[Dict]:
        """Execute Brave Search query."""
        if not self.api_key:
            return []

        try:
            response = await self.client.get(
                self.base_url,
                params={
                    "q": query,
                    "count": count,
                    "safesearch": "off",
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("web", {}).get("results", [])
        except Exception as e:
            print(f"Brave Search error: {e}")
            return []

    async def find_website(self, company_name: str) -> Optional[str]:
        """Find official company website."""
        query = f'"{company_name}" official website'
        results = await self._search(query, count=10)

        for result in results:
            url = result.get("url", "")
            if self._is_official_website(url, company_name):
                return url

        # Fallback: try simpler query
        query = f"{company_name} company"
        results = await self._search(query, count=5)

        for result in results:
            url = result.get("url", "")
            if self._is_official_website(url, company_name):
                return url

        return None

    async def find_ceo_linkedin(self, company_name: str) -> tuple[Optional[str], Optional[str]]:
        """
        Find CEO/Founder LinkedIn profile.

        Returns (ceo_name, linkedin_url)
        """
        # Try CEO first
        query = f'"{company_name}" CEO LinkedIn'
        results = await self._search(query, count=5)

        for result in results:
            url = result.get("url", "")
            title = result.get("title", "")
            description = result.get("description", "")

            linkedin_url = self._extract_linkedin_url(url)
            if linkedin_url:
                # Try to extract name from title
                # LinkedIn titles are usually "Name - Title | LinkedIn"
                name_match = re.match(r"^([^-|]+)", title)
                name = name_match.group(1).strip() if name_match else None
                return (name, linkedin_url)

            # Check description for LinkedIn URL
            linkedin_url = self._extract_linkedin_url(description)
            if linkedin_url:
                return (None, linkedin_url)

        # Fallback: try Founder
        query = f'"{company_name}" Founder LinkedIn'
        results = await self._search(query, count=5)

        for result in results:
            url = result.get("url", "")
            linkedin_url = self._extract_linkedin_url(url)
            if linkedin_url:
                name_match = re.match(r"^([^-|]+)", result.get("title", ""))
                name = name_match.group(1).strip() if name_match else None
                return (name, linkedin_url)

        return (None, None)

    async def enrich(self, company_name: str) -> BraveEnrichmentResult:
        """
        Enrich company with website and CEO LinkedIn.

        Uses 2 Brave Search queries per company.
        """
        result = BraveEnrichmentResult(company_name=company_name)

        # Find website (1 query)
        result.website = await self.find_website(company_name)
        await asyncio.sleep(0.2)  # Rate limit

        # Find CEO LinkedIn (1 query)
        ceo_name, linkedin_url = await self.find_ceo_linkedin(company_name)
        result.ceo_name = ceo_name
        result.ceo_linkedin = linkedin_url

        return result


async def enrich_company(company_name: str) -> BraveEnrichmentResult:
    """
    Enrich a single company with website and CEO LinkedIn.

    Uses Brave Search (2 queries per company).
    """
    async with BraveEnrichmentClient() as client:
        return await client.enrich(company_name)


async def enrich_companies_batch(
    company_names: List[str],
    delay_seconds: float = 0.5,
) -> Dict[str, BraveEnrichmentResult]:
    """
    Enrich multiple companies with rate limiting.

    Args:
        company_names: List of company names to enrich
        delay_seconds: Delay between companies to avoid rate limits

    Returns:
        Dict mapping company name to enrichment result
    """
    results = {}

    async with BraveEnrichmentClient() as client:
        for name in company_names:
            try:
                result = await client.enrich(name)
                results[name] = result
            except Exception as e:
                print(f"Error enriching {name}: {e}")
                results[name] = BraveEnrichmentResult(company_name=name)

            await asyncio.sleep(delay_seconds)

    return results

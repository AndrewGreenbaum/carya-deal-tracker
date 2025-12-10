"""
Company Enrichment via Brave Search.

Uses Brave Search to find:
- Company official website
- CEO/Founder LinkedIn profiles

IMPROVED: Better startup-specific queries to avoid matching wrong entities.
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
    founder_linkedin: Optional[str] = None
    enrichment_source: str = "brave_search"


# LinkedIn profile URL pattern
LINKEDIN_PROFILE_PATTERN = r"https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+"

# Skip these domains (not startup company sites)
SKIP_DOMAINS = {
    # News/Media
    "crunchbase.com", "linkedin.com", "twitter.com", "facebook.com",
    "bloomberg.com", "techcrunch.com", "forbes.com", "reuters.com",
    "wikipedia.org", "pitchbook.com", "cbinsights.com", "tracxn.com",
    "owler.com", "zoominfo.com", "apollo.io", "glassdoor.com", "indeed.com",
    "ycombinator.com", "producthunt.com", "github.com", "youtube.com",
    "medium.com", "substack.com", "sec.gov", "businesswire.com", "prnewswire.com",
    # Entertainment/Sports (false positives)
    "spotify.com", "apple.com", "amazon.com", "google.com",
    "rangers.co.uk", "leonalewismusic.com", "apache.org",
    "music.com", "band.com", "artist.com",
    # Generic
    "wix.com", "squarespace.com", "wordpress.com", "blogspot.com",
}

# Known wrong matches to skip
KNOWN_FALSE_POSITIVES = {
    "maven": ["apache.org", "maven.apache.org"],
    "leona": ["leonalewis", "music"],
    "ranger": ["rangers.co.uk", "rangerfc", "football", "soccer"],
    "unknown": ["weareunknown"],
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

    def _is_valid_startup_website(self, url: str, company_name: str) -> bool:
        """Check if URL is a valid startup website (not a false positive)."""
        try:
            url_lower = url.lower()
            parsed = urlparse(url_lower)
            domain = parsed.netloc.replace("www.", "")

            # Skip known non-startup domains
            for skip in SKIP_DOMAINS:
                if skip in domain:
                    return False

            # Check known false positives for this company name
            company_lower = company_name.lower()
            for keyword, false_urls in KNOWN_FALSE_POSITIVES.items():
                if keyword in company_lower:
                    for false_url in false_urls:
                        if false_url in url_lower:
                            return False

            # Must have a reasonable TLD
            valid_tlds = ("com", "io", "ai", "co", "dev", "app", "tech", "cloud", "xyz", "me")
            tld = domain.split(".")[-1]
            if tld not in valid_tlds:
                return False

            # Company name should appear in domain (fuzzy match)
            company_slug = re.sub(r"[^a-z0-9]", "", company_lower)
            domain_slug = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])

            # At least partial match
            if len(company_slug) >= 4:
                if company_slug[:4] in domain_slug or domain_slug in company_slug:
                    return True

            # Short names need exact match
            if company_slug == domain_slug:
                return True

            return False
        except Exception:
            return False

    def _extract_linkedin_url(self, text: str) -> Optional[str]:
        """Extract LinkedIn profile URL from text."""
        match = re.search(LINKEDIN_PROFILE_PATTERN, text)
        if match:
            url = match.group(0)
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
        """Find official startup website with funding context."""
        # Skip invalid names
        if not company_name or company_name.lower() in ("<unknown>", "unknown", "n/a", "no funding deal"):
            return None

        # Try startup-specific query first
        query = f'"{company_name}" startup funding official website'
        results = await self._search(query, count=10)

        for result in results:
            url = result.get("url", "")
            if self._is_valid_startup_website(url, company_name):
                return url

        # Fallback: try with "company" context
        query = f'"{company_name}" company venture capital'
        results = await self._search(query, count=5)

        for result in results:
            url = result.get("url", "")
            if self._is_valid_startup_website(url, company_name):
                return url

        return None

    async def find_ceo_linkedin(self, company_name: str) -> tuple[Optional[str], Optional[str]]:
        """Find CEO/Founder LinkedIn with startup context."""
        if not company_name or company_name.lower() in ("<unknown>", "unknown", "n/a"):
            return (None, None)

        # Try CEO with startup context
        query = f'"{company_name}" startup CEO founder LinkedIn'
        results = await self._search(query, count=5)

        for result in results:
            url = result.get("url", "")
            title = result.get("title", "")
            description = result.get("description", "")

            # Check if this result is related to our company
            result_text = f"{title} {description}".lower()
            company_lower = company_name.lower()

            # Company name should appear in the result
            if company_lower not in result_text and company_lower.split()[0] not in result_text:
                continue

            linkedin_url = self._extract_linkedin_url(url)
            if linkedin_url:
                name_match = re.match(r"^([^-|]+)", title)
                name = name_match.group(1).strip() if name_match else None
                return (name, linkedin_url)

            linkedin_url = self._extract_linkedin_url(description)
            if linkedin_url:
                return (None, linkedin_url)

        return (None, None)

    async def enrich(self, company_name: str) -> BraveEnrichmentResult:
        """Enrich company with website and CEO LinkedIn."""
        result = BraveEnrichmentResult(company_name=company_name)

        # Skip invalid names
        if not company_name or len(company_name) < 2:
            return result

        invalid_names = {"<unknown>", "unknown", "n/a", "no funding deal", "no specific funding deal"}
        if company_name.lower() in invalid_names:
            return result

        # Find website
        result.website = await self.find_website(company_name)
        await asyncio.sleep(0.2)

        # Find CEO LinkedIn
        ceo_name, linkedin_url = await self.find_ceo_linkedin(company_name)
        result.ceo_name = ceo_name
        result.ceo_linkedin = linkedin_url

        return result


async def enrich_company(company_name: str) -> BraveEnrichmentResult:
    """Enrich a single company with website and CEO LinkedIn."""
    async with BraveEnrichmentClient() as client:
        return await client.enrich(company_name)


async def enrich_companies_batch(
    company_names: List[str],
    delay_seconds: float = 0.5,
) -> Dict[str, BraveEnrichmentResult]:
    """Enrich multiple companies with rate limiting."""
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

"""
Company Enrichment via Brave Search.

Uses Brave Search to find:
- Company official website
- CEO/Founder LinkedIn profiles

OPTIMIZED: Uses shared BraveClient (no duplicate HTTP client or retry logic).
"""

import asyncio
import logging
import re

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from urllib.parse import urlparse

from ..common.brave_client import get_brave_client
from ..config.settings import settings


@dataclass
class DealContext:
    """Context from deal for better enrichment."""
    company_name: str
    lead_investor: Optional[str] = None
    founders: List[Dict] = field(default_factory=list)  # [{"name": "...", "title": "..."}]
    source_url: Optional[str] = None
    enterprise_category: Optional[str] = None


@dataclass
class BraveEnrichmentResult:
    """Result of company enrichment."""
    company_name: str
    website: Optional[str] = None
    ceo_name: Optional[str] = None
    ceo_linkedin: Optional[str] = None
    founder_linkedins: Dict[str, str] = field(default_factory=dict)  # {name: linkedin_url}
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
    "leona": ["leonalewis", "music", "leonalewismusic"],
    "ranger": ["rangers.co.uk", "rangerfc", "football", "soccer"],
    "unknown": ["weareunknown"],
}

# Valid TLDs for startup websites
VALID_TLDS = ("com", "io", "ai", "co", "dev", "app", "tech", "cloud", "xyz", "me", "health", "bio", "so", "gg")


class BraveEnrichmentClient:
    """
    Client for enriching company data via Brave Search.

    OPTIMIZED: Uses shared BraveClient instead of creating its own HTTP client.
    """

    def __init__(self):
        self.rate_limit_delay = settings.brave_search_rate_limit_delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass  # Client is shared, don't close it

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
            tld = domain.split(".")[-1]
            if tld not in VALID_TLDS:
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
        """Execute Brave Search query using shared client."""
        client = get_brave_client()
        if not client.validate_api_key():
            logger.warning("BRAVE_SEARCH_KEY not configured for enrichment")
            return []

        data = await client.search_web(query, count)
        if data is None:
            return []

        return data.get("web", {}).get("results", [])

    async def find_website_with_context(
        self,
        company_name: str,
        investor: Optional[str] = None,
        category: Optional[str] = None
    ) -> Optional[str]:
        """Find official startup website using investor context for disambiguation."""
        if not company_name or company_name.lower() in ("<unknown>", "unknown", "n/a", "no funding deal"):
            return None

        # Build context-aware query
        query_parts = [f'"{company_name}"']

        # Add investor for disambiguation (most important!)
        if investor:
            investor_short = investor.split()[0] if investor else ""
            query_parts.append(f'"{investor_short}"')

        # Add category context
        if category:
            category_map = {
                "vertical_saas": "healthcare software",
                "infrastructure": "developer tools",
                "security": "cybersecurity",
                "agentic": "AI automation",
                "data_intelligence": "enterprise data",
            }
            query_parts.append(category_map.get(category, "startup"))
        else:
            query_parts.append("startup")

        query_parts.append("official website")
        query = " ".join(query_parts)

        results = await self._search(query, count=10)

        for result in results:
            url = result.get("url", "")
            if self._is_valid_startup_website(url, company_name):
                parsed = urlparse(url)
                return f"https://{parsed.netloc}"

        # Fallback: try simpler query with investor
        if investor:
            query = f'"{company_name}" "{investor}" startup'
            results = await self._search(query, count=5)
            for result in results:
                url = result.get("url", "")
                if self._is_valid_startup_website(url, company_name):
                    parsed = urlparse(url)
                    return f"https://{parsed.netloc}"

        return None

    async def find_founder_linkedin(
        self,
        founder_name: str,
        company_name: str
    ) -> Optional[str]:
        """Find LinkedIn for a specific founder by name."""
        if not founder_name or len(founder_name) < 3:
            return None

        # Skip generic names
        if founder_name.lower() in ("ceo", "founder", "unknown"):
            return None

        # Search for specific founder + company
        query = f'"{founder_name}" "{company_name}" LinkedIn'
        results = await self._search(query, count=5)

        for result in results:
            url = result.get("url", "")
            title = result.get("title", "").lower()

            # Must be a LinkedIn profile URL
            linkedin_url = self._extract_linkedin_url(url)
            if linkedin_url:
                # Verify founder name appears in result
                founder_first = founder_name.split()[0].lower()
                if founder_first in title:
                    return linkedin_url

        # Fallback: search without company name
        query = f'"{founder_name}" LinkedIn founder'
        results = await self._search(query, count=3)

        for result in results:
            url = result.get("url", "")
            linkedin_url = self._extract_linkedin_url(url)
            if linkedin_url:
                return linkedin_url

        return None

    async def find_ceo_linkedin(self, company_name: str) -> tuple[Optional[str], Optional[str]]:
        """Find CEO/Founder LinkedIn with startup context (legacy method)."""
        if not company_name or company_name.lower() in ("<unknown>", "unknown", "n/a"):
            return (None, None)

        query = f'"{company_name}" startup CEO founder LinkedIn'
        results = await self._search(query, count=5)

        for result in results:
            url = result.get("url", "")
            title = result.get("title", "")
            description = result.get("description", "")

            result_text = f"{title} {description}".lower()
            company_lower = company_name.lower()

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

    async def enrich_with_context(self, context: DealContext) -> BraveEnrichmentResult:
        """Enrich company using full deal context for accuracy."""
        result = BraveEnrichmentResult(company_name=context.company_name)

        # Skip invalid names
        if not context.company_name or len(context.company_name) < 2:
            return result

        invalid_names = {"<unknown>", "unknown", "n/a", "no funding deal", "no specific funding deal"}
        if context.company_name.lower() in invalid_names:
            return result

        # Find website with investor context
        result.website = await self.find_website_with_context(
            context.company_name,
            investor=context.lead_investor,
            category=context.enterprise_category,
        )
        await asyncio.sleep(self.rate_limit_delay)

        # Find LinkedIn for each known founder
        if context.founders:
            for founder in context.founders:
                founder_name = founder.get("name", "")
                if not founder_name or founder_name.lower() in ("ceo", "founder", "unknown"):
                    continue

                # Skip if already has LinkedIn
                if founder.get("linkedin_url"):
                    result.founder_linkedins[founder_name] = founder["linkedin_url"]
                    if not result.ceo_linkedin:
                        result.ceo_name = founder_name
                        result.ceo_linkedin = founder["linkedin_url"]
                    continue

                linkedin = await self.find_founder_linkedin(founder_name, context.company_name)
                if linkedin:
                    result.founder_linkedins[founder_name] = linkedin
                    if not result.ceo_linkedin:
                        result.ceo_name = founder_name
                        result.ceo_linkedin = linkedin

                await asyncio.sleep(self.rate_limit_delay)

        # Fallback: if still no LinkedIn found, try generic CEO search
        if not result.ceo_linkedin:
            ceo_name, linkedin_url = await self.find_ceo_linkedin(context.company_name)
            if linkedin_url:
                result.ceo_name = ceo_name
                result.ceo_linkedin = linkedin_url

        return result

    async def enrich(self, company_name: str) -> BraveEnrichmentResult:
        """Enrich company with website and CEO LinkedIn (legacy method)."""
        result = BraveEnrichmentResult(company_name=company_name)

        if not company_name or len(company_name) < 2:
            return result

        invalid_names = {"<unknown>", "unknown", "n/a", "no funding deal", "no specific funding deal"}
        if company_name.lower() in invalid_names:
            return result

        # Find website (without context - less accurate)
        result.website = await self.find_website_with_context(company_name)
        await asyncio.sleep(self.rate_limit_delay)

        # Find CEO LinkedIn
        ceo_name, linkedin_url = await self.find_ceo_linkedin(company_name)
        result.ceo_name = ceo_name
        result.ceo_linkedin = linkedin_url

        return result


async def enrich_company(company_name: str) -> BraveEnrichmentResult:
    """Enrich a single company with website and CEO LinkedIn."""
    async with BraveEnrichmentClient() as client:
        return await client.enrich(company_name)


async def enrich_company_with_context(context: DealContext) -> BraveEnrichmentResult:
    """Enrich a company using full deal context for better accuracy."""
    async with BraveEnrichmentClient() as client:
        return await client.enrich_with_context(context)


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
                logger.error(f"Error enriching {name}: {e}")
                results[name] = BraveEnrichmentResult(company_name=name)

            await asyncio.sleep(delay_seconds)

    return results


async def persist_deal_linkedin_enrichment(
    deal_id: int,
    founders_with_linkedin: List[Dict],
) -> bool:
    """
    Persist LinkedIn enrichment results to a deal's founders_json.

    Args:
        deal_id: ID of the deal to update
        founders_with_linkedin: Updated list of founder dicts with linkedin_url fields

    Returns:
        True if update succeeded, False otherwise
    """
    import json
    from ..archivist.database import get_session
    from ..archivist.models import Deal

    try:
        async with get_session() as session:
            deal = await session.get(Deal, deal_id)
            if deal:
                deal.founders_json = json.dumps(founders_with_linkedin)
                await session.commit()
                logger.info(f"Updated LinkedIn for deal {deal_id}")
                return True
            return False
    except Exception as e:
        logger.error(f"Error persisting LinkedIn for deal {deal_id}: {e}")
        return False


async def enrich_deal_founders_linkedin(
    deal_id: int,
    company_name: str,
    founders: List[Dict],
    persist: bool = True,
) -> List[Dict]:
    """
    Enrich founders with LinkedIn URLs and optionally persist to database.

    Args:
        deal_id: ID of the deal
        company_name: Name of the company
        founders: List of founder dicts [{"name": "...", "title": "...", "linkedin_url": None}]
        persist: If True, save results to database

    Returns:
        Updated list of founder dicts with linkedin_url fields populated
    """
    if not founders:
        return []

    updated_founders = []
    enriched_count = 0

    async with BraveEnrichmentClient() as client:
        for founder in founders:
            founder_copy = dict(founder)

            # Skip if already has LinkedIn
            if founder.get("linkedin_url"):
                updated_founders.append(founder_copy)
                continue

            founder_name = founder.get("name", "")
            if not founder_name or founder_name.lower() in ("ceo", "founder", "unknown"):
                updated_founders.append(founder_copy)
                continue

            # Find LinkedIn
            linkedin_url = await client.find_founder_linkedin(founder_name, company_name)
            if linkedin_url:
                founder_copy["linkedin_url"] = linkedin_url
                enriched_count += 1
                logger.info(f"Found LinkedIn for {founder_name} at {company_name}: {linkedin_url}")

            updated_founders.append(founder_copy)
            await asyncio.sleep(client.rate_limit_delay)

    # Persist results
    if persist and enriched_count > 0:
        await persist_deal_linkedin_enrichment(deal_id, updated_founders)

    return updated_founders

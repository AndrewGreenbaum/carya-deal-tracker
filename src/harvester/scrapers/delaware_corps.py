"""
Delaware Division of Corporations - Catch New Tech Company Formations.

Monitors Delaware's Division of Corporations for new entity filings.
~67% of Fortune 500 and most VC-backed startups incorporate in Delaware.

Why Delaware?
- Business-friendly laws
- Court of Chancery (specialized business court)
- Privacy (officers/directors not public)
- Standard for VC investment

This scraper looks for:
1. New corporations with tech-sounding names
2. Entity name patterns (Labs, AI, Technologies, etc.)
3. Registered agent signals (startup-friendly agents)

Note: Delaware doesn't provide a public API, so we use:
1. Brave Search for recent Delaware incorporation news
2. Third-party aggregators that track filings
"""

import asyncio
import re
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any

from ..base_scraper import NormalizedArticle
from ...config.settings import settings


# Tech company name patterns
TECH_NAME_PATTERNS = [
    r'\b(AI|ML|Labs?|Tech|Technologies|Systems|Software|Platform|Cloud|Data|Analytics|Cyber|Security)\b',
    r'\b(Automation|Robotics|Bio|Health|Med|Fin|Dev|Ops|Infrastructure)\b',
    r'\b(Intelligence|Agent|Bot|Quantum|Neural|Deep|Machine)\b',
]

# Common startup suffixes
STARTUP_SUFFIXES = [
    "Inc", "Inc.", "LLC", "Corp", "Corporation",
    "Labs", "Technologies", "Systems", "AI",
    "HQ", "Co", "Studio", "Ventures",
]

# Registered agents popular with startups (signals VC-backed)
STARTUP_FRIENDLY_AGENTS = [
    "Corporation Service Company",
    "CSC",
    "CT Corporation",
    "Registered Agents Inc",
    "Harvard Business Services",
    "Delaware Registered Agent",
    "Incorp Services",
    "Northwest Registered Agent",
    "Rocket Lawyer",
    "LegalZoom",
    "Stripe Atlas",  # Huge signal for tech startups
    "Clerky",  # YC favorite
]

# Search queries for finding Delaware incorporations
DELAWARE_QUERIES = [
    '"Delaware corporation" "artificial intelligence" formed 2024',
    '"incorporated in Delaware" startup AI OR "machine learning"',
    '"Delaware LLC" technology startup founded',
    'site:icis.corp.delaware.gov new filing technology',
    '"Stripe Atlas" startup Delaware incorporated',
    '"Delaware C-Corp" seed funding OR "Series A"',
    '"newly incorporated" Delaware tech startup',
]


@dataclass
class DelawareEntity:
    """Delaware corporate entity filing."""
    entity_name: str
    entity_type: str  # Corporation, LLC, LP, etc.
    file_number: Optional[str]
    formation_date: Optional[date]
    registered_agent: Optional[str]
    status: str  # Active, Good Standing, etc.
    source_url: str

    # Signals
    has_tech_name: bool = False
    has_startup_agent: bool = False


class DelawareCorpsScraper:
    """
    Monitor Delaware Division of Corporations for new tech company formations.

    Since Delaware doesn't provide a public API, we use:
    1. Brave Search to find recent incorporation news
    2. Third-party aggregators
    3. News about companies incorporating
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml",
            },
            follow_redirects=True,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _has_tech_name(self, name: str) -> bool:
        """Check if entity name suggests a tech company."""
        for pattern in TECH_NAME_PATTERNS:
            if re.search(pattern, name, re.IGNORECASE):
                return True
        return False

    def _has_startup_agent(self, agent: str) -> bool:
        """Check if registered agent is startup-friendly."""
        if not agent:
            return False
        agent_lower = agent.lower()
        return any(sa.lower() in agent_lower for sa in STARTUP_FRIENDLY_AGENTS)

    async def search_recent_incorporations(
        self,
        days_back: int = 7,
        max_results: int = 50,
    ) -> List[DelawareEntity]:
        """
        Search for recent Delaware incorporations via Brave Search.
        """
        entities = []

        if not settings.brave_search_key:
            print("BRAVE_SEARCH_KEY not configured for Delaware search")
            return entities

        try:
            brave_client = httpx.AsyncClient(
                timeout=30,
                headers={
                    "X-Subscription-Token": settings.brave_search_key,
                    "Accept": "application/json",
                }
            )

            try:
                for query in DELAWARE_QUERIES[:3]:  # Limit queries
                    response = await brave_client.get(
                        "https://api.search.brave.com/res/v1/web/search",
                        params={
                            "q": query,
                            "count": 20,
                            "freshness": "pw",  # Past week
                        }
                    )

                    if response.status_code == 200:
                        data = response.json()
                        for result in data.get("web", {}).get("results", []):
                            entity = self._parse_search_result(result)
                            if entity:
                                entities.append(entity)

                    await asyncio.sleep(0.3)
            finally:
                await brave_client.aclose()

        except Exception as e:
            print(f"Delaware Brave search error: {e}")

        # Deduplicate by name
        seen_names = set()
        unique_entities = []
        for entity in entities:
            name_key = entity.entity_name.lower().strip()
            if name_key not in seen_names:
                seen_names.add(name_key)
                unique_entities.append(entity)

        return unique_entities[:max_results]

    def _parse_search_result(self, result: Dict[str, Any]) -> Optional[DelawareEntity]:
        """Parse Brave search result into DelawareEntity."""
        try:
            url = result.get("url", "")
            title = result.get("title", "")
            description = result.get("description", "")

            # Try to extract company name
            # Look for patterns like "Company Name Inc" or "Company Name, Inc."
            text = f"{title} {description}"

            # Try various patterns to extract company name
            name_patterns = [
                r'"([^"]+(?:Inc\.?|LLC|Corp\.?|Technologies|Labs|AI))"',
                r'([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*(?:\s+(?:Inc\.?|LLC|Corp\.?|Technologies|Labs|AI)))',
                r'startup\s+([A-Z][a-zA-Z0-9]+)',
                r'company\s+([A-Z][a-zA-Z0-9]+)',
            ]

            entity_name = None
            for pattern in name_patterns:
                match = re.search(pattern, text)
                if match:
                    entity_name = match.group(1).strip()
                    break

            if not entity_name:
                # Use title as fallback, clean it up
                entity_name = re.sub(r'\s*[-|]\s*.*$', '', title).strip()

            if not entity_name or len(entity_name) < 2 or len(entity_name) > 100:
                return None

            # Check for tech indicators
            has_tech = self._has_tech_name(entity_name) or self._has_tech_name(text)

            # Only return if it has tech indicators
            if not has_tech:
                return None

            # Check for startup-friendly agent mentions
            has_startup_agent = any(
                agent.lower() in text.lower()
                for agent in STARTUP_FRIENDLY_AGENTS
            )

            return DelawareEntity(
                entity_name=entity_name,
                entity_type="Corporation",  # Assume corp unless specified
                file_number=None,
                formation_date=date.today(),  # Approximate
                registered_agent=None,
                status="Active",
                source_url=url,
                has_tech_name=has_tech,
                has_startup_agent=has_startup_agent,
            )

        except Exception:
            return None

    async def search_aggregators(self) -> List[DelawareEntity]:
        """
        Search third-party aggregators for Delaware filings.

        Note: Most aggregators require paid access. This searches
        free sources like OpenCorporates.
        """
        entities = []

        try:
            # OpenCorporates search for recent Delaware tech companies
            # Note: They have rate limits on free tier
            response = await self.client.get(
                "https://api.opencorporates.com/v0.4/companies/search",
                params={
                    "q": "technology",
                    "jurisdiction_code": "us_de",
                    "order": "incorporation_date",
                    "per_page": 30,
                }
            )

            if response.status_code == 200:
                data = response.json()
                for company in data.get("results", {}).get("companies", []):
                    c = company.get("company", {})
                    name = c.get("name", "")

                    if self._has_tech_name(name):
                        inc_date = c.get("incorporation_date")
                        if inc_date:
                            try:
                                formation_date = datetime.strptime(inc_date, "%Y-%m-%d").date()
                            except ValueError:
                                formation_date = None
                        else:
                            formation_date = None

                        entities.append(DelawareEntity(
                            entity_name=name,
                            entity_type=c.get("company_type", "Corporation"),
                            file_number=c.get("company_number"),
                            formation_date=formation_date,
                            registered_agent=c.get("registered_agent_name"),
                            status=c.get("current_status", "Active"),
                            source_url=c.get("opencorporates_url", ""),
                            has_tech_name=True,
                            has_startup_agent=self._has_startup_agent(
                                c.get("registered_agent_name", "")
                            ),
                        ))

        except Exception as e:
            print(f"OpenCorporates search error: {e}")

        return entities

    def entity_to_article(self, entity: DelawareEntity) -> NormalizedArticle:
        """Convert Delaware entity to NormalizedArticle."""
        signals = []
        if entity.has_tech_name:
            signals.append("Tech company name pattern")
        if entity.has_startup_agent:
            signals.append("Startup-friendly registered agent")

        text_parts = [
            f"🏛️ DELAWARE INCORPORATION DETECTED",
            f"",
            f"Entity: {entity.entity_name}",
            f"Type: {entity.entity_type}",
            f"Status: {entity.status}",
        ]

        if entity.file_number:
            text_parts.append(f"File Number: {entity.file_number}")
        if entity.formation_date:
            text_parts.append(f"Formation Date: {entity.formation_date}")
        if entity.registered_agent:
            text_parts.append(f"Registered Agent: {entity.registered_agent}")

        text_parts.extend([
            f"",
            f"Signals: {', '.join(signals) if signals else 'None'}",
            f"",
            f"Note: ~67% of VC-backed startups incorporate in Delaware.",
            f"New tech company formations may indicate upcoming funding.",
        ])

        if entity.source_url:
            text_parts.append(f"\nSource: {entity.source_url}")

        tags = ["delaware", "incorporation", "stealth_signal"]
        if entity.has_tech_name:
            tags.append("tech_name")
        if entity.has_startup_agent:
            tags.append("startup_agent")

        return NormalizedArticle(
            url=entity.source_url or f"https://icis.corp.delaware.gov/",
            title=f"Delaware Filing: {entity.entity_name}",
            text="\n".join(text_parts),
            published_date=entity.formation_date or date.today(),
            author="Delaware Division of Corporations",
            tags=tags,
            fund_slug="",
            fetched_at=datetime.utcnow(),
        )

    def score_entity(self, entity: DelawareEntity) -> int:
        """
        Score entity for likelihood of being VC-backed startup.

        Higher score = more likely to be interesting.
        """
        score = 0

        # Tech name pattern (+3)
        if entity.has_tech_name:
            score += 3

        # Startup-friendly agent (+5 - strong signal)
        if entity.has_startup_agent:
            score += 5

        # Recent formation (+2)
        if entity.formation_date:
            days_old = (date.today() - entity.formation_date).days
            if days_old <= 30:
                score += 2
            elif days_old <= 90:
                score += 1

        # Name patterns that suggest AI/ML startup (+2)
        ai_patterns = ['ai', 'intelligence', 'neural', 'deep', 'machine', 'ml', 'llm', 'gpt']
        if any(p in entity.entity_name.lower() for p in ai_patterns):
            score += 2

        return score

    async def scrape_all(
        self,
        days_back: int = 7,
        min_score: int = 3,
    ) -> List[NormalizedArticle]:
        """
        Full scraping pipeline for Delaware incorporations.

        Args:
            days_back: How many days back to search
            min_score: Minimum score to include (filters noise)

        Returns:
            List of NormalizedArticle for potential startup incorporations
        """
        all_entities = []

        # Search via Brave
        brave_entities = await self.search_recent_incorporations(days_back=days_back)
        all_entities.extend(brave_entities)

        # Search aggregators
        agg_entities = await self.search_aggregators()
        all_entities.extend(agg_entities)

        # Deduplicate by name
        seen_names = set()
        unique_entities = []
        for entity in all_entities:
            name_key = entity.entity_name.lower().strip()
            if name_key not in seen_names:
                seen_names.add(name_key)
                unique_entities.append(entity)

        # Score and filter
        scored = [(e, self.score_entity(e)) for e in unique_entities]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Convert to articles
        articles = []
        for entity, score in scored:
            if score >= min_score:
                article = self.entity_to_article(entity)
                articles.append(article)

        return articles


# Convenience function
async def run_delaware_corps_scraper(
    days_back: int = 7,
    min_score: int = 3,
) -> List[NormalizedArticle]:
    """Run Delaware Corps scraper and return articles."""
    async with DelawareCorpsScraper() as scraper:
        return await scraper.scrape_all(days_back=days_back, min_score=min_score)

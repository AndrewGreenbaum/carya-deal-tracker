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

# Fund name variations for search queries (primary name + alternates)
# Core 18 tracked funds
FUND_SEARCH_NAMES = {
    "founders_fund": ["Founders Fund", "Peter Thiel"],
    "benchmark": ["Benchmark", "Benchmark Capital"],
    "sequoia": ["Sequoia Capital", "Sequoia"],
    "khosla": ["Khosla Ventures", "Vinod Khosla"],
    "index": ["Index Ventures"],
    "a16z": ["Andreessen Horowitz", "a16z"],
    "insight": ["Insight Partners"],
    "bessemer": ["Bessemer Venture Partners", "Bessemer", "BVP"],
    "redpoint": ["Redpoint Ventures", "Redpoint"],
    "greylock": ["Greylock Partners", "Greylock"],
    "gv": ["Google Ventures", "GV"],
    "menlo": ["Menlo Ventures"],
    "usv": ["Union Square Ventures", "USV"],
    "thrive": ["Thrive Capital"],
    "accel": ["Accel Partners", "Accel"],
    "felicis": ["Felicis Ventures", "Felicis"],
    "general_catalyst": ["General Catalyst", "GC"],
    "first_round": ["First Round Capital", "First Round"],
}

# Exclusion terms to filter noise - enhanced for generic fund names
EXCLUSION_TERMS = {
    "gv": ["-NYSE", "-stock", "-ticker", "-dividend", "-\"Visionary Holdings\""],  # Exclude GV stock ticker
    "thrive": ["-\"Thrive Global\"", "-\"Thrive IT\"", "-\"Thrive Market\"", "-wellness"],  # Wrong Thrive
    "sequoia": ["-\"Sequoia National\"", "-HongShan", "-\"Peak XV\"", "-\"Sequoia India\"", "-\"Sequoia China\""],  # Wrong Sequoia
    "benchmark": ["-benchmark.com", "-benchmarking", "-\"Benchmark International\"", "-\"Benchmark Electronics\""],  # Generic benchmark
    "accel": ["-accelerate", "-acceleration", "-accelerator", "-\"Accel Entertainment\""],  # Generic accel word
    "index": ["-\"index fund\"", "-\"stock index\"", "-\"S&P index\"", "-\"market index\""],  # Exclude index fund mentions
    "insight": ["-\"market insight\"", "-\"business insight\"", "-\"consumer insight\"", "-insights"],  # Generic insight
    "menlo": ["-\"Menlo Park\"", "-\"Menlo College\"", "-\"Menlo School\""],  # Menlo Park location
    "redpoint": ["-\"Redpoint Bio\"", "-\"Redpoint Positioning\""],  # Other Redpoint companies
    "first_round": ["-\"first round pick\"", "-\"first round draft\"", "-NFL", "-NBA"],  # Sports draft picks
}

# Partner name queries for funds with disabled website scrapers
# These funds don't have useful news pages, so we search by partner names
PARTNER_QUERIES = {
    "benchmark": [
        '"Bill Gurley" investment OR funding OR leads',
        '"Sarah Tavel" investment OR funding OR leads',
        '"Peter Fenton" investment OR funding OR leads',
    ],
    "first_round": [
        '"Josh Kopelman" investment OR funding OR leads',
        '"Phin Barnes" investment OR funding OR leads',
        '"First Round Capital" leads OR "led by First Round"',
    ],
    "khosla": [
        '"Vinod Khosla" investment OR funding OR leads',
        '"Khosla Ventures" leads OR "led by Khosla"',
    ],
    "thrive": [
        '"Josh Kushner" investment OR funding OR leads',
        '"Thrive Capital" leads OR "led by Thrive"',
    ],
    "founders_fund": [
        '"Peter Thiel" investment OR funding OR leads',
        '"Keith Rabois" investment OR funding OR leads',
        '"Founders Fund" leads OR "led by Founders Fund"',
    ],
}

# Enterprise AI keywords - optimized for high-signal results
ENTERPRISE_KEYWORDS = [
    # Infrastructure & DevTools
    "AI infrastructure",
    "LLMOps",
    "MLOps",
    "vector database",
    "AI developer tools",
    # Security
    "AI security startup",
    "cybersecurity AI",
    # Vertical SaaS
    "AI SaaS startup",
    "enterprise AI startup",
    "B2B AI startup",
    # Agentic
    "AI agents startup",
    "agentic AI",
    "AI automation startup",
    "AI copilot startup",
    # Data Intelligence
    "AI analytics startup",
    "enterprise search AI",
]

# Lead investor phrases - all variations used in funding news
LEAD_PHRASES = [
    "led by",
    "led the round",
    "led the investment",
    "leading the round",
    "lead investor",
    "spearheaded by",
]

# Participation/announcement phrases (catches non-lead mentions)
PARTICIPATION_PHRASES = [
    "invests in",
    "invested in",
    "backs",
    "backed",
    "joins",
    "participated",
    "announces investment",
    "portfolio company",
]

# Funding keywords
FUNDING_KEYWORDS = [
    "raises",
    "raised",
    "funding",
    "Series A",
    "Series B",
    "Series C",
    "seed round",
    "million",
]

# Stealth/early signal keywords
STEALTH_KEYWORDS = [
    "stealth mode",
    "emerges from stealth",
    "comes out of stealth",
    "exits stealth",
    "stealth startup",
]

# Regional funding queries for broader international coverage
REGIONAL_QUERIES = {
    "europe": '"Series A" OR "Series B" Europe startup funding led',
    "uk": '"Series A" OR "Series B" UK startup funding led',
    "germany": '"Series A" OR "Series B" Germany startup funding led',
    "france": '"Series A" OR "Series B" France startup funding led',
    "asia": '"Series A" OR "Series B" Asia startup funding led',
    "india": '"Series A" OR "Series B" India startup funding led',
    "singapore": '"Series A" OR "Series B" Singapore startup funding led',
    "latam": '"Series A" OR "Series B" Latin America startup funding led',
    "brazil": '"Series A" OR "Series B" Brazil startup funding led',
    "israel": '"Series A" OR "Series B" Israel startup funding led',
}

# Early signal queries (detect before official announcements)
EARLY_SIGNAL_QUERIES = [
    # Hiring signals
    '"stealth startup" hiring AI engineer',
    '"backed by" "stealth" hiring',
    '"well-funded" startup hiring AI',
    '"recently raised" hiring engineering',
    # Acquisition/M&A signals
    'AI startup acquisition announced',
    '"acquired by" AI startup',
    # Company updates that hint at funding
    '"announces expansion" AI startup',
    '"opens office" AI startup funded',
    '"hires" "former" Google AI startup',
    '"hires" "former" Meta AI startup',
    # Press release patterns
    '"today announced" funding AI',
    '"secures funding" AI enterprise',
]

# Enterprise AI category queries (one per category)
ENTERPRISE_CATEGORY_QUERIES = {
    "infrastructure": '"AI infrastructure" OR "LLMOps" OR "MLOps" OR "vector database"',
    "security": '"AI security" OR "cybersecurity AI" OR "AI threat detection"',
    "vertical_saas": '"AI SaaS" OR "enterprise AI" OR "B2B AI"',
    "agentic": '"AI agents" OR "agentic AI" OR "AI automation" OR "AI copilot"',
    "data_intelligence": '"AI analytics" OR "enterprise search AI" OR "knowledge management AI"',
}


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

    def _build_fund_query(self, fund_slug: str, query_type: str = "lead") -> str:
        """Build optimized search query for a specific fund.

        Args:
            fund_slug: Fund identifier
            query_type: "lead" for lead investor queries, "participation" for broader coverage
        """
        fund_names = FUND_SEARCH_NAMES.get(fund_slug, [])
        if not fund_names:
            return ""

        # Build fund name alternatives (OR)
        name_query = " OR ".join([f'"{name}"' for name in fund_names])

        # Build funding keywords (OR)
        funding_query = " OR ".join([f'"{kw}"' for kw in FUNDING_KEYWORDS[:4]])

        # Get exclusion terms for this fund
        exclusions = EXCLUSION_TERMS.get(fund_slug, [])
        exclusion_query = " ".join(exclusions) if exclusions else ""

        if query_type == "lead":
            # Lead query: fund + lead phrases + funding
            lead_query = " OR ".join([f'"{phrase}"' for phrase in LEAD_PHRASES[:3]])
            query = f'({name_query}) AND ({lead_query}) AND ({funding_query})'
        else:
            # Participation query: fund + participation phrases + funding (catches more)
            participation_query = " OR ".join([f'"{phrase}"' for phrase in PARTICIPATION_PHRASES[:4]])
            query = f'({name_query}) AND ({participation_query}) AND ({funding_query})'

        if exclusion_query:
            query = f'{query} {exclusion_query}'

        return query

    def _build_enterprise_query(self, category: str = None) -> str:
        """Build optimized Enterprise AI funding query.

        Args:
            category: Optional specific category (infrastructure, security, etc.)
        """
        if category and category in ENTERPRISE_CATEGORY_QUERIES:
            keywords = ENTERPRISE_CATEGORY_QUERIES[category]
        else:
            # Use top 6 enterprise keywords for broader coverage
            keywords = " OR ".join([f'"{kw}"' for kw in ENTERPRISE_KEYWORDS[:6]])

        lead_query = " OR ".join([f'"{phrase}"' for phrase in LEAD_PHRASES[:3]])
        funding_query = " OR ".join([f'"{kw}"' for kw in FUNDING_KEYWORDS[:4]])

        return f'({keywords}) AND ({funding_query}) AND ({lead_query})'

    def _build_stealth_query(self) -> str:
        """Build query for stealth startup funding news."""
        stealth_query = " OR ".join([f'"{kw}"' for kw in STEALTH_KEYWORDS[:3]])
        funding_query = " OR ".join([f'"{kw}"' for kw in FUNDING_KEYWORDS[:4]])
        return f'({stealth_query}) AND ({funding_query}) AND ("AI" OR "startup")'

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
        query_type: str = "lead",
    ) -> List[BraveSearchResult]:
        """
        Search for funding news about a specific tracked fund.

        Args:
            fund_slug: The fund to search for (e.g., 'a16z', 'sequoia')
            freshness: pd=past day, pw=past week, pm=past month
            query_type: "lead" or "participation"

        Returns:
            List of search results
        """
        query = self._build_fund_query(fund_slug, query_type)
        if not query:
            return []

        return await self.search_news(query, count=20, freshness=freshness)

    async def search_all_funds(
        self,
        freshness: str = "pw",
        include_participation: bool = True,
    ) -> Dict[str, List[BraveSearchResult]]:
        """
        Search for all 18 tracked funds with optional 2x coverage.

        Args:
            freshness: pd=past day, pw=past week, pm=past month
            include_participation: If True, runs both lead AND participation queries (2x)

        Returns:
            Dict mapping fund_slug to list of results
        """
        results: Dict[str, List[BraveSearchResult]] = {}

        for fund_slug in FUND_SEARCH_NAMES.keys():
            # Lead query (primary)
            lead_results = await self.search_for_fund(fund_slug, freshness, "lead")
            results[fund_slug] = lead_results
            await asyncio.sleep(0.3)

            # Participation query (2x expansion)
            if include_participation:
                participation_results = await self.search_for_fund(fund_slug, freshness, "participation")
                results[f"{fund_slug}_participation"] = participation_results
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

    async def search_stealth(
        self,
        freshness: str = "pw",
    ) -> List[BraveSearchResult]:
        """
        Search for stealth startup funding news.

        Catches early signals before official announcements.
        """
        query = self._build_stealth_query()
        return await self.search_news(query, count=30, freshness=freshness)

    async def search_regional(
        self,
        freshness: str = "pw",
    ) -> Dict[str, List[BraveSearchResult]]:
        """
        Search for regional funding news (10 regions).

        Covers Europe, UK, Germany, France, Asia, India, Singapore, LATAM, Brazil, Israel.
        """
        results: Dict[str, List[BraveSearchResult]] = {}

        for region, query in REGIONAL_QUERIES.items():
            region_results = await self.search_news(query, count=20, freshness=freshness)
            results[region] = region_results
            await asyncio.sleep(0.3)

        return results

    async def search_early_signals(
        self,
        freshness: str = "pd",  # Past day for early signals
    ) -> List[BraveSearchResult]:
        """
        Search for early signals (hiring, expansion) that hint at funding.

        Uses past day freshness for faster detection.
        """
        all_results: List[BraveSearchResult] = []
        seen_urls: set = set()

        for query in EARLY_SIGNAL_QUERIES:
            results = await self.search_news(query, count=10, freshness=freshness)
            for r in results:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)
            await asyncio.sleep(0.2)

        return all_results

    async def search_partner_names(
        self,
        freshness: str = "pw",
    ) -> Dict[str, List[BraveSearchResult]]:
        """
        Search for funding news by partner names.

        For funds without useful website scrapers (Benchmark, First Round,
        Khosla, Thrive, Founders Fund), we search by partner names to find
        deals that might not mention the fund name explicitly.
        """
        results: Dict[str, List[BraveSearchResult]] = {}

        for fund_slug, queries in PARTNER_QUERIES.items():
            fund_results: List[BraveSearchResult] = []
            seen_urls: set = set()

            for query in queries:
                search_results = await self.search_news(query, count=15, freshness=freshness)
                for r in search_results:
                    if r.url not in seen_urls:
                        seen_urls.add(r.url)
                        fund_results.append(r)
                await asyncio.sleep(0.3)

            results[fund_slug] = fund_results

        return results

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
        include_participation: bool = True,
        include_stealth: bool = True,
        include_regional: bool = False,
        include_early_signals: bool = False,
        include_partner_names: bool = True,  # NEW: Search by partner names
    ) -> List[NormalizedArticle]:
        """
        Full scraping pipeline with expanded coverage options.

        Args:
            freshness: pd=past day, pw=past week, pm=past month
            include_enterprise: Also search for general Enterprise AI deals
            include_participation: Run participation queries (2x fund coverage)
            include_stealth: Search for stealth startup news
            include_regional: Search 10 regional markets
            include_early_signals: Search for hiring/expansion signals
            include_partner_names: Search by partner names for funds w/o websites

        Returns:
            Deduplicated list of NormalizedArticle objects

        Query breakdown (when all options enabled):
        - 18 fund lead queries
        - 18 fund participation queries (if enabled)
        - 1 enterprise AI query
        - 1 stealth query
        - 10 regional queries (if enabled)
        - 12 early signal queries (if enabled)
        - 13 partner name queries (if enabled)
        = 38-73 queries depending on options
        """
        all_articles: List[NormalizedArticle] = []
        seen_urls: set = set()

        # Search for each tracked fund (lead + participation = 36 queries)
        fund_results = await self.search_all_funds(freshness, include_participation)

        for fund_slug, results in fund_results.items():
            # Strip "_participation" suffix for actual fund_slug
            actual_fund_slug = fund_slug.replace("_participation", "")

            for result in results:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)

                article = await self.to_normalized_article(result, actual_fund_slug)
                all_articles.append(article)

        # Search for broad Enterprise AI deals (1 query)
        if include_enterprise:
            enterprise_results = await self.search_enterprise_ai(freshness)

            for result in enterprise_results:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)

                article = await self.to_normalized_article(result, "")
                all_articles.append(article)

        # Search for stealth startups (1 query)
        if include_stealth:
            stealth_results = await self.search_stealth(freshness)

            for result in stealth_results:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)

                article = await self.to_normalized_article(result, "")
                article.tags.append("stealth")
                all_articles.append(article)

        # Search regional markets (10 queries)
        if include_regional:
            regional_results = await self.search_regional(freshness)

            for region, results in regional_results.items():
                for result in results:
                    if result.url in seen_urls:
                        continue
                    seen_urls.add(result.url)

                    article = await self.to_normalized_article(result, "")
                    article.tags.extend(["regional", region])
                    all_articles.append(article)

        # Search early signals (12 queries, uses past day)
        if include_early_signals:
            early_results = await self.search_early_signals("pd")

            for result in early_results:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)

                article = await self.to_normalized_article(result, "")
                article.tags.append("early_signal")
                all_articles.append(article)

        # Search by partner names for funds without useful website scrapers
        # (Benchmark, First Round, Khosla, Thrive, Founders Fund)
        if include_partner_names:
            partner_results = await self.search_partner_names(freshness)

            for fund_slug, results in partner_results.items():
                for result in results:
                    if result.url in seen_urls:
                        continue
                    seen_urls.add(result.url)

                    article = await self.to_normalized_article(result, fund_slug)
                    article.tags.append("partner_search")
                    all_articles.append(article)

        return all_articles


# Convenience function
async def run_brave_search_scraper(freshness: str = "pw") -> List[NormalizedArticle]:
    """Run Brave Search scraper and return normalized articles."""
    async with BraveSearchScraper() as scraper:
        return await scraper.scrape_all(freshness=freshness)

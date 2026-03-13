"""
Brave Search API Scraper - Broad News Coverage.

Uses Brave Search API to find funding news articles with
"led by" language for tracked VC funds.

OPTIMIZED:
- Uses shared BraveClient with connection pooling
- Parallel query execution (3x concurrency)
- TTL caching for partner queries (1 hour)
- Reduced scrape time: 42s → ~15s
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import List, Optional, Dict, Any, Tuple

from ..base_scraper import NormalizedArticle
from ...common.brave_client import get_brave_client, get_query_cache
from ...config.settings import settings

logger = logging.getLogger(__name__)

# Memory safety limit (Brave API max is 100 per request)
MAX_RESULTS_PER_QUERY = 100

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
# NOTE: Be careful not to exclude common startup terms (e.g., don't exclude "accelerate" for Accel)
EXCLUSION_TERMS = {
    "gv": ["-NYSE", "-stock", "-ticker", "-dividend", "-\"Visionary Holdings\""],
    # FIXED: Removed "-wellness" - was too broad, excluded any article mentioning wellness sector
    "thrive": ["-\"Thrive Global\"", "-\"Thrive IT\"", "-\"Thrive Market\"", "-\"Thrive Wellness\""],
    "sequoia": ["-\"Sequoia National\"", "-HongShan", "-\"Peak XV\"", "-\"Sequoia India\"", "-\"Sequoia China\""],
    "benchmark": ["-benchmark.com", "-\"Benchmark International\"", "-\"Benchmark Electronics\""],  # Removed -benchmarking (too broad)
    "accel": ["-\"Accel Entertainment\""],  # Removed -accelerate/-acceleration/-accelerator (common startup terms)
    "index": ["-\"index fund\"", "-\"stock index\"", "-\"S&P index\"", "-\"market index\""],
    "insight": ["-\"market insight\"", "-\"business insight\"", "-\"consumer insight\""],  # Removed -insights (too broad)
    "menlo": ["-\"Menlo Park\"", "-\"Menlo College\"", "-\"Menlo School\""],
    "redpoint": ["-\"Redpoint Bio\"", "-\"Redpoint Positioning\""],
    "first_round": ["-\"first round pick\"", "-\"first round draft\"", "-NFL", "-NBA"],
}

# Partner name queries for funds that need external source coverage
# Only include funds with DISABLED scrapers or weak website coverage
# Funds with working scrapers (a16z, sequoia, etc.) are covered by fund-level queries
#
# STRATEGY: Cast a WIDE net - the LLM will determine lead status from full article
# Don't require "led by" in the query - many headlines say "backs", "invests", etc.
# but the article body reveals the fund actually led the round.
#
# SYNTAX FIX: Use AND grouping - "Name" AND (term1 OR term2)
# NOT: "Name" term1 OR term2 (parses as ("Name" term1) OR term2)
PARTNER_QUERIES = {
    # DISABLED SCRAPER - needs full partner coverage
    # Benchmark partners: https://www.benchmark.com/people
    "benchmark": [
        # Fund-level queries (BROAD - catch any funding mention)
        '"Benchmark Capital" AND (funding OR raises OR raised OR investment OR invests)',
        '"Benchmark" AND "Series A" AND (startup OR funding OR million)',
        '"Benchmark" AND "Series B" AND (startup OR funding OR million)',
        '"Benchmark" AND "seed" AND (startup OR funding OR million)',
        '"Benchmark" AND (backs OR backed OR invests) AND startup',
        # Partner queries (any investment mention)
        '"Bill Gurley" AND (startup OR investment OR funding OR venture)',
        '"Peter Fenton" AND (startup OR investment OR funding OR venture)',
        '"Sarah Tavel" AND (startup OR investment OR funding OR venture)',
        '"Eric Vishria" AND (startup OR investment OR funding OR venture)',
        '"Chetan Puttagunta" AND (startup OR investment OR funding OR venture)',
        '"Miles Grimshaw" AND (startup OR investment OR funding OR venture)',
    ],
    # DISABLED SCRAPER - needs full partner coverage
    # First Round partners: https://firstround.com/team
    "first_round": [
        # Fund-level queries (BROAD - catch any funding mention)
        '"First Round Capital" AND (funding OR raises OR raised OR investment)',
        '"First Round Capital" AND "Series A" AND (startup OR million)',
        '"First Round" AND "seed" AND (startup OR funding OR raises)',
        '"First Round" AND (backs OR backed OR invests) AND startup',
        # Partner queries (any investment mention)
        '"Josh Kopelman" AND (startup OR investment OR funding OR venture)',
        '"Todd Jackson" AND (startup OR investment OR funding OR venture) AND "First Round"',
        '"Phin Barnes" AND (startup OR investment OR funding OR venture)',
        '"Bill Trenchard" AND (startup OR investment OR funding OR venture)',
        '"Rob Hayes" AND (startup OR investment OR funding OR "First Round")',
        '"Howard Morgan" AND (startup OR investment OR funding OR venture)',
    ],
    # DISABLED SCRAPER - needs full partner coverage
    # Khosla partners: https://www.khoslaventures.com/team
    "khosla": [
        # Fund-level queries (BROAD - catch any funding mention)
        '"Khosla Ventures" AND (funding OR raises OR raised OR investment)',
        '"Khosla Ventures" AND "Series A" AND (startup OR million)',
        '"Khosla Ventures" AND "Series B" AND (startup OR million)',
        '"Khosla" AND (backs OR backed OR invests) AND startup',
        # Partner queries (any investment mention)
        '"Vinod Khosla" AND (startup OR investment OR funding OR venture)',
        '"Samir Kaul" AND (startup OR investment OR funding OR "Khosla")',
        '"Sven Strohband" AND (startup OR investment OR funding OR "Khosla")',
    ],
    # DISABLED SCRAPER - needs full partner coverage
    # Thrive partners: https://www.thrivecap.com (limited public info)
    "thrive": [
        # Fund-level queries (BROAD - catch any funding mention)
        '"Thrive Capital" AND (funding OR raises OR raised OR investment)',
        '"Thrive Capital" AND "Series A" AND (startup OR million)',
        '"Thrive Capital" AND "Series B" AND (startup OR million)',
        '"Thrive Capital" AND "seed" AND (startup OR million)',
        '"Thrive Capital" AND (backs OR backed OR invests)',
        # Partner queries (any investment mention)
        '"Josh Kushner" AND (startup OR investment OR funding OR venture)',
        '"Kareem Zaki" AND (startup OR investment OR funding OR "Thrive")',
        '"Josh Miller" AND "Thrive Capital" AND (investment OR funding)',
    ],
    # WEAK SCRAPER - needs partner backup
    # Founders Fund partners: https://foundersfund.com/team
    "founders_fund": [
        # Fund-level queries (BROAD - catch any funding mention)
        '"Founders Fund" AND (funding OR raises OR raised OR investment)',
        '"Founders Fund" AND "Series A" AND (startup OR million)',
        '"Founders Fund" AND "Series B" AND (startup OR million)',
        '"Founders Fund" AND (backs OR backed OR invests)',
        # Partner queries (any investment mention)
        '"Keith Rabois" AND (startup OR investment OR funding OR venture)',
        '"Brian Singerman" AND (startup OR investment OR funding OR "Founders Fund")',
        '"Delian Asparouhov" AND (startup OR investment OR funding OR venture)',
        '"Trae Stephens" AND (startup OR investment OR funding OR venture)',
    ],
}

# Enterprise AI keywords
ENTERPRISE_KEYWORDS = [
    "AI infrastructure", "LLMOps", "MLOps", "vector database", "AI developer tools",
    "AI security startup", "cybersecurity AI",
    "AI SaaS startup", "enterprise AI startup", "B2B AI startup",
    "AI agents startup", "agentic AI", "AI automation startup", "AI copilot startup",
    "AI analytics startup", "enterprise search AI",
]

# Lead investor phrases - include past AND present tense variations
# FIXED: Added single-word "leads" and "led" for headlines like "Thrive Capital leads $34M"
LEAD_PHRASES = [
    "led by", "led the round", "led the investment",
    "leading the round", "leads the round", "leads the investment",
    "lead investor", "spearheaded by", "co-led by",
    "leads",  # Single word for headlines
    "led",    # Single word for headlines
]

# Participation phrases
PARTICIPATION_PHRASES = [
    "invests in", "invested in", "backs", "backed",
    "joins", "participated", "announces investment", "portfolio company",
]

# Funding keywords
FUNDING_KEYWORDS = [
    "raises", "raised", "funding", "Series A",
    "Series B", "Series C", "seed round", "million",
]

# Stealth keywords
STEALTH_KEYWORDS = [
    "stealth mode", "emerges from stealth", "comes out of stealth",
    "exits stealth", "stealth startup",
]

# Regional queries
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

# Early signal queries
EARLY_SIGNAL_QUERIES = [
    '"stealth startup" hiring AI engineer',
    '"backed by" "stealth" hiring',
    '"well-funded" startup hiring AI',
    '"recently raised" hiring engineering',
    'AI startup acquisition announced',
    '"acquired by" AI startup',
    '"announces expansion" AI startup',
    '"opens office" AI startup funded',
    '"hires" "former" Google AI startup',
    '"hires" "former" Meta AI startup',
    '"today announced" funding AI',
    '"secures funding" AI enterprise',
]

# Enterprise category queries
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

    OPTIMIZED:
    - Uses shared BraveClient (no duplicate HTTP client)
    - Parallel query execution with semaphore
    - TTL caching for partner queries
    """

    def __init__(self):
        self.rate_limit_delay = settings.brave_search_rate_limit_delay
        # Validate API key at init time for early failure
        if not settings.brave_search_key:
            logger.warning("BRAVE_SEARCH_KEY not configured - BraveSearchScraper will return empty results")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass  # Client is shared, don't close it

    def _build_fund_query(self, fund_slug: str, query_type: str = "lead") -> str:
        """Build optimized search query for a specific fund."""
        fund_names = FUND_SEARCH_NAMES.get(fund_slug, [])
        if not fund_names:
            return ""

        name_query = " OR ".join([f'"{name}"' for name in fund_names])
        # FIXED: Use first 6 keywords (was 4) - now includes "Series B" and "Series C"
        funding_query = " OR ".join([f'"{kw}"' for kw in FUNDING_KEYWORDS[:6]])
        exclusions = EXCLUSION_TERMS.get(fund_slug, [])
        exclusion_query = " ".join(exclusions) if exclusions else ""

        if query_type == "lead":
            # FIXED: Use all lead phrases, not just first 6 (was missing "leads", "led" single words)
            lead_query = " OR ".join([f'"{phrase}"' for phrase in LEAD_PHRASES])
            query = f'({name_query}) AND ({lead_query}) AND ({funding_query})'
        else:
            participation_query = " OR ".join([f'"{phrase}"' for phrase in PARTICIPATION_PHRASES[:4]])
            query = f'({name_query}) AND ({participation_query}) AND ({funding_query})'

        if exclusion_query:
            query = f'{query} {exclusion_query}'

        return query

    def _build_enterprise_query(self, category: str = None) -> str:
        """Build Enterprise AI funding query."""
        if category and category in ENTERPRISE_CATEGORY_QUERIES:
            keywords = ENTERPRISE_CATEGORY_QUERIES[category]
        else:
            keywords = " OR ".join([f'"{kw}"' for kw in ENTERPRISE_KEYWORDS[:6]])

        # Use all lead phrases for broader coverage
        lead_query = " OR ".join([f'"{phrase}"' for phrase in LEAD_PHRASES])
        funding_query = " OR ".join([f'"{kw}"' for kw in FUNDING_KEYWORDS[:6]])
        return f'({keywords}) AND ({funding_query}) AND ({lead_query})'

    def _build_stealth_query(self) -> str:
        """Build query for stealth startup funding news."""
        stealth_query = " OR ".join([f'"{kw}"' for kw in STEALTH_KEYWORDS[:3]])
        funding_query = " OR ".join([f'"{kw}"' for kw in FUNDING_KEYWORDS[:4]])
        return f'({stealth_query}) AND ({funding_query}) AND ("AI" OR "startup")'

    def _parse_news_results(self, data: Optional[Dict[str, Any]]) -> List[BraveSearchResult]:
        """Parse Brave news API response with validation."""
        if data is None:
            return []

        results = []

        if "error" in data:
            error_msg = data.get("error", {})
            if isinstance(error_msg, dict):
                logger.error(f"Brave API error: {error_msg.get('message', 'Unknown error')}")
            else:
                logger.error(f"Brave API error: {error_msg}")
            return []

        if "results" not in data:
            if "web" in data and "results" in data.get("web", {}):
                data["results"] = data["web"]["results"]
            else:
                return []

        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            return []

        for item in raw_results:
            # Memory safety check
            if len(results) >= MAX_RESULTS_PER_QUERY:
                logger.warning(f"Hit max results limit ({MAX_RESULTS_PER_QUERY})")
                break

            try:
                url = item.get("url", "")
                if not url:
                    continue

                pub_date = None
                age = item.get("age", "")
                if age:
                    pub_date = self._parse_relative_date(age)

                meta_url = item.get("meta_url")
                source = meta_url.get("hostname", "") if isinstance(meta_url, dict) else ""

                results.append(BraveSearchResult(
                    title=item.get("title", ""),
                    url=url,
                    description=item.get("description", ""),
                    published_date=pub_date,
                    source=source,
                    extra_snippets=item.get("extra_snippets", []),
                ))
            except (TypeError, KeyError, AttributeError) as e:
                logger.warning(f"Error parsing Brave result: {e}")
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

    async def search_news(
        self,
        query: str,
        count: int = 20,
        freshness: str = "pw",
        use_cache: bool = False,
    ) -> List[BraveSearchResult]:
        """Execute news search using shared client."""
        client = get_brave_client()
        data = await client.search_news(query, count, freshness, use_cache)
        return self._parse_news_results(data)

    async def search_all_funds_parallel(
        self,
        freshness: str = "pw",
        include_participation: bool = True,
    ) -> Dict[str, List[BraveSearchResult]]:
        """
        Search all 18 tracked funds with PARALLEL execution.

        OPTIMIZED: Uses batch queries with 3x concurrency.
        Before: 36 queries × 0.5s = 18s
        After: 36 queries / 3 × 0.3s ≈ 4s
        """
        # Build all queries upfront
        queries: List[Tuple[str, str, int, str, bool]] = []
        query_to_fund: Dict[str, Tuple[str, str]] = {}  # query -> (fund_slug, type)

        for fund_slug in FUND_SEARCH_NAMES.keys():
            # Lead query - API cost is per-query not per-result, so maximize coverage
            lead_query = self._build_fund_query(fund_slug, "lead")
            if lead_query:
                queries.append((lead_query, "news", 25, freshness, True))  # use_cache=True
                query_to_fund[lead_query] = (fund_slug, "lead")

            # Participation query
            if include_participation:
                part_query = self._build_fund_query(fund_slug, "participation")
                if part_query:
                    queries.append((part_query, "news", 25, freshness, True))  # use_cache=True
                    query_to_fund[part_query] = (fund_slug, "participation")

        # Execute in parallel
        client = get_brave_client()
        batch_results = await client.search_batch(queries, max_concurrent=3, delay_between=self.rate_limit_delay)

        # Process results
        results: Dict[str, List[BraveSearchResult]] = {}
        for query, data in batch_results.items():
            if query in query_to_fund:
                fund_slug, query_type = query_to_fund[query]
                key = f"{fund_slug}_participation" if query_type == "participation" else fund_slug
                results[key] = self._parse_news_results(data)

        return results

    async def search_partner_names_parallel(
        self,
        freshness: str = "pw",
    ) -> Dict[str, List[BraveSearchResult]]:
        """
        Search by partner names with PARALLEL execution and CACHING.

        OPTIMIZED:
        - Uses batch queries with 3x concurrency
        - Caches results for 1 hour (partner searches are stable)
        Before: 47 queries × 0.5s = 23.5s
        After: 47 queries / 3 × 0.3s ≈ 5s (+ cache hits = 0s)
        """
        # Build all queries upfront
        queries: List[Tuple[str, str, int, str, bool]] = []
        query_to_fund: Dict[str, str] = {}  # query -> fund_slug

        for fund_slug, partner_queries in PARTNER_QUERIES.items():
            for query in partner_queries:
                # API cost is per-query not per-result, so maximize coverage
                queries.append((query, "news", 25, freshness, True))  # use_cache=True
                query_to_fund[query] = fund_slug

        # Execute in parallel with caching
        client = get_brave_client()
        batch_results = await client.search_batch(queries, max_concurrent=3, delay_between=self.rate_limit_delay)

        # Process results, deduplicating by URL per fund
        results: Dict[str, List[BraveSearchResult]] = {}
        fund_seen_urls: Dict[str, set] = {}

        for query, data in batch_results.items():
            if query in query_to_fund:
                fund_slug = query_to_fund[query]
                if fund_slug not in results:
                    results[fund_slug] = []
                    fund_seen_urls[fund_slug] = set()

                for result in self._parse_news_results(data):
                    if result.url not in fund_seen_urls[fund_slug]:
                        fund_seen_urls[fund_slug].add(result.url)
                        results[fund_slug].append(result)

        return results

    async def search_regional_parallel(
        self,
        freshness: str = "pw",
    ) -> Dict[str, List[BraveSearchResult]]:
        """Search regional markets with PARALLEL execution."""
        queries: List[Tuple[str, str, int, str, bool]] = []
        query_to_region: Dict[str, str] = {}

        for region, query in REGIONAL_QUERIES.items():
            queries.append((query, "news", 20, freshness, False))
            query_to_region[query] = region

        client = get_brave_client()
        batch_results = await client.search_batch(queries, max_concurrent=3, delay_between=self.rate_limit_delay)

        results: Dict[str, List[BraveSearchResult]] = {}
        for query, data in batch_results.items():
            if query in query_to_region:
                region = query_to_region[query]
                results[region] = self._parse_news_results(data)

        return results

    async def search_early_signals_parallel(
        self,
        freshness: str = "pd",
    ) -> List[BraveSearchResult]:
        """Search early signals with PARALLEL execution."""
        queries: List[Tuple[str, str, int, str, bool]] = [
            (query, "news", 15, freshness, True)  # use_cache=True
            for query in EARLY_SIGNAL_QUERIES
        ]

        client = get_brave_client()
        batch_results = await client.search_batch(queries, max_concurrent=3, delay_between=self.rate_limit_delay)

        all_results: List[BraveSearchResult] = []
        seen_urls: set = set()

        for data in batch_results.values():
            for result in self._parse_news_results(data):
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    all_results.append(result)

        return all_results

    async def search_enterprise_ai(
        self,
        freshness: str = "pw",
    ) -> List[BraveSearchResult]:
        """Search for Enterprise AI funding news."""
        query = self._build_enterprise_query()
        # Reduced from 50 to 30 for token savings
        return await self.search_news(query, count=30, freshness=freshness)

    async def search_stealth(
        self,
        freshness: str = "pw",
    ) -> List[BraveSearchResult]:
        """Search for stealth startup funding news."""
        query = self._build_stealth_query()
        # Reduced from 30 to 20 for token savings
        return await self.search_news(query, count=20, freshness=freshness)

    async def to_normalized_article(
        self,
        result: BraveSearchResult,
        fund_slug: str = "",
    ) -> NormalizedArticle:
        """Convert search result to NormalizedArticle."""
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
            fetched_at=datetime.now(timezone.utc),
        )

    async def scrape_all(
        self,
        freshness: str = "pw",
        include_enterprise: bool = True,
        include_participation: bool = True,
        include_stealth: bool = True,
        include_regional: bool = False,
        include_early_signals: bool = False,
        include_partner_names: bool = True,
    ) -> List[NormalizedArticle]:
        """
        Full scraping pipeline with PARALLEL execution.

        OPTIMIZED:
        - Parallel fund queries: 18s → 4s
        - Parallel partner queries: 23.5s → 5s (+ caching)
        - Parallel regional queries: 5s → 1s
        - Total: ~42s → ~15s
        """
        all_articles: List[NormalizedArticle] = []
        seen_urls: set = set()

        # Parallel fund search (lead + participation)
        fund_results = await self.search_all_funds_parallel(freshness, include_participation)

        for fund_slug, results in fund_results.items():
            actual_fund_slug = fund_slug.replace("_participation", "")
            for result in results:
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    article = await self.to_normalized_article(result, actual_fund_slug)
                    all_articles.append(article)

        # Enterprise AI (single query)
        if include_enterprise:
            for result in await self.search_enterprise_ai(freshness):
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    article = await self.to_normalized_article(result, "")
                    all_articles.append(article)

        # Stealth startups (single query)
        if include_stealth:
            for result in await self.search_stealth(freshness):
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    article = await self.to_normalized_article(result, "")
                    article.tags.append("stealth")
                    all_articles.append(article)

        # Parallel regional search
        if include_regional:
            regional_results = await self.search_regional_parallel(freshness)
            for region, results in regional_results.items():
                for result in results:
                    if result.url not in seen_urls:
                        seen_urls.add(result.url)
                        article = await self.to_normalized_article(result, "")
                        article.tags.extend(["regional", region])
                        all_articles.append(article)

        # Parallel early signals search
        if include_early_signals:
            for result in await self.search_early_signals_parallel("pd"):
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    article = await self.to_normalized_article(result, "")
                    article.tags.append("early_signal")
                    all_articles.append(article)

        # Parallel partner names search with caching
        if include_partner_names:
            partner_results = await self.search_partner_names_parallel(freshness)
            for fund_slug, results in partner_results.items():
                for result in results:
                    if result.url not in seen_urls:
                        seen_urls.add(result.url)
                        article = await self.to_normalized_article(result, fund_slug)
                        article.tags.append("partner_search")
                        all_articles.append(article)

        # Log cache stats
        cache = get_query_cache()
        logger.info(f"Query cache size: {cache.size()} entries")

        return all_articles


# Convenience function
async def run_brave_search_scraper(freshness: str = "pw") -> List[NormalizedArticle]:
    """Run Brave Search scraper and return normalized articles."""
    async with BraveSearchScraper() as scraper:
        return await scraper.scrape_all(freshness=freshness)

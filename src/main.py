"""
The Bud Tracker - Main Application Entry Point

An autonomous intelligence system to monitor lead investor signals
from elite VC firms.

OPTIMIZED:
- Response caching for static endpoints
- GZip compression for large responses
- Pagination support for deals
- Batch extraction endpoint
- Improved error handling (no sample data fallback)
"""

import asyncio
import json
from contextlib import asynccontextmanager
from functools import lru_cache
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import time

from fastapi import FastAPI, HTTPException, Depends, Security, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, nullslast
from sqlalchemy.orm import selectinload

from .config import settings, FUND_REGISTRY, get_all_funds
from .analyst import extract_deal, DealExtraction, LeadStatus
from .analyst.extractor import extract_deal_batch
from .archivist import (
    get_db,
    get_session,
    seed_funds,
    get_deals,
    Deal,
    DealInvestor,
    PortfolioCompany,
)
from .archivist.models import Article
from .archivist.models import StealthDetection
from .harvester import (
    scrape_fund,
    scrape_all_funds,
    get_implemented_scrapers,
    get_unimplemented_scrapers,
)
from .scheduler import setup_scheduler, shutdown_scheduler
from .scheduler import jobs as scheduler_module
import re
from urllib.parse import quote


# ----- URL Generation Helpers -----

def generate_company_website(company_name: str) -> str:
    """Generate likely company website URL from company name."""
    if not company_name or company_name.lower() in ["unknown", "error"]:
        return None
    # Clean company name: lowercase, remove special chars, spaces to nothing
    clean_name = re.sub(r'[^a-zA-Z0-9]', '', company_name.lower())
    # For AI companies, prefer .ai domain
    return f"https://{clean_name}.ai"


def generate_company_linkedin(company_name: str) -> str:
    """Generate company LinkedIn search URL from company name."""
    if not company_name or company_name.lower() in ["unknown", "error"]:
        return None
    # Use LinkedIn company search instead of direct page (more reliable)
    encoded = quote(company_name)
    return f"https://www.linkedin.com/search/results/companies/?keywords={encoded}"


def generate_founder_linkedin_search(founder_name: str, company_name: str) -> str:
    """Generate LinkedIn search URL for founder at company."""
    if not founder_name:
        return None
    search_terms = founder_name
    if company_name and company_name.lower() not in ["unknown", "error"]:
        search_terms = f"{founder_name} {company_name}"
    encoded = quote(search_terms)
    return f"https://www.linkedin.com/search/results/people/?keywords={encoded}"


# ----- Caching Infrastructure -----

class SimpleCache:
    """Simple in-memory cache with TTL support."""

    def __init__(self):
        self._cache: Dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            value, expires_at = self._cache[key]
            if time.time() < expires_at:
                return value
            else:
                del self._cache[key]
        return None

    def set(self, key: str, value: Any, ttl_seconds: int = 3600):
        expires_at = time.time() + ttl_seconds
        self._cache[key] = (value, expires_at)

    def invalidate(self, key: str):
        self._cache.pop(key, None)

    def clear(self):
        self._cache.clear()


# Global cache instance
cache = SimpleCache()


# ----- API Key Security -----

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)):
    """Verify API key for protected endpoints."""
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")
    # In production, validate against stored keys
    return api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    print("Starting The Bud Tracker...")
    print(f"Tracking {len(FUND_REGISTRY)} VC funds")

    # Seed funds in database
    try:
        async with get_session() as session:
            await seed_funds(session)
            print("Database seeded with tracked funds")
    except Exception as e:
        print(f"Warning: Could not seed database: {e}")

    # Start scheduler for automated hourly scraping
    try:
        setup_scheduler()
        print("Scheduler started - hourly scraping enabled")
    except Exception as e:
        print(f"Warning: Could not start scheduler: {e}")

    yield

    # Shutdown scheduler
    shutdown_scheduler()
    print("Shutting down...")


app = FastAPI(
    title="The Bud Tracker",
    description="Monitor lead investor signals from elite VC firms",
    version="0.2.0",  # Version bump for optimizations
    lifespan=lifespan
)

# GZip compression for responses > 500 bytes
app.add_middleware(GZipMiddleware, minimum_size=500)

# CORS middleware for frontend
# Build allowed origins dynamically (includes production frontend if configured)
_allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
]
if settings.frontend_url:
    _allowed_origins.append(settings.frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- Response Models -----

class FundResponse(BaseModel):
    slug: str
    name: str
    ingestion_url: str
    scraper_type: str


class ExtractionRequest(BaseModel):
    text: str
    source_url: Optional[str] = None
    fund_slug: Optional[str] = None


class BatchExtractionRequest(BaseModel):
    """Request for batch extraction of multiple articles."""
    articles: List[ExtractionRequest] = Field(..., min_length=1, max_length=10)


class ExtractionResponse(BaseModel):
    startup_name: str
    round_label: str
    amount: Optional[str]
    tracked_fund_is_lead: bool
    tracked_fund_name: Optional[str]
    tracked_fund_role: Optional[str]
    confidence_score: float
    reasoning_summary: str


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    tracked_funds: int
    implemented_scrapers: int


class FounderResponse(BaseModel):
    """Founder information for API response."""
    name: str
    title: Optional[str] = None
    linkedin_url: Optional[str] = None


class DealResponse(BaseModel):
    id: str
    startup_name: str
    investor_roles: List[str]
    investment_stage: str
    amount_invested: str
    date: str
    next_steps: Optional[str] = None
    # Enterprise AI fields
    enterprise_category: Optional[str] = None
    is_enterprise_ai: bool = False
    lead_investor: Optional[str] = None
    lead_partner: Optional[str] = None
    verification_snippet: Optional[str] = None
    # Company links
    company_website: Optional[str] = None
    company_linkedin: Optional[str] = None
    # Source tracking
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    # Founders
    founders: Optional[List[FounderResponse]] = None


class PaginatedDealsResponse(BaseModel):
    """Paginated response for deals."""
    deals: List[DealResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


# ----- Endpoints -----

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint (cached for 5 minutes)."""
    cached = cache.get("health")
    if cached:
        return cached

    response = HealthResponse(
        status="healthy",
        timestamp=datetime.utcnow(),
        tracked_funds=len(FUND_REGISTRY),
        implemented_scrapers=len(get_implemented_scrapers())
    )

    cache.set("health", response, ttl_seconds=300)
    return response


@app.get("/funds", response_model=List[FundResponse])
async def list_funds():
    """
    List all tracked VC funds (cached for 1 hour).

    This data is static, so caching is safe.
    """
    cached = cache.get("funds")
    if cached:
        return cached

    funds = [
        FundResponse(
            slug=f.slug,
            name=f.name,
            ingestion_url=f.ingestion_url,
            scraper_type=f.scraper_type.value
        )
        for f in get_all_funds()
    ]

    cache.set("funds", funds, ttl_seconds=3600)
    return funds


@app.get("/funds/{slug}", response_model=FundResponse)
async def get_fund(slug: str):
    """Get details for a specific fund."""
    if slug not in FUND_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Fund '{slug}' not found")

    f = FUND_REGISTRY[slug]
    return FundResponse(
        slug=f.slug,
        name=f.name,
        ingestion_url=f.ingestion_url,
        scraper_type=f.scraper_type.value
    )


@app.get("/deals", response_model=PaginatedDealsResponse)
async def list_deals(
    fund_slug: Optional[str] = Query(None, description="Filter by fund slug"),
    stage: Optional[str] = Query(None, description="Filter by investment stage"),
    is_lead: Optional[bool] = Query(None, description="Filter by lead status (True = tracked fund led)"),
    is_enterprise_ai: Optional[bool] = Query(None, description="Filter for Enterprise AI only (True = B2B AI)"),
    enterprise_category: Optional[str] = Query(None, description="Filter by category (infrastructure, security, vertical_saas, agentic, data_intelligence)"),
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """
    List tracked deals with pagination.

    ENTERPRISE AI FILTER:
    - is_enterprise_ai=true: Only B2B Enterprise AI deals
    - enterprise_category: Filter by specific category

    LEAD FILTER:
    - is_lead=true: Only deals where tracked fund LED the round
    """
    try:
        async with get_session() as session:
            # Build base query
            base_stmt = select(Deal)

            # Apply filters
            if stage:
                base_stmt = base_stmt.where(Deal.round_type == stage)
            if is_lead is not None:
                base_stmt = base_stmt.where(Deal.is_lead_confirmed == is_lead)
            if is_enterprise_ai is not None:
                base_stmt = base_stmt.where(Deal.is_enterprise_ai == is_enterprise_ai)
            if enterprise_category:
                base_stmt = base_stmt.where(Deal.enterprise_category == enterprise_category)

            # Get total count for pagination
            count_stmt = select(func.count()).select_from(base_stmt.subquery())
            total_result = await session.execute(count_stmt)
            total = total_result.scalar() or 0

            # Query deals with eager loading
            stmt = (
                base_stmt
                .options(
                    selectinload(Deal.company),
                    selectinload(Deal.investors),
                    selectinload(Deal.articles),
                )
                .order_by(nullslast(Deal.announced_date.desc()), Deal.created_at.desc())
                .offset(offset)
                .limit(limit)
            )

            result = await session.execute(stmt)
            deals = result.scalars().all()

            # Build responses
            responses = []
            for deal in deals:
                # Build investor roles list and find lead investor
                investor_roles = []
                lead_investor_name = None
                for inv in deal.investors:
                    if inv.is_lead:
                        investor_roles.append("lead")
                        if inv.is_tracked_fund:
                            lead_investor_name = inv.investor_name
                    elif inv.is_tracked_fund:
                        investor_roles.append("non_lead")
                    else:
                        investor_roles.append("non_lead")

                # Handle stealth (no investors tracked)
                if not investor_roles:
                    investor_roles = ["stealth"]

                # Get company name for URL generation
                company_name = deal.company.name if deal.company else "Unknown"

                # Parse founders from JSON with LinkedIn fallback
                founders = None
                if deal.founders_json:
                    try:
                        founders_data = json.loads(deal.founders_json)
                        founders = []
                        for f in founders_data:
                            founder_name = f.get("name", "Unknown")
                            # Use stored LinkedIn URL or generate search URL
                            linkedin_url = f.get("linkedin_url")
                            if not linkedin_url and founder_name != "Unknown":
                                linkedin_url = generate_founder_linkedin_search(founder_name, company_name)
                            founders.append(FounderResponse(
                                name=founder_name,
                                title=f.get("title"),
                                linkedin_url=linkedin_url,
                            ))
                    except json.JSONDecodeError:
                        founders = None

                # Get source info from articles
                source_url = None
                source_name = None
                if deal.articles:
                    first_article = deal.articles[0]
                    source_url = first_article.url
                    source_name = first_article.source_fund_slug or first_article.title

                # Company links with fallback URL generation
                db_website = deal.company.website if deal.company else None
                db_linkedin = deal.company.linkedin_url if deal.company else None

                company_website = db_website or generate_company_website(company_name)
                company_linkedin = db_linkedin or generate_company_linkedin(company_name)

                responses.append(DealResponse(
                    id=str(deal.id),
                    startup_name=company_name,
                    investor_roles=investor_roles,
                    investment_stage=deal.round_type,
                    amount_invested=deal.amount or "Undisclosed",
                    date=deal.announced_date.isoformat() if deal.announced_date else "",
                    next_steps=None,
                    # Enterprise AI fields
                    enterprise_category=deal.enterprise_category,
                    is_enterprise_ai=deal.is_enterprise_ai,
                    lead_investor=lead_investor_name,
                    lead_partner=deal.lead_partner_name,
                    verification_snippet=deal.verification_snippet,
                    # Company links (with fallback generation)
                    company_website=company_website,
                    company_linkedin=company_linkedin,
                    # Source tracking
                    source_url=source_url,
                    source_name=source_name,
                    # Founders (with LinkedIn search URLs)
                    founders=founders,
                ))

            return PaginatedDealsResponse(
                deals=responses,
                total=total,
                limit=limit,
                offset=offset,
                has_more=(offset + len(responses)) < total
            )

    except Exception as e:
        # Log the error but return empty results instead of sample data
        print(f"Database query failed: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: {str(e)}"
        )


@app.get("/deals/export")
async def export_deals_csv(
    fund_slug: Optional[str] = Query(None, description="Filter by fund slug"),
    stage: Optional[str] = Query(None, description="Filter by investment stage"),
    is_lead: Optional[bool] = Query(None, description="Filter by lead status"),
    is_enterprise_ai: Optional[bool] = Query(None, description="Filter for Enterprise AI only"),
    enterprise_category: Optional[str] = Query(None, description="Filter by category"),
):
    """
    Export deals as CSV file.

    Applies same filters as /deals endpoint.
    Returns a downloadable CSV file.
    """
    import csv
    import io
    from fastapi.responses import StreamingResponse

    try:
        async with get_session() as session:
            # Build query with filters
            stmt = (
                select(Deal)
                .options(
                    selectinload(Deal.company),
                    selectinload(Deal.investors),
                )
            )

            if stage:
                stmt = stmt.where(Deal.round_type == stage)
            if is_lead is not None:
                stmt = stmt.where(Deal.is_lead_confirmed == is_lead)
            if is_enterprise_ai is not None:
                stmt = stmt.where(Deal.is_enterprise_ai == is_enterprise_ai)
            if enterprise_category:
                stmt = stmt.where(Deal.enterprise_category == enterprise_category)

            stmt = stmt.order_by(nullslast(Deal.announced_date.desc()), Deal.created_at.desc())
            result = await session.execute(stmt)
            deals = result.scalars().all()

            # Create CSV in memory
            output = io.StringIO()
            writer = csv.writer(output)

            # Header row
            writer.writerow([
                "Company",
                "Round",
                "Amount",
                "Date",
                "Lead Investor",
                "Lead Partner",
                "Enterprise Category",
                "Is Enterprise AI",
                "Verification Snippet",
            ])

            # Data rows
            for deal in deals:
                # Find lead investor
                lead_investor = None
                for inv in deal.investors:
                    if inv.is_lead and inv.is_tracked_fund:
                        lead_investor = inv.investor_name
                        break

                writer.writerow([
                    deal.company.name if deal.company else "Unknown",
                    deal.round_type,
                    deal.amount or "Undisclosed",
                    deal.announced_date.isoformat() if deal.announced_date else "",
                    lead_investor or "",
                    deal.lead_partner_name or "",
                    deal.enterprise_category or "",
                    "Yes" if deal.is_enterprise_ai else "No",
                    deal.verification_snippet or "",
                ])

            output.seek(0)

            # Return as streaming response with CSV content type
            return StreamingResponse(
                iter([output.getvalue()]),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=bud-tracker-deals-{datetime.utcnow().strftime('%Y%m%d')}.csv"
                }
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class StealthDetectionResponse(BaseModel):
    id: int
    fund_slug: str
    detected_url: str
    detected_at: str
    company_name: Optional[str]
    is_confirmed: bool
    notes: Optional[str]


class StealthDetectionsListResponse(BaseModel):
    detections: List[StealthDetectionResponse]
    total: int


@app.get("/stealth-detections", response_model=StealthDetectionsListResponse)
async def list_stealth_detections(
    fund_slug: Optional[str] = Query(None, description="Filter by fund slug"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    List stealth portfolio detections.

    Shows companies added to fund portfolios without press releases.
    """
    try:
        async with get_session() as session:
            stmt = select(StealthDetection).order_by(StealthDetection.detected_at.desc())

            if fund_slug:
                stmt = stmt.where(StealthDetection.fund_slug == fund_slug)

            # Get total count
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total_result = await session.execute(count_stmt)
            total = total_result.scalar() or 0

            # Apply pagination
            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            detections = result.scalars().all()

            return StealthDetectionsListResponse(
                detections=[
                    StealthDetectionResponse(
                        id=d.id,
                        fund_slug=d.fund_slug,
                        detected_url=d.detected_url,
                        detected_at=d.detected_at.isoformat(),
                        company_name=d.company_name,
                        is_confirmed=d.is_confirmed,
                        notes=d.notes,
                    )
                    for d in detections
                ],
                total=total,
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/extract", response_model=ExtractionResponse)
async def extract_funding_deal(
    request: ExtractionRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Extract funding deal information from article text.

    Uses Claude 3.5 Sonnet with chain-of-thought reasoning.
    """
    fund_context = None
    if request.fund_slug and request.fund_slug in FUND_REGISTRY:
        fund_context = FUND_REGISTRY[request.fund_slug]

    try:
        result = await extract_deal(
            article_text=request.text,
            source_url=request.source_url or "",
            fund_context=fund_context
        )

        return ExtractionResponse(
            startup_name=result.startup_name,
            round_label=result.round_label.value,
            amount=result.amount,
            tracked_fund_is_lead=result.tracked_fund_is_lead,
            tracked_fund_name=result.tracked_fund_name,
            tracked_fund_role=result.tracked_fund_role.value if result.tracked_fund_role else None,
            confidence_score=result.confidence_score,
            reasoning_summary=result.reasoning.final_reasoning[:500]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/extract/batch", response_model=List[ExtractionResponse])
async def extract_funding_deals_batch(
    request: BatchExtractionRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Extract funding deals from multiple articles concurrently.

    OPTIMIZED:
    - Process up to 10 articles in parallel
    - Shared prompt cache across extractions
    - Early exit for non-funding content

    Maximum 10 articles per request.
    """
    # Convert to format expected by batch extractor
    articles = [
        {
            "text": article.text,
            "source_url": article.source_url or "",
            "fund_slug": article.fund_slug
        }
        for article in request.articles
    ]

    try:
        results = await extract_deal_batch(articles, max_concurrent=5)

        responses = []
        for result in results:
            responses.append(ExtractionResponse(
                startup_name=result.startup_name,
                round_label=result.round_label.value,
                amount=result.amount,
                tracked_fund_is_lead=result.tracked_fund_is_lead,
                tracked_fund_name=result.tracked_fund_name,
                tracked_fund_role=result.tracked_fund_role.value if result.tracked_fund_role else None,
                confidence_score=result.confidence_score,
                reasoning_summary=result.reasoning.final_reasoning[:500]
            ))

        return responses

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- Scraping Endpoints -----

class ScraperStatusResponse(BaseModel):
    implemented: List[str]
    not_implemented: List[str]
    total_funds: int


class ScrapeResponse(BaseModel):
    fund_slug: str
    articles_found: int
    articles_skipped_duplicate: int = 0
    deals_extracted: int = 0
    deals_saved: int
    errors: List[str]
    duration_seconds: float


class BatchScrapeRequest(BaseModel):
    """Request for scraping multiple funds."""
    fund_slugs: List[str] = Field(..., min_length=1, max_length=18)
    parallel: bool = Field(False, description="Run scrapers in parallel")


@app.get("/scrapers/status", response_model=ScraperStatusResponse)
async def get_scraper_status():
    """Get status of implemented scrapers (cached for 1 hour)."""
    cached = cache.get("scraper_status")
    if cached:
        return cached

    response = ScraperStatusResponse(
        implemented=get_implemented_scrapers(),
        not_implemented=get_unimplemented_scrapers(),
        total_funds=len(FUND_REGISTRY),
    )

    cache.set("scraper_status", response, ttl_seconds=3600)
    return response


@app.post("/scrapers/run/{fund_slug}", response_model=ScrapeResponse)
async def run_scraper(
    fund_slug: str,
    api_key: str = Depends(verify_api_key),
):
    """
    Run scraper for a specific fund.

    Requires API key. Scrapes the fund's news source and extracts deals.
    """
    if fund_slug not in FUND_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Fund '{fund_slug}' not found")

    if fund_slug not in get_implemented_scrapers():
        raise HTTPException(
            status_code=400,
            detail=f"Scraper not implemented for '{fund_slug}'"
        )

    result = await scrape_fund(fund_slug)

    # Invalidate deals cache after scraping
    cache.invalidate("deals")

    return ScrapeResponse(
        fund_slug=result.fund_slug,
        articles_found=result.articles_found,
        articles_skipped_duplicate=result.articles_skipped_duplicate,
        deals_extracted=result.deals_extracted,
        deals_saved=result.deals_saved,
        errors=result.errors,
        duration_seconds=result.duration_seconds,
    )


@app.post("/scrapers/run", response_model=List[ScrapeResponse])
async def run_scrapers_batch(
    request: BatchScrapeRequest,
    api_key: str = Depends(verify_api_key),
):
    """
    Run scrapers for multiple funds.

    OPTIMIZED:
    - Optional parallel execution
    - Process up to 18 funds per request
    """
    # Validate all fund slugs
    for slug in request.fund_slugs:
        if slug not in FUND_REGISTRY:
            raise HTTPException(status_code=404, detail=f"Fund '{slug}' not found")
        if slug not in get_implemented_scrapers():
            raise HTTPException(
                status_code=400,
                detail=f"Scraper not implemented for '{slug}'"
            )

    results = await scrape_all_funds(
        fund_slugs=request.fund_slugs,
        parallel=request.parallel,
        max_parallel_funds=3
    )

    # Invalidate deals cache after scraping
    cache.invalidate("deals")

    return [
        ScrapeResponse(
            fund_slug=r.fund_slug,
            articles_found=r.articles_found,
            articles_skipped_duplicate=r.articles_skipped_duplicate,
            deals_extracted=r.deals_extracted,
            deals_saved=r.deals_saved,
            errors=r.errors,
            duration_seconds=r.duration_seconds,
        )
        for r in results
    ]


# ----- Run All Scrapers -----

@app.post("/scrapers/run-all", response_model=List[ScrapeResponse])
async def run_all_scrapers(
    api_key: str = Depends(verify_api_key),
    parallel: bool = Query(True, description="Run in parallel"),
):
    """
    Run all implemented scrapers.

    This endpoint can be called by external cron services or manually.
    Scrapes all 18 VC funds with parallel execution.
    """
    fund_slugs = get_implemented_scrapers()

    results = await scrape_all_funds(
        fund_slugs=fund_slugs,
        parallel=parallel,
        max_parallel_funds=3
    )

    cache.invalidate("deals")

    return [
        ScrapeResponse(
            fund_slug=r.fund_slug,
            articles_found=r.articles_found,
            articles_skipped_duplicate=r.articles_skipped_duplicate,
            deals_extracted=r.deals_extracted,
            deals_saved=r.deals_saved,
            errors=r.errors,
            duration_seconds=r.duration_seconds,
        )
        for r in results
    ]


# ----- News Aggregator Endpoints -----

class NewsAggregatorResponse(BaseModel):
    articles_fetched: int
    articles_processed: int
    enterprise_ai_deals: int
    lead_deals: int
    deals_saved: int
    skipped_consumer: int
    skipped_participant: int
    errors: int


@app.post("/scrapers/news", response_model=NewsAggregatorResponse)
async def run_news_aggregator(
    days: int = Query(30, ge=1, le=365, description="Number of days to fetch"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run news aggregator to fetch funding news from TechCrunch, Fortune, etc.

    Searches for Enterprise AI deals led by tracked funds.
    Requires NEWSAPI_KEY to be configured.
    """
    from datetime import date, timedelta
    from .harvester.backfill import backfill_deals

    from_date = date.today() - timedelta(days=days)
    to_date = date.today()

    try:
        stats = await backfill_deals(
            from_date=from_date,
            to_date=to_date,
            dry_run=False,
            verbose=False,
        )

        # Invalidate deals cache
        cache.invalidate("deals")

        return NewsAggregatorResponse(**stats)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/backfill", response_model=NewsAggregatorResponse)
async def run_backfill(
    months: int = Query(12, ge=1, le=12, description="Number of months to backfill"),
    dry_run: bool = Query(False, description="Preview without saving"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run historical backfill for Enterprise AI deals.

    Fetches up to 12 months of funding news from NewsAPI.
    NOTE: NewsAPI Developer plan required for >1 month history.
    """
    from .harvester.backfill import backfill_monthly_batches

    try:
        stats = await backfill_monthly_batches(
            months=months,
            dry_run=dry_run,
            verbose=False,
        )

        if not dry_run:
            cache.invalidate("deals")

        return NewsAggregatorResponse(**stats)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- New Data Source Endpoints -----

class SECEdgarResponse(BaseModel):
    filings_found: int
    filings_with_tracked_funds: int
    articles_generated: int


@app.post("/scrapers/sec-edgar", response_model=SECEdgarResponse)
async def run_sec_edgar_scraper(
    hours: int = Query(24, ge=1, le=168, description="Hours to look back"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run SEC EDGAR Form D scraper.

    Fetches recent Form D filings (private placements) from SEC.
    This is the "first signal" - often appears before news.
    No API key required (public SEC data).
    """
    from .harvester.scrapers.sec_edgar import SECEdgarScraper

    try:
        async with SECEdgarScraper() as scraper:
            filings = await scraper.fetch_recent_filings(hours=hours)

            tracked_count = 0
            articles = []
            for filing in filings:
                fund_slug = scraper.match_tracked_fund(filing)
                if fund_slug:
                    tracked_count += 1
                article = await scraper.to_normalized_article(filing, fund_slug)
                articles.append(article)

        cache.invalidate("deals")

        return SECEdgarResponse(
            filings_found=len(filings),
            filings_with_tracked_funds=tracked_count,
            articles_generated=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class BraveSearchResponse(BaseModel):
    queries_executed: int
    results_found: int
    articles_generated: int


@app.post("/scrapers/brave-search", response_model=BraveSearchResponse)
async def run_brave_search_scraper_endpoint(
    freshness: str = Query("pw", description="pd=past day, pw=past week, pm=past month"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run Brave Search API scraper.

    Searches for funding news with "led by" language for tracked funds.
    Requires BRAVE_SEARCH_KEY to be configured.
    """
    from .harvester.scrapers.brave_search import BraveSearchScraper

    if not settings.brave_search_key:
        raise HTTPException(status_code=400, detail="BRAVE_SEARCH_KEY not configured")

    try:
        async with BraveSearchScraper() as scraper:
            fund_results = await scraper.search_all_funds(freshness=freshness)

            total_results = sum(len(results) for results in fund_results.values())

            articles = await scraper.scrape_all(freshness=freshness)

        cache.invalidate("deals")

        return BraveSearchResponse(
            queries_executed=len(fund_results),
            results_found=total_results,
            articles_generated=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FirecrawlRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1, max_length=10, description="URLs to scrape")


class FirecrawlResponse(BaseModel):
    urls_submitted: int
    urls_scraped: int
    articles_generated: int


@app.post("/scrapers/firecrawl", response_model=FirecrawlResponse)
async def run_firecrawl_scraper_endpoint(
    request: FirecrawlRequest,
    api_key: str = Depends(verify_api_key),
):
    """
    Scrape URLs using Firecrawl API.

    Use this for JS-heavy PR sites (PRNewswire, BusinessWire, etc.)
    that have bot detection. Returns clean markdown text.
    Requires FIRECRAWL_API_KEY to be configured.
    """
    from .harvester.scrapers.firecrawl_scraper import FirecrawlScraper

    if not settings.firecrawl_api_key:
        raise HTTPException(status_code=400, detail="FIRECRAWL_API_KEY not configured")

    try:
        async with FirecrawlScraper() as scraper:
            articles = await scraper.scrape_pr_urls(request.urls)

        return FirecrawlResponse(
            urls_submitted=len(request.urls),
            urls_scraped=len(articles),
            articles_generated=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AllSourcesResponse(BaseModel):
    sec_edgar: SECEdgarResponse
    brave_search: Optional[BraveSearchResponse] = None
    newsapi: NewsAggregatorResponse


@app.post("/scrapers/all-sources", response_model=AllSourcesResponse)
async def run_all_sources(
    days: int = Query(7, ge=1, le=30, description="Days to look back"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run all data source scrapers in parallel.

    Fetches from:
    1. SEC EDGAR (Form D filings) - always runs
    2. Brave Search (if configured)
    3. NewsAPI (if configured)

    This is the recommended endpoint for comprehensive deal discovery.
    """
    from datetime import date, timedelta
    from .harvester.backfill import backfill_deals
    from .harvester.scrapers.sec_edgar import SECEdgarScraper
    from .harvester.scrapers.brave_search import BraveSearchScraper

    results = {}

    # SEC EDGAR (always runs - free)
    try:
        async with SECEdgarScraper() as scraper:
            filings = await scraper.fetch_recent_filings(hours=days * 24)
            tracked_count = sum(1 for f in filings if scraper.match_tracked_fund(f))

        results["sec_edgar"] = SECEdgarResponse(
            filings_found=len(filings),
            filings_with_tracked_funds=tracked_count,
            articles_generated=len(filings),
        )
    except Exception as e:
        results["sec_edgar"] = SECEdgarResponse(filings_found=0, filings_with_tracked_funds=0, articles_generated=0)

    # Brave Search (if configured)
    if settings.brave_search_key:
        try:
            freshness = "pd" if days <= 1 else ("pw" if days <= 7 else "pm")
            async with BraveSearchScraper() as scraper:
                articles = await scraper.scrape_all(freshness=freshness)

            results["brave_search"] = BraveSearchResponse(
                queries_executed=18,
                results_found=len(articles),
                articles_generated=len(articles),
            )
        except Exception:
            results["brave_search"] = None
    else:
        results["brave_search"] = None

    # NewsAPI
    try:
        from_date = date.today() - timedelta(days=days)
        to_date = date.today()

        stats = await backfill_deals(
            from_date=from_date,
            to_date=to_date,
            dry_run=False,
            verbose=False,
        )

        results["newsapi"] = NewsAggregatorResponse(**stats)
    except Exception:
        results["newsapi"] = NewsAggregatorResponse(
            articles_fetched=0,
            articles_processed=0,
            enterprise_ai_deals=0,
            lead_deals=0,
            deals_saved=0,
            skipped_consumer=0,
            skipped_participant=0,
            errors=0,
        )

    cache.invalidate("deals")

    return AllSourcesResponse(**results)


# ----- Scheduler Status -----

class SchedulerJobStatus(BaseModel):
    id: str
    name: str
    next_run: Optional[str] = None


class SchedulerStatusResponse(BaseModel):
    status: str
    jobs: List[SchedulerJobStatus]


@app.get("/scheduler/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status():
    """Get scheduler status and next run times."""
    sched = scheduler_module.scheduler
    if not sched:
        return SchedulerStatusResponse(status="not_initialized", jobs=[])

    jobs = []
    for job in sched.get_jobs():
        jobs.append(SchedulerJobStatus(
            id=job.id,
            name=job.name,
            next_run=job.next_run_time.isoformat() if job.next_run_time else None,
        ))

    return SchedulerStatusResponse(
        status="running" if sched.running else "stopped",
        jobs=jobs
    )


# ----- Cache Management -----

@app.post("/cache/clear")
async def clear_cache(api_key: str = Depends(verify_api_key)):
    """Clear all cached responses."""
    cache.clear()
    return {"status": "cache cleared"}


# ----- CLI Runner -----

def run_server():
    """Run the FastAPI server."""
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )


if __name__ == "__main__":
    run_server()

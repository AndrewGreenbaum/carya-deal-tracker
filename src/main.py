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
from .archivist.models import Fund
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


# ----- URL Validation Helpers -----

def is_valid_url(url: str) -> bool:
    """Check if URL is a real URL (not a search URL or placeholder)."""
    if not url:
        return False
    url_lower = url.lower().strip()
    # Invalid placeholders
    invalid_values = ["not mentioned", "not specified", "unknown", "n/a", "none", ""]
    if url_lower in invalid_values:
        return False
    # Must start with http
    if not url_lower.startswith("http"):
        return False
    # Reject search URLs - we want real direct URLs only
    if "google.com/search" in url_lower:
        return False
    if "linkedin.com/search" in url_lower:
        return False
    return True


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
    # Validate against configured API keys
    if api_key not in settings.valid_api_keys:
        raise HTTPException(status_code=403, detail="Invalid API key")
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

            # Filter by fund_slug - join through DealInvestor to Fund
            if fund_slug:
                base_stmt = base_stmt.join(DealInvestor, Deal.id == DealInvestor.deal_id).join(
                    Fund, DealInvestor.fund_id == Fund.id
                ).where(Fund.slug == fund_slug).distinct()

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

                # Get company name
                company_name = deal.company.name if deal.company else "Unknown"

                # Parse founders from JSON - only use REAL LinkedIn URLs
                founders = None
                if deal.founders_json:
                    try:
                        founders_data = json.loads(deal.founders_json)
                        founders = []
                        for f in founders_data:
                            founder_name = f.get("name", "Unknown")
                            # Only use LinkedIn URL if it's a real direct profile URL
                            linkedin_url = f.get("linkedin_url")
                            if not is_valid_url(linkedin_url):
                                linkedin_url = None  # Return null, not a search URL
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

                # Company links - only use REAL URLs, not search URLs
                db_website = deal.company.website if deal.company else None
                db_linkedin = deal.company.linkedin_url if deal.company else None

                # Only return URL if it's a valid direct URL
                company_website = db_website if is_valid_url(db_website) else None
                company_linkedin = db_linkedin if is_valid_url(db_linkedin) else None

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

            # Filter by fund_slug - join through DealInvestor to Fund
            if fund_slug:
                stmt = stmt.join(DealInvestor, Deal.id == DealInvestor.deal_id).join(
                    Fund, DealInvestor.fund_id == Fund.id
                ).where(Fund.slug == fund_slug).distinct()

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


# ----- Data Source Endpoints -----

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
            seen_ciks = set()  # Deduplication by CIK

            for filing in filings:
                # Skip duplicate CIKs
                if filing.cik in seen_ciks:
                    continue
                seen_ciks.add(filing.cik)

                # Fetch filing details (amount, industry, state, investors)
                filing = await scraper.fetch_filing_details(filing)
                await asyncio.sleep(0.2)  # Rate limit

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
    include_regional: bool = Query(False, description="Include 10 regional market searches"),
    include_early_signals: bool = Query(False, description="Include early signal detection (hiring, expansion)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run Brave Search API scraper with expanded coverage options.

    Searches for funding news with "led by" AND participation language for tracked funds.
    Requires BRAVE_SEARCH_KEY to be configured.

    Query breakdown (base):
    - 18 fund lead queries
    - 18 fund participation queries
    - 1 enterprise AI query
    - 1 stealth query
    = 38 queries (base)

    Optional expansions:
    - include_regional: +10 queries (Europe, UK, Germany, France, Asia, India, Singapore, LATAM, Brazil, Israel)
    - include_early_signals: +12 queries (hiring signals, expansion news)

    Full expansion = 60 queries total
    """
    from .harvester.scrapers.brave_search import BraveSearchScraper

    if not settings.brave_search_key:
        raise HTTPException(status_code=400, detail="BRAVE_SEARCH_KEY not configured")

    try:
        async with BraveSearchScraper() as scraper:
            # Run full scrape with expanded options
            articles = await scraper.scrape_all(
                freshness=freshness,
                include_enterprise=True,
                include_participation=True,
                include_stealth=True,
                include_regional=include_regional,
                include_early_signals=include_early_signals,
            )

        cache.invalidate("deals")

        # Calculate query count
        queries_executed = 38  # Base: 18 lead + 18 participation + 1 enterprise + 1 stealth
        if include_regional:
            queries_executed += 10  # 10 regions
        if include_early_signals:
            queries_executed += 12  # 12 early signal queries

        return BraveSearchResponse(
            queries_executed=queries_executed,
            results_found=len(articles),  # Before dedup
            articles_generated=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FastScanResponse(BaseModel):
    """Response for fast-scan endpoint."""
    queries_executed: int
    results_found: int
    scan_type: str
    freshness: str


@app.post("/scrapers/fast-scan", response_model=FastScanResponse)
async def run_fast_scan_endpoint(
    api_key: str = Depends(verify_api_key),
):
    """
    Fast scan for real-time deal detection (run hourly).

    Lightweight scan using past-day freshness for faster detection:
    - 18 fund lead queries only (no participation)
    - 12 early signal queries (hiring, expansion)
    - 1 stealth query
    = 31 queries with pd (past day) freshness

    Designed to run every hour for near real-time detection.
    Use /scrapers/brave-search with full options for comprehensive weekly scans.
    """
    from .harvester.scrapers.brave_search import BraveSearchScraper

    if not settings.brave_search_key:
        raise HTTPException(status_code=400, detail="BRAVE_SEARCH_KEY not configured")

    try:
        async with BraveSearchScraper() as scraper:
            # Fast scan: lead queries + early signals only, past day
            articles = await scraper.scrape_all(
                freshness="pd",  # Past day for speed
                include_enterprise=False,  # Skip for speed
                include_participation=False,  # Skip for speed
                include_stealth=True,
                include_regional=False,
                include_early_signals=True,  # Key for fast detection
            )

        cache.invalidate("deals")

        # Query count: 18 lead + 1 stealth + 12 early signals = 31
        queries_executed = 31

        return FastScanResponse(
            queries_executed=queries_executed,
            results_found=len(articles),
            scan_type="fast",
            freshness="pd",
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


# ----- Google Alerts Endpoints -----

class GoogleAlertsResponse(BaseModel):
    feeds_configured: int
    articles_found: int
    setup_instructions: Optional[str] = None


@app.post("/scrapers/google-alerts", response_model=GoogleAlertsResponse)
async def run_google_alerts_scraper(
    api_key: str = Depends(verify_api_key),
):
    """
    Run Google Alerts RSS scraper.

    Fetches articles from configured Google Alerts RSS feeds.
    Set GOOGLE_ALERTS_FEEDS environment variable with comma-separated feed URLs.
    """
    from .harvester.scrapers.google_alerts import GoogleAlertsScraper, get_alert_setup_instructions

    feed_urls = []
    if settings.google_alerts_feeds:
        feed_urls = [f.strip() for f in settings.google_alerts_feeds.split(',') if f.strip()]

    if not feed_urls:
        return GoogleAlertsResponse(
            feeds_configured=0,
            articles_found=0,
            setup_instructions=get_alert_setup_instructions(),
        )

    try:
        async with GoogleAlertsScraper(feed_urls) as scraper:
            articles = await scraper.scrape_all()

        return GoogleAlertsResponse(
            feeds_configured=len(feed_urls),
            articles_found=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scrapers/google-alerts/setup")
async def get_google_alerts_setup():
    """Get setup instructions for Google Alerts."""
    from .harvester.scrapers.google_alerts import get_alert_setup_instructions
    return {"instructions": get_alert_setup_instructions()}


# ----- Twitter Monitor Endpoints -----

class TwitterMonitorResponse(BaseModel):
    tweets_found: int
    api_calls_used: int
    configured: bool
    setup_instructions: Optional[str] = None


@app.post("/scrapers/twitter", response_model=TwitterMonitorResponse)
async def run_twitter_monitor(
    hours_back: int = Query(168, ge=1, le=168, description="Hours to look back (max 7 days)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run Twitter/X monitor for VC funding announcements.

    Monitors official VC accounts and searches for funding news.
    Requires TWITTER_BEARER_TOKEN environment variable.

    Free tier limit: ~100 API calls per run (conservative to stay under 1,500/month)
    """
    from .harvester.scrapers.twitter_monitor import TwitterMonitor, get_twitter_setup_instructions

    if not settings.twitter_bearer_token:
        return TwitterMonitorResponse(
            tweets_found=0,
            api_calls_used=0,
            configured=False,
            setup_instructions=get_twitter_setup_instructions(),
        )

    try:
        async with TwitterMonitor() as monitor:
            articles = await monitor.scrape_all(hours_back=hours_back)
            api_calls = monitor._request_count

        return TwitterMonitorResponse(
            tweets_found=len(articles),
            api_calls_used=api_calls,
            configured=True,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scrapers/twitter/setup")
async def get_twitter_setup():
    """Get setup instructions for Twitter API."""
    from .harvester.scrapers.twitter_monitor import get_twitter_setup_instructions
    return {"instructions": get_twitter_setup_instructions()}


# ----- RSS Feed Scrapers -----

class TechCrunchResponse(BaseModel):
    articles_found: int
    funding_articles: int


@app.post("/scrapers/techcrunch", response_model=TechCrunchResponse)
async def run_techcrunch_scraper_endpoint(
    hours_back: int = Query(168, ge=1, le=720, description="Hours to look back (default 7 days)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run TechCrunch RSS scraper.

    Fetches startup and venture articles from TechCrunch RSS feeds.
    Filters for funding-related content mentioning tracked funds.
    FREE - no API key required.
    """
    from .harvester.scrapers.techcrunch_rss import TechCrunchScraper

    try:
        async with TechCrunchScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=hours_back)

        cache.invalidate("deals")

        return TechCrunchResponse(
            articles_found=len(articles),
            funding_articles=len([a for a in articles if a.fund_slug]),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FortuneResponse(BaseModel):
    articles_found: int
    funding_articles: int


@app.post("/scrapers/fortune", response_model=FortuneResponse)
async def run_fortune_scraper_endpoint(
    hours_back: int = Query(168, ge=1, le=720, description="Hours to look back (default 7 days)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run Fortune Term Sheet RSS scraper.

    Fetches deal-focused content from Fortune's Term Sheet newsletter feed.
    One of the best curated sources for funding news.
    FREE - no API key required.
    """
    from .harvester.scrapers.fortune_term_sheet import FortuneTermSheetScraper

    try:
        async with FortuneTermSheetScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=hours_back)

        cache.invalidate("deals")

        return FortuneResponse(
            articles_found=len(articles),
            funding_articles=len([a for a in articles if a.fund_slug]),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- Community/Early Signal Scrapers -----

class YCombinatorResponse(BaseModel):
    companies_found: int
    blog_posts_found: int
    total_articles: int


@app.post("/scrapers/ycombinator", response_model=YCombinatorResponse)
async def run_ycombinator_scraper_endpoint(
    api_key: str = Depends(verify_api_key),
):
    """
    Run Y Combinator scraper.

    Fetches companies from recent YC batches (Demo Day = 200+ companies twice/year).
    Also monitors YC blog for announcements.
    FREE - no API key required.
    """
    from .harvester.scrapers.ycombinator import YCombinatorScraper

    try:
        async with YCombinatorScraper() as scraper:
            articles = await scraper.scrape_all()

        # Count by type
        companies = len([a for a in articles if 'demo_day' in a.tags])
        blog_posts = len([a for a in articles if 'blog' in a.tags])

        cache.invalidate("deals")

        return YCombinatorResponse(
            companies_found=companies,
            blog_posts_found=blog_posts,
            total_articles=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class GitHubTrendingResponse(BaseModel):
    repos_found: int
    enterprise_devtools: int


@app.post("/scrapers/github-trending", response_model=GitHubTrendingResponse)
async def run_github_trending_scraper_endpoint(
    since: str = Query("daily", description="Time range: daily, weekly, monthly"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run GitHub Trending scraper.

    Fetches trending repositories - dev tools often appear before funding announcements.
    Filters for enterprise dev tools (AI, infrastructure, DevOps, etc.).
    FREE - no API key required.
    """
    from .harvester.scrapers.github_trending import GitHubTrendingScraper

    try:
        async with GitHubTrendingScraper() as scraper:
            # Get all repos first (unfiltered)
            all_repos = await scraper.fetch_all_trending(since=since)
            # Then get filtered articles
            articles = await scraper.scrape_all(since=since, filter_enterprise=True)

        return GitHubTrendingResponse(
            repos_found=len(all_repos),
            enterprise_devtools=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class HackerNewsResponse(BaseModel):
    launch_hn_posts: int
    show_hn_posts: int
    funding_posts: int
    total_articles: int


@app.post("/scrapers/hackernews", response_model=HackerNewsResponse)
async def run_hackernews_scraper_endpoint(
    hours_back: int = Query(168, ge=1, le=720, description="Hours to look back (default 7 days)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run Hacker News scraper.

    Monitors HN for Launch HN posts (startup launches) and funding news.
    Launch posts often precede/coincide with funding announcements.
    FREE - uses public HN Algolia API.
    """
    from .harvester.scrapers.hackernews import HackerNewsScraper

    try:
        async with HackerNewsScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=hours_back)

        # Count by type
        launch_hn = len([a for a in articles if 'launch_hn' in a.tags])
        show_hn = len([a for a in articles if 'show_hn' in a.tags])
        funding = len([a for a in articles if a.fund_slug])

        return HackerNewsResponse(
            launch_hn_posts=launch_hn,
            show_hn_posts=show_hn,
            funding_posts=funding,
            total_articles=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- LinkedIn Jobs (Stealth Detection) -----

class LinkedInJobsResponse(BaseModel):
    queries_executed: int
    stealth_signals_found: int
    fund_matches: int
    total_jobs: int


@app.post("/scrapers/linkedin-jobs", response_model=LinkedInJobsResponse)
async def run_linkedin_jobs_scraper_endpoint(
    api_key: str = Depends(verify_api_key),
):
    """
    Run LinkedIn Jobs scraper for stealth startup detection.

    Searches for job posts mentioning:
    - "stealth startup" / "stealth mode"
    - "backed by [VC name]" for tracked funds
    - "recently funded" startups

    Uses Brave Search API (requires BRAVE_SEARCH_KEY).
    ~17 queries per run.
    """
    from .harvester.scrapers.linkedin_jobs import LinkedInJobsScraper, STEALTH_JOB_QUERIES
    from .archivist.storage import save_stealth_detection
    from .archivist.database import get_session

    if not settings.brave_search_key:
        raise HTTPException(status_code=400, detail="BRAVE_SEARCH_KEY not configured")

    try:
        async with LinkedInJobsScraper() as scraper:
            articles = await scraper.scrape_all()

        stealth_count = len([a for a in articles if 'stealth' in a.tags])
        fund_matches = len([a for a in articles if a.fund_slug])

        # Save stealth detections to database
        async with get_session() as session:
            for article in articles:
                if 'stealth' in article.tags or article.fund_slug:
                    await save_stealth_detection(
                        session=session,
                        fund_slug=article.fund_slug or "unknown",
                        detected_url=article.url,
                        company_name=article.title.replace("Stealth Signal: ", "").split(" - ")[0] if article.title else None,
                        notes=f"Source: LinkedIn Jobs | Tags: {', '.join(article.tags)}",
                    )

        return LinkedInJobsResponse(
            queries_executed=len(STEALTH_JOB_QUERIES),
            stealth_signals_found=stealth_count,
            fund_matches=fund_matches,
            total_jobs=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- Tier 1 Stealth Detection Scrapers -----

class PortfolioDiffResponse(BaseModel):
    funds_checked: int
    new_companies_found: int
    total_articles: int


@app.post("/scrapers/portfolio-diff", response_model=PortfolioDiffResponse)
async def run_portfolio_diff_scraper_endpoint(
    fund_slugs: Optional[str] = Query(None, description="Comma-separated fund slugs (default: all 18)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run VC Portfolio Page Diff scraper.

    Monitors VC fund portfolio pages for new company additions.
    VCs often add companies to portfolios BEFORE public announcements.
    FREE - no API key required (scrapes public portfolio pages).

    Tier 1 stealth detection - one of the BEST free sources.
    """
    from .harvester.scrapers.portfolio_diff import PortfolioDiffScraper
    from .archivist.storage import save_stealth_detection
    from .archivist.database import get_session

    try:
        slugs = None
        if fund_slugs:
            slugs = [s.strip() for s in fund_slugs.split(',') if s.strip()]

        async with PortfolioDiffScraper() as scraper:
            articles = await scraper.scrape_all(fund_slugs=slugs)

        funds_checked = len(slugs) if slugs else 18  # 18 funds in PORTFOLIO_URLS

        # Save stealth detections to database
        async with get_session() as session:
            for article in articles:
                company_name = article.title.replace("Stealth Addition: ", "").split(" added to ")[0] if article.title else None
                await save_stealth_detection(
                    session=session,
                    fund_slug=article.fund_slug or "unknown",
                    detected_url=article.url,
                    company_name=company_name,
                    notes=f"Source: Portfolio Diff | Tags: {', '.join(article.tags)}",
                )

        cache.invalidate("deals")

        return PortfolioDiffResponse(
            funds_checked=funds_checked,
            new_companies_found=len(articles),
            total_articles=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class USPTOTrademarkResponse(BaseModel):
    trademarks_found: int
    tech_trademarks: int
    total_articles: int


@app.post("/scrapers/uspto-trademarks", response_model=USPTOTrademarkResponse)
async def run_uspto_trademark_scraper_endpoint(
    days_back: int = Query(7, ge=1, le=30, description="Days to look back"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run USPTO Trademark scraper.

    Monitors USPTO for new trademark applications that might indicate:
    - New startup names (company formation)
    - New product launches
    - Stealth companies emerging

    Trademark applications are PUBLIC and often filed BEFORE announcements.
    Uses Brave Search API (requires BRAVE_SEARCH_KEY).

    Filters for tech classes:
    - Class 9: Software, computers, electronics
    - Class 35: Business services, SaaS
    - Class 42: Technology services, cloud computing
    """
    from .harvester.scrapers.uspto_trademarks import USPTOTrademarkScraper

    if not settings.brave_search_key:
        raise HTTPException(status_code=400, detail="BRAVE_SEARCH_KEY not configured")

    try:
        async with USPTOTrademarkScraper() as scraper:
            # Get all trademarks first
            trademarks = await scraper.search_recent_trademarks(days_back=days_back)
            # Get filtered articles (tech only)
            articles = await scraper.scrape_all(days_back=days_back, tech_only=True)

        cache.invalidate("deals")

        return USPTOTrademarkResponse(
            trademarks_found=len(trademarks),
            tech_trademarks=len(articles),
            total_articles=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class DelawareCorpsResponse(BaseModel):
    entities_found: int
    tech_entities: int
    high_signal_entities: int
    total_articles: int


@app.post("/scrapers/delaware-corps", response_model=DelawareCorpsResponse)
async def run_delaware_corps_scraper_endpoint(
    days_back: int = Query(7, ge=1, le=30, description="Days to look back"),
    min_score: int = Query(3, ge=0, le=10, description="Minimum score to include"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run Delaware Division of Corporations scraper.

    Monitors Delaware for new tech company formations.
    ~67% of Fortune 500 and most VC-backed startups incorporate in Delaware.

    Scoring signals:
    - Tech company name pattern (+3)
    - Startup-friendly registered agent like Stripe Atlas, Clerky (+5)
    - Recent formation (+2)
    - AI/ML in name (+2)

    Uses Brave Search + OpenCorporates API.
    Requires BRAVE_SEARCH_KEY for best results.
    """
    from .harvester.scrapers.delaware_corps import DelawareCorpsScraper

    if not settings.brave_search_key:
        raise HTTPException(status_code=400, detail="BRAVE_SEARCH_KEY not configured")

    try:
        async with DelawareCorpsScraper() as scraper:
            # Get all articles (already filtered by score)
            articles = await scraper.scrape_all(days_back=days_back, min_score=min_score)

            # Also get raw entities for counts
            brave_entities = await scraper.search_recent_incorporations(days_back=days_back)
            agg_entities = await scraper.search_aggregators()

        all_entities = len(brave_entities) + len(agg_entities)
        tech_entities = len([e for e in brave_entities if e.has_tech_name] +
                          [e for e in agg_entities if e.has_tech_name])
        high_signal = len([e for e in brave_entities if e.has_startup_agent] +
                         [e for e in agg_entities if e.has_startup_agent])

        cache.invalidate("deals")

        return DelawareCorpsResponse(
            entities_found=all_entities,
            tech_entities=tech_entities,
            high_signal_entities=high_signal,
            total_articles=len(articles),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- SEC + Delaware Cross-Reference -----

class LeadScoreBreakdown(BaseModel):
    delaware: Optional[int] = None
    first_sale: Optional[int] = None
    tech_industry: Optional[int] = None
    amount_5m_plus: Optional[int] = None
    amount_1m_plus: Optional[int] = None
    amount_500k_plus: Optional[int] = None
    vc_fund_match: Optional[int] = None
    startup_agent: Optional[int] = None
    tech_name: Optional[int] = None


class HighPriorityLeadResponse(BaseModel):
    company_name: str
    cik: str
    filing_date: str
    filing_url: str
    state_of_incorporation: Optional[str]
    amount_raised: Optional[str]
    total_offering: Optional[str]
    is_first_sale: bool
    industry: Optional[str]
    score: int
    priority: str
    score_breakdown: LeadScoreBreakdown
    is_delaware: bool
    is_tech_industry: bool
    has_vc_fund_match: bool
    has_startup_agent: bool
    matched_fund_slug: Optional[str]
    registered_agent: Optional[str]


class SECDelawareCrossrefResponse(BaseModel):
    leads_found: int
    high_priority: int
    strong_signal: int
    watch_list: int
    leads: List[HighPriorityLeadResponse]


@app.post("/scrapers/sec-delaware-crossref", response_model=SECDelawareCrossrefResponse)
async def run_sec_delaware_crossref_endpoint(
    days_back: int = Query(30, ge=1, le=90, description="Days to look back"),
    min_score: int = Query(3, ge=0, le=15, description="Minimum score to include"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run SEC Form D + Delaware cross-reference for high-priority leads.

    Combines SEC Form D filings with Delaware registry data using multi-signal scoring:

    **Scoring:**
    - Delaware incorporation: +2
    - First sale (new round): +3
    - Tech industry: +2
    - Amount $5M+: +3, $1M+: +2, $500K+: +1
    - Tracked VC fund match: +5
    - Startup-friendly agent (Stripe Atlas, Clerky): +5
    - Tech company name: +2

    **Priority Tiers:**
    - HIGH (10+): Strong VC-backed signals
    - STRONG (7-9): Good indicators
    - WATCH (3-6): Worth monitoring

    Uses SEC EDGAR (FREE) + OpenCorporates API (FREE with rate limits).
    """
    from .harvester.scrapers.sec_delaware_crossref import get_high_priority_leads

    try:
        leads = await get_high_priority_leads(
            days_back=days_back,
            min_score=min_score,
            delaware_only=True,
            tech_only=True,
            min_amount=500_000,
        )

        # Count by priority
        high_count = len([l for l in leads if l.priority == "HIGH"])
        strong_count = len([l for l in leads if l.priority == "STRONG"])
        watch_count = len([l for l in leads if l.priority == "WATCH"])

        # Convert to response format
        lead_responses = []
        for lead in leads:
            lead_responses.append(HighPriorityLeadResponse(
                company_name=lead.company_name,
                cik=lead.cik,
                filing_date=lead.filing_date.isoformat(),
                filing_url=lead.filing_url,
                state_of_incorporation=lead.state_of_incorporation,
                amount_raised=lead.amount_raised,
                total_offering=lead.total_offering,
                is_first_sale=lead.is_first_sale,
                industry=lead.industry,
                score=lead.score,
                priority=lead.priority,
                score_breakdown=LeadScoreBreakdown(**lead.score_breakdown),
                is_delaware=lead.is_delaware,
                is_tech_industry=lead.is_tech_industry,
                has_vc_fund_match=lead.has_vc_fund_match,
                has_startup_agent=lead.has_startup_agent,
                matched_fund_slug=lead.matched_fund_slug,
                registered_agent=lead.delaware_entity.registered_agent if lead.delaware_entity else None,
            ))

        cache.invalidate("deals")

        return SECDelawareCrossrefResponse(
            leads_found=len(leads),
            high_priority=high_count,
            strong_signal=strong_count,
            watch_list=watch_count,
            leads=lead_responses,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- Tech Funding News -----

class TechFundingNewsResponse(BaseModel):
    feeds_scraped: int
    articles_found: int
    funding_articles: int
    fund_matches: int


@app.post("/scrapers/tech-funding-news", response_model=TechFundingNewsResponse)
async def run_tech_funding_news_endpoint(
    hours_back: int = Query(168, ge=1, le=720, description="Hours to look back (default 7 days)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run Tech Funding News scraper.

    Premium source for startup funding news. Covers SaaS, AI, FinTech.
    Multiple category feeds for comprehensive coverage.
    FREE - uses public RSS feeds.
    """
    from .harvester.scrapers.tech_funding_news import TechFundingNewsScraper

    try:
        async with TechFundingNewsScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=hours_back)

        fund_matches = len([a for a in articles if a.fund_slug])

        return TechFundingNewsResponse(
            feeds_scraped=5,  # main + 4 categories
            articles_found=len(articles),
            funding_articles=len(articles),  # Already filtered for funding
            fund_matches=fund_matches,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- Ventureburn -----

class VentureburnResponse(BaseModel):
    articles_found: int
    funding_articles: int
    fund_matches: int


@app.post("/scrapers/ventureburn", response_model=VentureburnResponse)
async def run_ventureburn_endpoint(
    hours_back: int = Query(168, ge=1, le=720, description="Hours to look back (default 7 days)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Run Ventureburn scraper.

    Covers emerging markets startup news (Africa, global).
    Filters out crypto noise, focuses on VC funding.
    FREE - uses public RSS feed.
    """
    from .harvester.scrapers.ventureburn import VentureburnScraper

    try:
        async with VentureburnScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=hours_back)

        fund_matches = len([a for a in articles if a.fund_slug])

        return VentureburnResponse(
            articles_found=len(articles),
            funding_articles=len(articles),  # Already filtered
            fund_matches=fund_matches,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- All Sources Combined -----

class AllSourcesResponse(BaseModel):
    sec_edgar: SECEdgarResponse
    brave_search: Optional[BraveSearchResponse] = None
    google_alerts: Optional[GoogleAlertsResponse] = None
    twitter: Optional[TwitterMonitorResponse] = None
    techcrunch: Optional[TechCrunchResponse] = None
    fortune: Optional[FortuneResponse] = None
    ycombinator: Optional[YCombinatorResponse] = None
    github_trending: Optional[GitHubTrendingResponse] = None
    hackernews: Optional[HackerNewsResponse] = None
    linkedin_jobs: Optional[LinkedInJobsResponse] = None
    tech_funding_news: Optional[TechFundingNewsResponse] = None
    ventureburn: Optional[VentureburnResponse] = None
    # Tier 1 Stealth Detection
    portfolio_diff: Optional[PortfolioDiffResponse] = None
    uspto_trademarks: Optional[USPTOTrademarkResponse] = None
    delaware_corps: Optional[DelawareCorpsResponse] = None


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
    3. Google Alerts (if configured)
    4. Twitter (if configured)

    This is the recommended endpoint for comprehensive deal discovery.
    """
    from .harvester.scrapers.sec_edgar import SECEdgarScraper
    from .harvester.scrapers.brave_search import BraveSearchScraper
    from .harvester.scrapers.google_alerts import GoogleAlertsScraper
    from .harvester.scrapers.twitter_monitor import TwitterMonitor

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
    except Exception:
        results["sec_edgar"] = SECEdgarResponse(filings_found=0, filings_with_tracked_funds=0, articles_generated=0)

    # Brave Search (if configured) - 2x coverage with participation + stealth
    if settings.brave_search_key:
        try:
            freshness = "pd" if days <= 1 else ("pw" if days <= 7 else "pm")
            async with BraveSearchScraper() as scraper:
                articles = await scraper.scrape_all(
                    freshness=freshness,
                    include_enterprise=True,
                    include_participation=True,
                    include_stealth=True,
                )

            # 18 lead + 18 participation + 1 enterprise + 1 stealth = 38 queries
            results["brave_search"] = BraveSearchResponse(
                queries_executed=38,
                results_found=len(articles),
                articles_generated=len(articles),
            )
        except Exception:
            results["brave_search"] = None
    else:
        results["brave_search"] = None

    # Google Alerts (if configured)
    if settings.google_alerts_feeds:
        try:
            feed_urls = [f.strip() for f in settings.google_alerts_feeds.split(',') if f.strip()]
            async with GoogleAlertsScraper(feed_urls) as scraper:
                articles = await scraper.scrape_all()

            results["google_alerts"] = GoogleAlertsResponse(
                feeds_configured=len(feed_urls),
                articles_found=len(articles),
            )
        except Exception:
            results["google_alerts"] = None
    else:
        results["google_alerts"] = None

    # Twitter (if configured)
    if settings.twitter_bearer_token:
        try:
            async with TwitterMonitor() as monitor:
                articles = await monitor.scrape_all(hours_back=days * 24)
                api_calls = monitor._request_count

            results["twitter"] = TwitterMonitorResponse(
                tweets_found=len(articles),
                api_calls_used=api_calls,
                configured=True,
            )
        except Exception:
            results["twitter"] = None
    else:
        results["twitter"] = None

    # TechCrunch RSS (always runs - free)
    try:
        from .harvester.scrapers.techcrunch_rss import TechCrunchScraper
        async with TechCrunchScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)
        results["techcrunch"] = TechCrunchResponse(
            articles_found=len(articles),
            funding_articles=len([a for a in articles if a.fund_slug]),
        )
    except Exception:
        results["techcrunch"] = None

    # Fortune Term Sheet (always runs - free)
    try:
        from .harvester.scrapers.fortune_term_sheet import FortuneTermSheetScraper
        async with FortuneTermSheetScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)
        results["fortune"] = FortuneResponse(
            articles_found=len(articles),
            funding_articles=len([a for a in articles if a.fund_slug]),
        )
    except Exception:
        results["fortune"] = None

    # Y Combinator (always runs - free)
    try:
        from .harvester.scrapers.ycombinator import YCombinatorScraper
        async with YCombinatorScraper() as scraper:
            articles = await scraper.scrape_all()
        companies = len([a for a in articles if 'demo_day' in a.tags])
        blog_posts = len([a for a in articles if 'blog' in a.tags])
        results["ycombinator"] = YCombinatorResponse(
            companies_found=companies,
            blog_posts_found=blog_posts,
            total_articles=len(articles),
        )
    except Exception:
        results["ycombinator"] = None

    # GitHub Trending (always runs - free)
    try:
        from .harvester.scrapers.github_trending import GitHubTrendingScraper
        async with GitHubTrendingScraper() as scraper:
            all_repos = await scraper.fetch_all_trending(since="daily")
            articles = await scraper.scrape_all(since="daily", filter_enterprise=True)
        results["github_trending"] = GitHubTrendingResponse(
            repos_found=len(all_repos),
            enterprise_devtools=len(articles),
        )
    except Exception:
        results["github_trending"] = None

    # Hacker News (always runs - free)
    try:
        from .harvester.scrapers.hackernews import HackerNewsScraper
        async with HackerNewsScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)
        launch_hn = len([a for a in articles if 'launch_hn' in a.tags])
        show_hn = len([a for a in articles if 'show_hn' in a.tags])
        funding = len([a for a in articles if a.fund_slug])
        results["hackernews"] = HackerNewsResponse(
            launch_hn_posts=launch_hn,
            show_hn_posts=show_hn,
            funding_posts=funding,
            total_articles=len(articles),
        )
    except Exception:
        results["hackernews"] = None

    # LinkedIn Jobs - Stealth Detection (if Brave Search configured)
    if settings.brave_search_key:
        try:
            from .harvester.scrapers.linkedin_jobs import LinkedInJobsScraper, STEALTH_JOB_QUERIES
            async with LinkedInJobsScraper() as scraper:
                articles = await scraper.scrape_all()
            stealth_count = len([a for a in articles if 'stealth' in a.tags])
            fund_matches = len([a for a in articles if a.fund_slug])
            results["linkedin_jobs"] = LinkedInJobsResponse(
                queries_executed=len(STEALTH_JOB_QUERIES),
                stealth_signals_found=stealth_count,
                fund_matches=fund_matches,
                total_jobs=len(articles),
            )
        except Exception:
            results["linkedin_jobs"] = None
    else:
        results["linkedin_jobs"] = None

    # Tech Funding News (always runs - free RSS)
    try:
        from .harvester.scrapers.tech_funding_news import TechFundingNewsScraper
        async with TechFundingNewsScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)
        fund_matches = len([a for a in articles if a.fund_slug])
        results["tech_funding_news"] = TechFundingNewsResponse(
            feeds_scraped=5,
            articles_found=len(articles),
            funding_articles=len(articles),
            fund_matches=fund_matches,
        )
    except Exception:
        results["tech_funding_news"] = None

    # Ventureburn (always runs - free RSS)
    try:
        from .harvester.scrapers.ventureburn import VentureburnScraper
        async with VentureburnScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)
        fund_matches = len([a for a in articles if a.fund_slug])
        results["ventureburn"] = VentureburnResponse(
            articles_found=len(articles),
            funding_articles=len(articles),
            fund_matches=fund_matches,
        )
    except Exception:
        results["ventureburn"] = None

    # ----- Tier 1 Stealth Detection -----

    # Portfolio Diff (always runs - free, scrapes public portfolio pages)
    try:
        from .harvester.scrapers.portfolio_diff import PortfolioDiffScraper
        async with PortfolioDiffScraper() as scraper:
            articles = await scraper.scrape_all()
        results["portfolio_diff"] = PortfolioDiffResponse(
            funds_checked=18,
            new_companies_found=len(articles),
            total_articles=len(articles),
        )
    except Exception:
        results["portfolio_diff"] = None

    # USPTO Trademarks (if Brave Search configured)
    if settings.brave_search_key:
        try:
            from .harvester.scrapers.uspto_trademarks import USPTOTrademarkScraper
            async with USPTOTrademarkScraper() as scraper:
                trademarks = await scraper.search_recent_trademarks(days_back=days)
                articles = await scraper.scrape_all(days_back=days, tech_only=True)
            results["uspto_trademarks"] = USPTOTrademarkResponse(
                trademarks_found=len(trademarks),
                tech_trademarks=len(articles),
                total_articles=len(articles),
            )
        except Exception:
            results["uspto_trademarks"] = None
    else:
        results["uspto_trademarks"] = None

    # Delaware Corps (if Brave Search configured)
    if settings.brave_search_key:
        try:
            from .harvester.scrapers.delaware_corps import DelawareCorpsScraper
            async with DelawareCorpsScraper() as scraper:
                articles = await scraper.scrape_all(days_back=days, min_score=3)
                brave_entities = await scraper.search_recent_incorporations(days_back=days)
                agg_entities = await scraper.search_aggregators()
            all_entities = len(brave_entities) + len(agg_entities)
            tech_entities = len([e for e in brave_entities if e.has_tech_name] +
                              [e for e in agg_entities if e.has_tech_name])
            high_signal = len([e for e in brave_entities if e.has_startup_agent] +
                             [e for e in agg_entities if e.has_startup_agent])
            results["delaware_corps"] = DelawareCorpsResponse(
                entities_found=all_entities,
                tech_entities=tech_entities,
                high_signal_entities=high_signal,
                total_articles=len(articles),
            )
        except Exception:
            results["delaware_corps"] = None
    else:
        results["delaware_corps"] = None

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


# ----- Company Enrichment -----

class EnrichmentResult(BaseModel):
    company_name: str
    website: Optional[str] = None
    ceo_name: Optional[str] = None
    ceo_linkedin: Optional[str] = None


class EnrichmentResponse(BaseModel):
    companies_enriched: int
    companies_updated: int
    results: List[EnrichmentResult]


@app.post("/enrichment/company", response_model=EnrichmentResult)
async def enrich_single_company(
    company_name: str = Query(..., description="Company name to enrich"),
    api_key: str = Depends(verify_api_key),
):
    """
    Enrich a single company with website and CEO LinkedIn.

    Uses Brave Search to find:
    - Official company website
    - CEO/Founder LinkedIn profile
    """
    from .enrichment import enrich_company

    result = await enrich_company(company_name)

    return EnrichmentResult(
        company_name=result.company_name,
        website=result.website,
        ceo_name=result.ceo_name,
        ceo_linkedin=result.ceo_linkedin,
    )


@app.post("/enrichment/deals", response_model=EnrichmentResponse)
async def enrich_deals(
    limit: int = Query(10, ge=1, le=50, description="Max deals to enrich"),
    skip_enriched: bool = Query(True, description="Skip deals that already have data"),
    force_update: bool = Query(False, description="Overwrite existing data with new enrichment"),
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Enrich deals with missing website/LinkedIn data.

    Uses deal context (investor, founders, category) for accurate enrichment.
    Finds deals missing company_website or founder LinkedIn,
    then uses Brave Search to populate the data.

    Use force_update=true to overwrite existing (potentially wrong) data.
    """
    from .enrichment import enrich_company_with_context, DealContext
    import json as json_module

    # Find deals missing enrichment data
    query = (
        select(Deal)
        .options(
            selectinload(Deal.company),
            selectinload(Deal.investors),
            selectinload(Deal.articles),
        )
        .join(Deal.company)
    )

    if skip_enriched:
        # Only get deals where company website is missing
        query = query.where(
            (PortfolioCompany.website.is_(None)) | (PortfolioCompany.website == "")
        )

    query = query.order_by(Deal.created_at.desc()).limit(limit)

    result = await db.execute(query)
    deals = result.scalars().all()

    results = []
    updated_count = 0

    for deal in deals:
        company_name = deal.company.name if deal.company else None
        if not company_name:
            continue

        try:
            # Build context from deal data
            founders = []
            if deal.founders_json:
                try:
                    founders = json_module.loads(deal.founders_json)
                except (json_module.JSONDecodeError, TypeError, ValueError):
                    founders = []

            # Extract lead investor from deal.investors relationship
            lead_investor = None
            for inv in deal.investors:
                if inv.is_lead:
                    lead_investor = inv.investor_name
                    break

            # Extract source URL from deal.articles relationship
            source_url = deal.articles[0].url if deal.articles else None

            context = DealContext(
                company_name=company_name,
                lead_investor=lead_investor,
                founders=founders,
                source_url=source_url,
                enterprise_category=deal.enterprise_category,
            )

            # Use context-aware enrichment
            enrichment = await enrich_company_with_context(context)

            # Update company in database
            if deal.company:
                if enrichment.website and (not deal.company.website or force_update):
                    deal.company.website = enrichment.website
                    updated_count += 1

                # Update founders_json with LinkedIn URLs
                if enrichment.founder_linkedins or enrichment.ceo_linkedin:
                    # Update existing founders with LinkedIn URLs
                    for f in founders:
                        founder_name = f.get("name", "")
                        if founder_name in enrichment.founder_linkedins:
                            if not f.get("linkedin_url") or force_update:
                                f["linkedin_url"] = enrichment.founder_linkedins[founder_name]

                    # Fallback: if no founder matched, add CEO
                    if enrichment.ceo_linkedin:
                        ceo_found = any(f.get("linkedin_url") for f in founders)
                        if not ceo_found:
                            for f in founders:
                                if f.get("title", "").lower() in ("ceo", "founder", "co-founder"):
                                    if not f.get("linkedin_url") or force_update:
                                        f["linkedin_url"] = enrichment.ceo_linkedin
                                    break

                    deal.founders_json = json_module.dumps(founders)

            results.append(EnrichmentResult(
                company_name=company_name,
                website=enrichment.website,
                ceo_name=enrichment.ceo_name,
                ceo_linkedin=enrichment.ceo_linkedin,
            ))

            # Rate limit
            await asyncio.sleep(0.5)

        except Exception as e:
            print(f"Error enriching {company_name}: {e}")
            results.append(EnrichmentResult(company_name=company_name))

    await db.commit()

    return EnrichmentResponse(
        companies_enriched=len(results),
        companies_updated=updated_count,
        results=results,
    )


# ----- Date Backfill & Cleanup -----

class DateBackfillResponse(BaseModel):
    deals_processed: int
    deals_updated: int
    deals_skipped_no_article: int
    deals_skipped_already_has_date: int


@app.post("/enrichment/backfill-dates", response_model=DateBackfillResponse)
async def backfill_deal_dates(
    limit: int = Query(100, ge=1, le=500, description="Max deals to process"),
    api_key: str = Depends(verify_api_key),
):
    """
    Backfill missing announced_date for deals.

    Uses article.published_date as fallback for deals missing announced_date.
    This improves date coverage from ~50-60% to ~95%+.
    """
    from datetime import date

    processed = 0
    updated = 0
    skipped_no_article = 0
    skipped_has_date = 0

    try:
        async with get_session() as session:
            # Find deals missing announced_date
            stmt = (
                select(Deal)
                .options(selectinload(Deal.articles))
                .where(Deal.announced_date.is_(None))
                .order_by(Deal.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            deals = result.scalars().all()

            for deal in deals:
                processed += 1

                # Skip if deal already has a date (shouldn't happen with our query)
                if deal.announced_date:
                    skipped_has_date += 1
                    continue

                # Try to get date from linked article
                if deal.articles:
                    article = deal.articles[0]
                    if article.published_date:
                        deal.announced_date = article.published_date
                        updated += 1
                        continue

                # No article or no published_date
                skipped_no_article += 1

            await session.commit()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return DateBackfillResponse(
        deals_processed=processed,
        deals_updated=updated,
        deals_skipped_no_article=skipped_no_article,
        deals_skipped_already_has_date=skipped_has_date,
    )


class CleanupOldDealsResponse(BaseModel):
    deals_deleted: int
    cutoff_date: str


@app.post("/deals/cleanup-old", response_model=CleanupOldDealsResponse)
async def cleanup_old_deals(
    cutoff_date: str = Query("2023-12-01", description="Delete deals before this date (YYYY-MM-DD)"),
    include_undated: bool = Query(True, description="Also delete deals without dates that were created before cutoff"),
    api_key: str = Depends(verify_api_key),
):
    """
    Delete deals older than the cutoff date.

    Cleans up old data to focus on recent deals (past year).
    Use cutoff_date=2023-12-01 to keep only deals from the past year.
    Also deletes related articles and deal_investors.
    """
    from datetime import datetime as dt
    from sqlalchemy import delete

    try:
        cutoff = dt.strptime(cutoff_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    deleted_count = 0

    try:
        async with get_session() as session:
            # First, find deal IDs to delete
            stmt_find = select(Deal.id).where(Deal.announced_date < cutoff)
            result_find = await session.execute(stmt_find)
            deal_ids_dated = [row[0] for row in result_find.fetchall()]

            deal_ids_undated = []
            if include_undated:
                stmt_find2 = select(Deal.id).where(
                    Deal.announced_date.is_(None),
                    Deal.created_at < cutoff
                )
                result_find2 = await session.execute(stmt_find2)
                deal_ids_undated = [row[0] for row in result_find2.fetchall()]

            all_deal_ids = deal_ids_dated + deal_ids_undated

            if all_deal_ids:
                # Delete related articles first
                stmt_articles = delete(Article).where(Article.deal_id.in_(all_deal_ids))
                await session.execute(stmt_articles)

                # Delete related deal_investors
                stmt_investors = delete(DealInvestor).where(DealInvestor.deal_id.in_(all_deal_ids))
                await session.execute(stmt_investors)

                # Now delete the deals
                stmt_deals = delete(Deal).where(Deal.id.in_(all_deal_ids))
                result_deals = await session.execute(stmt_deals)
                deleted_count = result_deals.rowcount

            await session.commit()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    cache.invalidate("deals")

    return CleanupOldDealsResponse(
        deals_deleted=deleted_count,
        cutoff_date=cutoff_date,
    )


class LinkedInEnrichmentResponse(BaseModel):
    deals_processed: int
    founders_enriched: int
    linkedin_urls_found: int


@app.post("/enrichment/linkedin", response_model=LinkedInEnrichmentResponse)
async def enrich_founder_linkedin(
    limit: int = Query(50, ge=1, le=200, description="Max deals to process"),
    api_key: str = Depends(verify_api_key),
):
    """
    Enrich founders with LinkedIn URLs and persist to database.

    Finds deals with founders missing LinkedIn URLs,
    searches Brave for their profiles, and saves results.
    """
    from .enrichment.brave_enrichment import enrich_deal_founders_linkedin
    import json as json_module

    processed = 0
    founders_enriched = 0
    linkedin_found = 0

    try:
        async with get_session() as session:
            # Find deals with founders but missing LinkedIn URLs
            stmt = (
                select(Deal)
                .options(selectinload(Deal.company))
                .where(Deal.founders_json.isnot(None))
                .order_by(Deal.created_at.desc())
                .limit(limit * 2)  # Get more to filter
            )
            result = await session.execute(stmt)
            deals = result.scalars().all()

            for deal in deals:
                if processed >= limit:
                    break

                # Parse founders
                try:
                    founders = json_module.loads(deal.founders_json)
                except (json_module.JSONDecodeError, TypeError):
                    continue

                # Skip if all founders already have LinkedIn
                missing_linkedin = [f for f in founders if not f.get("linkedin_url")]
                if not missing_linkedin:
                    continue

                processed += 1
                company_name = deal.company.name if deal.company else "Unknown"

                # Enrich and persist
                updated_founders = await enrich_deal_founders_linkedin(
                    deal_id=deal.id,
                    company_name=company_name,
                    founders=founders,
                    persist=True,
                )

                # Count enriched
                for orig, updated in zip(founders, updated_founders):
                    if not orig.get("linkedin_url") and updated.get("linkedin_url"):
                        founders_enriched += 1
                        linkedin_found += 1

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return LinkedInEnrichmentResponse(
        deals_processed=processed,
        founders_enriched=founders_enriched,
        linkedin_urls_found=linkedin_found,
    )


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

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
from sqlalchemy import select, func
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
from .harvester import (
    scrape_fund,
    scrape_all_funds,
    get_implemented_scrapers,
    get_unimplemented_scrapers,
)
from .scheduler import setup_scheduler, shutdown_scheduler
from .scheduler import jobs as scheduler_module


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173"],
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


class DealResponse(BaseModel):
    id: str
    startup_name: str
    investor_roles: List[str]
    investment_stage: str
    amount_invested: str
    date: str
    next_steps: Optional[str] = None


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
    is_lead: Optional[bool] = Query(None, description="Filter by lead status"),
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """
    List tracked deals with pagination.

    OPTIMIZED:
    - Proper pagination support
    - No sample data fallback (fail properly)
    - Efficient query with selectinload
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
                )
                .order_by(Deal.created_at.desc())
                .offset(offset)
                .limit(limit)
            )

            result = await session.execute(stmt)
            deals = result.scalars().all()

            # Build responses
            responses = []
            for deal in deals:
                # Build investor roles list
                investor_roles = []
                for inv in deal.investors:
                    if inv.is_lead:
                        investor_roles.append("lead")
                    elif inv.is_tracked_fund:
                        investor_roles.append("non_lead")
                    else:
                        investor_roles.append("non_lead")

                # Handle stealth (no investors tracked)
                if not investor_roles:
                    investor_roles = ["stealth"]

                responses.append(DealResponse(
                    id=str(deal.id),
                    startup_name=deal.company.name if deal.company else "Unknown",
                    investor_roles=investor_roles,
                    investment_stage=deal.round_type,
                    amount_invested=deal.amount or "Undisclosed",
                    date=deal.announced_date.isoformat() if deal.announced_date else "",
                    next_steps=None,
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

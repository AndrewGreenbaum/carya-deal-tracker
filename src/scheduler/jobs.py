"""
APScheduler job definitions for automated scraping.

Runs scraping every 4 hours of all 18 VC funds with:
- Staggered execution to avoid rate limits
- Error isolation per fund
- Webhook notifications on completion
- Metrics logging
- Automatic enrichment of new deals
- External sources (Brave Search, SEC EDGAR, etc.)
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Set
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..harvester import scrape_all_funds, get_implemented_scrapers
from ..harvester.base_scraper import NormalizedArticle
from ..config.settings import settings
from ..config.funds import FUND_REGISTRY
from .notifications import send_scrape_summary

logger = logging.getLogger(__name__)


# =============================================================================
# GLOBAL URL DEDUPLICATION (Phase 5)
# =============================================================================

# Global set to track URLs seen across ALL sources within a single run
# Cleared at the start of each scheduled job
_global_seen_urls: Set[str] = set()
_global_urls_lock = asyncio.Lock()

# Tracking parameters to strip from URLs for normalization
TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'fbclid', 'gclid', 'gclsrc', 'dclid', 'msclkid',
    'ref', 'source', 'mc_cid', 'mc_eid',
    '_ga', '_gl', '_hsenc', '_hsmi',
    'ncid', 'sr_share', 'vero_id',
}


def normalize_url(url: str) -> str:
    """
    Normalize URL for deduplication by stripping tracking parameters.

    Examples:
        "https://example.com/article?utm_source=twitter&id=123"
        → "https://example.com/article?id=123"

    This catches more duplicates from different sources that link to
    the same article with different tracking params.
    """
    if not url:
        return url

    try:
        parsed = urlparse(url)

        # Parse query params and filter out tracking ones
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        filtered_params = {
            k: v for k, v in query_params.items()
            if k.lower() not in TRACKING_PARAMS
        }

        # Rebuild query string (sorted for consistency)
        new_query = urlencode(filtered_params, doseq=True) if filtered_params else ""

        # Rebuild URL without tracking params
        normalized = urlunparse((
            parsed.scheme,
            parsed.netloc.lower(),  # Lowercase domain
            parsed.path.rstrip('/'),  # Remove trailing slash
            parsed.params,
            new_query,
            '',  # Remove fragment
        ))

        return normalized
    except Exception:
        # If parsing fails, return original URL
        return url


async def check_global_url_seen(url: str) -> bool:
    """
    Check if URL has been seen in this run (cross-source dedup).

    Returns True if URL was already seen (should skip).
    Adds URL to global set if not seen.
    """
    normalized = normalize_url(url)

    async with _global_urls_lock:
        if normalized in _global_seen_urls:
            return True
        _global_seen_urls.add(normalized)
        return False


def clear_global_seen_urls() -> None:
    """Clear global URL set. Called at start of each scheduled run."""
    global _global_seen_urls
    size = len(_global_seen_urls)
    _global_seen_urls.clear()
    if size > 0:
        logger.info(f"Global URL dedup cache cleared ({size} entries)")

# =============================================================================
# TOKEN OPTIMIZATION CONSTANTS
# =============================================================================

MAX_ARTICLES_PER_SOURCE = 15  # Reduced from 25 to save ~340K tokens/run


def is_non_announcement_title(title: str) -> bool:
    """
    Check if article title is clearly NOT a funding announcement.

    Used as pre-filter to skip non-announcements BEFORE Claude call.
    Saves ~$3.75/month in token costs.

    Returns True if article should be SKIPPED.
    """
    if not title:
        return False  # Don't skip articles with no title

    title_lower = title.lower()

    # Skip patterns - these are almost never funding announcements
    skip_patterns = [
        # Year-end content
        'year in review', 'year-in-review', 'yearinreview',
        '2024 recap', '2025 recap', 'annual review', 'year end',
        'looking back at', 'best of 2024', 'best of 2025',
        # Portfolio updates without funding
        'portfolio update', 'portfolio spotlight', 'portfolio news',
        'company update:', 'quarterly update',
        # Interviews and Q&A (unless about funding)
        'interview with', 'q&a with', 'q&a:', 'in conversation with',
        'fireside chat', 'podcast:', 'episode:',
        # Events and conferences
        'demo day recap', 'conference recap', 'event recap',
        'webinar:', 'live event:', 'summit recap',
        # Opinion and analysis (not announcements)
        'opinion:', 'analysis:', 'predictions for', 'trends in',
        'what we learned', 'lessons from', 'how to',
        # Job postings and hiring (not funding)
        'we\'re hiring', 'join our team', 'career opportunities',
        'job opening', 'now hiring',
        # Exits and acquisitions (different from funding)
        'acquired by', 'acquisition of', 'merger with', 'ipo:',
    ]

    for pattern in skip_patterns:
        if pattern in title_lower:
            return True

    return False


def is_likely_funding_from_title(title: str) -> bool:
    """
    Check if article title suggests funding news.

    Used as pre-filter to skip non-funding articles BEFORE Claude call.
    Requires strong signal OR 2+ weak signals to reduce false positives.
    """
    if not title:
        return True  # Don't filter out articles with no title

    title_lower = title.lower()

    # Strong signals (1 is enough)
    strong_signals = [
        'series a', 'series b', 'series c', 'series d', 'seed round',
        'raises $', 'raised $', 'led by', 'funding round', 'pre-seed'
    ]
    if any(signal in title_lower for signal in strong_signals):
        return True

    # Weak signals (need 2+)
    weak_signals = [
        'funding', 'investment', 'venture', 'capital',
        'million', 'billion', 'backed', 'round', 'investor'
    ]
    return sum(1 for signal in weak_signals if signal in title_lower) >= 2


async def enrich_new_deals(limit: int = 25):
    """
    Enrich newly scraped deals with website and CEO LinkedIn.

    Uses deal context (investor, founders, category) for accurate enrichment.
    Runs after each scrape job to populate company links.

    FIX: Uses per-deal transactions to prevent data loss if one enrichment fails.
    """
    try:
        from ..enrichment import enrich_company_with_context, DealContext
        from ..archivist import get_session, Deal, PortfolioCompany
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        import json

        # First, fetch all deals needing enrichment (read-only query)
        async with get_session() as db:
            query = (
                select(Deal)
                .options(
                    selectinload(Deal.company),
                    selectinload(Deal.investors),
                )
                .join(Deal.company)
                .where(
                    (PortfolioCompany.website.is_(None)) | (PortfolioCompany.website == "")
                )
                .order_by(Deal.created_at.desc())
                .limit(limit)
            )

            result = await db.execute(query)
            deals = result.scalars().all()

            if not deals:
                logger.info("No deals needing enrichment")
                return 0

            # Build list of (deal_id, company_id, company_name, context) for processing
            deals_to_enrich = []
            for deal in deals:
                company_name = deal.company.name if deal.company else None
                if not company_name or company_name.startswith("No "):
                    continue
                if company_name.lower() in ("unknown", "n/a", "<unknown>"):
                    continue

                founders = []
                if deal.founders_json:
                    try:
                        founders = json.loads(deal.founders_json)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        founders = []

                lead_investor = None
                for inv in deal.investors:
                    if inv.is_lead:
                        lead_investor = inv.investor_name
                        break

                context = DealContext(
                    company_name=company_name,
                    lead_investor=lead_investor,
                    founders=founders,
                    source_url=None,
                    enterprise_category=deal.enterprise_category,
                )

                deals_to_enrich.append({
                    "deal_id": deal.id,
                    "company_id": deal.company.id if deal.company else None,
                    "company_name": company_name,
                    "context": context,
                    "founders": founders,
                })

        # FIX: Process each deal in its own transaction (prevents batch rollback)
        enriched_count = 0
        for deal_info in deals_to_enrich:
            try:
                try:
                    enrichment = await asyncio.wait_for(
                        enrich_company_with_context(deal_info["context"]),
                        timeout=15.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Enrichment timeout for {deal_info['company_name']}")
                    continue

                if not enrichment.website:
                    continue

                # Update in separate transaction (per-deal isolation)
                async with get_session() as db:
                    # Fetch fresh deal and company objects
                    deal = await db.get(Deal, deal_info["deal_id"])
                    if deal and deal.company:
                        deal.company.website = enrichment.website

                        # Update founders with LinkedIn URLs
                        founders = deal_info["founders"]
                        if enrichment.founder_linkedins or enrichment.ceo_linkedin:
                            for f in founders:
                                founder_name = f.get("name", "")
                                if founder_name in enrichment.founder_linkedins:
                                    if not f.get("linkedin_url"):
                                        f["linkedin_url"] = enrichment.founder_linkedins[founder_name]

                            if enrichment.ceo_linkedin:
                                ceo_found = any(f.get("linkedin_url") for f in founders)
                                if not ceo_found:
                                    for f in founders:
                                        if f.get("title", "").lower() in ("ceo", "founder", "co-founder"):
                                            if not f.get("linkedin_url"):
                                                f["linkedin_url"] = enrichment.ceo_linkedin
                                            break

                            deal.founders_json = json.dumps(founders)

                        # Commit with timeout to prevent hanging
                        try:
                            await asyncio.wait_for(db.commit(), timeout=30.0)
                            enriched_count += 1
                        except asyncio.TimeoutError:
                            logger.error(f"Database commit timeout for {deal_info['company_name']}")
                            await db.rollback()

                await asyncio.sleep(0.5)  # Rate limit

            except Exception as e:
                logger.warning(
                    f"Error enriching {deal_info['company_name']} (deal_id={deal_info['deal_id']}): {e}",
                    exc_info=True,
                )
                # Continue to next deal - don't let one failure stop others

        logger.info(f"Enriched {enriched_count} companies with website/LinkedIn data")
        return enriched_count

    except Exception as e:
        logger.error("Enrichment job failed: %s", e, exc_info=True)
        return 0

# Global scheduler instance
scheduler: Optional[AsyncIOScheduler] = None

# Job execution tracking (for observability)
_last_job_run: Optional[datetime] = None
_last_job_status: Optional[str] = None
_last_job_error: Optional[str] = None
_last_job_duration: Optional[float] = None


async def process_external_articles(
    articles: List[NormalizedArticle],
    source_name: str,
) -> Dict[str, int]:
    """
    Process articles from external sources through extraction pipeline.

    This is the CRITICAL fix: external sources (Brave Search, SEC, etc.)
    were returning articles but never extracting deals from them.

    Args:
        articles: List of NormalizedArticle from external source
        source_name: Name of source for logging

    Returns:
        Dict with processing stats
    """
    from ..analyst.extractor import extract_deal, set_extraction_context, clear_extraction_context
    from ..archivist.storage import save_deal
    from ..archivist.database import get_session
    from ..archivist.models import Article
    from sqlalchemy import select

    # Set extraction context for token logging
    scan_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    set_extraction_context(source_name, scan_id)

    stats = {
        "articles_received": len(articles),
        "articles_skipped_duplicate": 0,
        "articles_skipped_cross_source": 0,  # Phase 5: Cross-source dedup
        "articles_skipped_title": 0,  # Title pre-filter
        "deals_extracted": 0,
        "deals_saved": 0,
        "errors": 0,
    }

    if not articles:
        return stats

    # OPTIMIZATION: Apply source limit
    if len(articles) > MAX_ARTICLES_PER_SOURCE:
        logger.info(f"[{source_name}] Limiting from {len(articles)} to {MAX_ARTICLES_PER_SOURCE} articles")
        articles = articles[:MAX_ARTICLES_PER_SOURCE]
        stats["articles_received"] = MAX_ARTICLES_PER_SOURCE

    logger.info(f"[{source_name}] Processing {len(articles)} articles through extraction pipeline")

    # Phase 5: Cross-source URL dedup (catches same article from different sources)
    # This runs BEFORE database check for efficiency
    cross_source_new = []
    for article in articles:
        if await check_global_url_seen(article.url):
            stats["articles_skipped_cross_source"] += 1
        else:
            cross_source_new.append(article)

    if stats["articles_skipped_cross_source"] > 0:
        logger.info(f"[{source_name}] Skipped {stats['articles_skipped_cross_source']} cross-source duplicates")

    articles = cross_source_new
    if not articles:
        logger.info(f"[{source_name}] All articles were cross-source duplicates")
        return stats

    # OPTIMIZATION: Batch check for duplicate URLs in single query (avoids N sessions)
    article_urls = [a.url for a in articles]
    existing_urls: set[str] = set()
    async with get_session() as session:
        # Also check normalized URLs to catch tracking param variants
        normalized_urls = [normalize_url(url) for url in article_urls]
        all_urls = set(article_urls) | set(normalized_urls)
        stmt = select(Article.url).where(Article.url.in_(all_urls))
        result = await session.execute(stmt)
        existing_urls = {row[0] for row in result.fetchall()}

    # Filter out duplicates (check both original and normalized)
    new_articles = []
    for a in articles:
        if a.url not in existing_urls and normalize_url(a.url) not in existing_urls:
            new_articles.append(a)
    stats["articles_skipped_duplicate"] = len(articles) - len(new_articles)

    if not new_articles:
        logger.info(f"[{source_name}] All {len(articles)} articles already processed")
        return stats

    # OPTIMIZATION: Title pre-filters - skip articles BEFORE Claude call
    # Filter 1: Skip non-announcement titles (year-in-review, interviews, etc.)
    # Filter 2: Skip titles without funding signals
    filtered_articles = []
    skipped_non_announcement = 0
    for article in new_articles:
        # First check: Is this clearly NOT an announcement?
        if is_non_announcement_title(article.title):
            skipped_non_announcement += 1
            continue
        # Second check: Does it have funding signals?
        if is_likely_funding_from_title(article.title):
            filtered_articles.append(article)
        else:
            stats["articles_skipped_title"] += 1

    if skipped_non_announcement > 0:
        logger.info(f"[{source_name}] Skipped {skipped_non_announcement} non-announcement articles (year-in-review, interviews, etc.)")
    if stats["articles_skipped_title"] > 0:
        logger.info(f"[{source_name}] Skipped {stats['articles_skipped_title']} articles without funding signals")

    new_articles = filtered_articles

    if not new_articles:
        logger.info(f"[{source_name}] No articles passed title filter")
        return stats

    logger.info(f"[{source_name}] {len(new_articles)} new articles to process (skipped {stats['articles_skipped_duplicate']} duplicates, {stats['articles_skipped_title']} by title)")

    # OPTIMIZATION: Concurrent processing with semaphore (vs serial 0.5s per article)
    MAX_CONCURRENT = 5
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results_lock = asyncio.Lock()
    # FIX: Collect alerts to send OUTSIDE semaphore (was blocking 15s per alert)
    pending_alerts = []
    alerts_lock = asyncio.Lock()

    async def process_article(article):
        async with semaphore:
            try:
                # Get fund config for context
                fund_config = FUND_REGISTRY.get(article.fund_slug) if article.fund_slug else None

                # Extract deal using Claude
                extraction = await extract_deal(
                    article_text=article.text,
                    source_url=article.url,
                    fund_context=fund_config,
                )

                # Handle None returns
                if extraction is None:
                    logger.debug(f"[{source_name}] Extraction returned None for {article.url}")
                    return None

                # Skip low-confidence extractions
                if extraction.confidence_score < settings.extraction_confidence_threshold:
                    return None

                # Save to database
                alert_info = None
                async with get_session() as session:
                    deal, alert_info = await save_deal(
                        session=session,
                        extraction=extraction,
                        article_url=article.url,
                        article_title=article.title,
                        article_text=article.text,
                        source_fund_slug=article.fund_slug,
                        article_published_date=article.published_date,
                    )

                # Update stats thread-safely
                async with results_lock:
                    if deal:
                        stats["deals_saved"] += 1
                        stats["deals_extracted"] += 1
                        logger.info(
                            f"[{source_name}] Saved deal: {extraction.startup_name} "
                            f"(fund: {article.fund_slug or 'unknown'})"
                        )
                    else:
                        logger.warning(
                            f"[{source_name}] save_deal returned None for {extraction.startup_name} "
                            f"(confidence={extraction.confidence_score:.2f}, "
                            f"is_new={extraction.is_new_announcement})"
                        )

                # FIX: Queue alert for sending OUTSIDE semaphore scope
                # This prevents 15s alert timeouts from blocking concurrent processing
                if alert_info:
                    async with alerts_lock:
                        pending_alerts.append(alert_info)

                return extraction

            except Exception as e:
                async with results_lock:
                    stats["errors"] += 1
                logger.error(
                    f"[{source_name}] FAILED to save deal from {article.url}: {e}",
                    exc_info=True
                )
                return None

    # Process all articles concurrently (limited by semaphore)
    results = await asyncio.gather(*[process_article(a) for a in new_articles], return_exceptions=True)

    # Check for unhandled exceptions in results
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"[{source_name}] Unhandled exception processing article {i}: {result}")

    # FIX: Send alerts AFTER all processing completes (outside semaphore)
    # This allows full concurrency during extraction while still sending alerts
    if pending_alerts:
        from .notifications import send_lead_deal_alert
        for alert_info in pending_alerts:
            try:
                await send_lead_deal_alert(
                    company_name=alert_info.company_name,
                    amount=alert_info.amount,
                    round_type=alert_info.round_type,
                    lead_investor=alert_info.lead_investor,
                    enterprise_category=alert_info.enterprise_category,
                    verification_snippet=alert_info.verification_snippet,
                )
            except Exception as alert_err:
                logger.error(
                    f"[{source_name}] Alert sending failed for {alert_info.company_name}: {alert_err}",
                    exc_info=True
                )

    logger.info(
        f"[{source_name}] Extraction complete: "
        f"received={stats['articles_received']}, "
        f"cross_source={stats['articles_skipped_cross_source']}, "
        f"db_duplicates={stats['articles_skipped_duplicate']}, "
        f"title_filtered={stats['articles_skipped_title']}, "
        f"extracted={stats['deals_extracted']}, "
        f"saved={stats['deals_saved']}, "
        f"errors={stats['errors']}"
    )

    # Clear extraction context when done
    clear_extraction_context()

    return stats


async def scrape_external_sources(days: int = 7) -> Dict[str, Any]:
    """
    Scrape all external data sources and process through extraction pipeline.

    This is the KEY function that was missing from the scheduled job.
    External sources find articles about deals, but they must be extracted
    by Claude to become actual deal records in the database.

    Sources scraped:
    1. Brave Search (with partner name queries for Benchmark, Khosla, etc.)
    2. SEC EDGAR (Form D filings)
    3. Google Alerts (RSS feeds)
    4. Twitter/X (API monitoring)
    5. TechCrunch RSS
    6. Fortune Term Sheet RSS
    7. LinkedIn Jobs (stealth detector)
    8. Crunchbase News RSS
    9. VentureBeat RSS
    10. Axios Pro Rata RSS
    11. StrictlyVC RSS
    12. Delaware Corps (new tech company formations)
    13. Y Combinator (Demo Day companies)
    14. GitHub Trending (dev tools before funding)
    15. Hacker News (launch posts)
    16. Tech Funding News (SaaS/AI/FinTech)
    17. Ventureburn (emerging markets/Africa)

    Args:
        days: Number of days to look back

    Returns:
        Dict with stats per source
    """
    results = {}
    total_deals_saved = 0

    # 1. BRAVE SEARCH (most important - includes partner name queries)
    if settings.brave_search_key:
        try:
            from ..harvester.scrapers.brave_search import BraveSearchScraper

            freshness = "pd" if days <= 1 else ("pw" if days <= 7 else "pm")
            async with BraveSearchScraper() as scraper:
                articles = await scraper.scrape_all(
                    freshness=freshness,
                    include_enterprise=True,
                    include_participation=True,
                    include_stealth=True,
                    include_partner_names=True,  # Critical for Benchmark, Khosla, First Round
                )

            stats = await process_external_articles(articles, "brave_search")
            results["brave_search"] = stats
            total_deals_saved += stats["deals_saved"]

        except Exception as e:
            logger.error("Brave Search scraping failed: %s", e)
            results["brave_search"] = {"error": str(e)}

    # 2. SEC EDGAR (free - Form D filings)
    try:
        from ..harvester.scrapers.sec_edgar import SECEdgarScraper

        async with SECEdgarScraper() as scraper:
            filings = await scraper.fetch_recent_filings(hours=days * 24)
            logger.info(f"SEC EDGAR: Processing {len(filings)} filings")

            # Convert filings to articles with full details
            articles = []
            for filing in filings:
                # CRITICAL FIX: Fetch full details (amount, industry, state, investors)
                # Without this, articles lack critical data for fund matching and stealth detection
                result = await scraper.fetch_filing_details(filing)
                if result is None:
                    continue  # Skip filings with missing required data
                filing = result
                # Note: Rate limiting (1s) is built into fetch_filing_details()

                matched_fund = scraper.match_tracked_fund(filing)
                article = await scraper.to_normalized_article(filing, matched_fund)
                articles.append(article)

            logger.info(f"SEC EDGAR: Created {len(articles)} articles with full details")

        stats = await process_external_articles(articles, "sec_edgar")
        results["sec_edgar"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("SEC EDGAR scraping failed: %s", e)
        results["sec_edgar"] = {"error": str(e)}

    # 3. GOOGLE ALERTS (free - RSS feeds)
    if settings.google_alerts_feeds:
        try:
            from ..harvester.scrapers.google_alerts import GoogleAlertsScraper

            feed_urls = [f.strip() for f in settings.google_alerts_feeds.split(',') if f.strip()]
            async with GoogleAlertsScraper(feed_urls) as scraper:
                articles = await scraper.scrape_all()

            stats = await process_external_articles(articles, "google_alerts")
            results["google_alerts"] = stats
            total_deals_saved += stats["deals_saved"]

        except Exception as e:
            logger.error("Google Alerts scraping failed: %s", e)
            results["google_alerts"] = {"error": str(e)}

    # 4. TWITTER (free tier)
    if settings.twitter_bearer_token:
        try:
            from ..harvester.scrapers.twitter_monitor import TwitterMonitor

            async with TwitterMonitor() as monitor:
                articles = await monitor.scrape_all(hours_back=days * 24)

            stats = await process_external_articles(articles, "twitter")
            results["twitter"] = stats
            total_deals_saved += stats["deals_saved"]

        except Exception as e:
            logger.error("Twitter scraping failed: %s", e)
            results["twitter"] = {"error": str(e)}

    # 5. TECHCRUNCH RSS (free)
    try:
        from ..harvester.scrapers.techcrunch_rss import TechCrunchScraper

        async with TechCrunchScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)

        stats = await process_external_articles(articles, "techcrunch")
        results["techcrunch"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("TechCrunch scraping failed: %s", e)
        results["techcrunch"] = {"error": str(e)}

    # 6. FORTUNE TERM SHEET (free)
    try:
        from ..harvester.scrapers.fortune_term_sheet import FortuneTermSheetScraper

        async with FortuneTermSheetScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)

        stats = await process_external_articles(articles, "fortune")
        results["fortune"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("Fortune Term Sheet scraping failed: %s", e)
        results["fortune"] = {"error": str(e)}

    # 7. LINKEDIN JOBS / STEALTH DETECTOR (uses Brave Search)
    if settings.brave_search_key:
        try:
            from ..harvester.scrapers.linkedin_jobs import LinkedInJobsScraper

            async with LinkedInJobsScraper() as scraper:
                articles = await scraper.scrape_all()

            stats = await process_external_articles(articles, "linkedin_jobs")
            results["linkedin_jobs"] = stats
            total_deals_saved += stats["deals_saved"]

        except Exception as e:
            logger.error("LinkedIn Jobs scraping failed: %s", e)
            results["linkedin_jobs"] = {"error": str(e)}

    # 7b. PORTFOLIO DIFF (stealth detection via portfolio page diffing - FREE)
    try:
        from ..harvester.scrapers.portfolio_diff import PortfolioDiffScraper
        from ..archivist.storage import save_stealth_detection

        async with PortfolioDiffScraper() as scraper:
            articles = await scraper.scrape_all()

        # FIX: Save stealth detections with per-article transactions
        # (prevents batch rollback if one article fails)
        stealth_saved = 0
        for article in articles:
            try:
                async with get_session() as session:
                    await save_stealth_detection(
                        session=session,
                        fund_slug=article.fund_slug,
                        detected_url=article.url,
                        company_name=article.title.split(":")[1].strip() if ":" in article.title else None,
                        notes=f"Source: Portfolio Diff | Tags: {', '.join(article.tags)}",
                    )
                stealth_saved += 1
            except Exception as e:
                logger.warning(f"Failed to save stealth detection for {article.url}: {e}")

        results["portfolio_diff"] = {
            "articles_found": len(articles),
            "stealth_saved": stealth_saved,
        }
        logger.info(f"Portfolio Diff: {len(articles)} new additions, {stealth_saved} saved")

    except Exception as e:
        logger.error("Portfolio Diff scraping failed: %s", e)
        results["portfolio_diff"] = {"error": str(e)}

    # 8. CRUNCHBASE NEWS (free RSS)
    try:
        from ..harvester.scrapers.crunchbase_news import CrunchbaseNewsScraper

        async with CrunchbaseNewsScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)

        stats = await process_external_articles(articles, "crunchbase_news")
        results["crunchbase_news"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("Crunchbase News scraping failed: %s", e)
        results["crunchbase_news"] = {"error": str(e)}

    # 9. VENTUREBEAT (free RSS)
    try:
        from ..harvester.scrapers.venturebeat import VentureBeatScraper

        async with VentureBeatScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)

        stats = await process_external_articles(articles, "venturebeat")
        results["venturebeat"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("VentureBeat scraping failed: %s", e)
        results["venturebeat"] = {"error": str(e)}

    # 10. AXIOS PRO RATA (free RSS - may return 0 if deprecated)
    try:
        from ..harvester.scrapers.axios_prorata import AxiosProRataScraper

        async with AxiosProRataScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)

        stats = await process_external_articles(articles, "axios_prorata")
        results["axios_prorata"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("Axios Pro Rata scraping failed: %s", e)
        results["axios_prorata"] = {"error": str(e)}

    # 11. STRICTLYVC (free RSS)
    try:
        from ..harvester.scrapers.strictlyvc import StrictlyVCScraper

        async with StrictlyVCScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)

        stats = await process_external_articles(articles, "strictlyvc")
        results["strictlyvc"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("StrictlyVC scraping failed: %s", e)
        results["strictlyvc"] = {"error": str(e)}

    # 12. PR WIRE RSS (free - PRNewswire, GlobeNewswire, BusinessWire press releases)
    try:
        from ..harvester.scrapers.prwire_rss import PRWireRSSScraper

        async with PRWireRSSScraper() as scraper:
            # Use fund_filter=True to only process articles mentioning tracked funds
            articles = await scraper.scrape_all_feeds(hours_back=days * 24, fund_filter=True)

        stats = await process_external_articles(articles, "prwire")
        results["prwire"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("PR Wire RSS scraping failed: %s", e)
        results["prwire"] = {"error": str(e)}

    # 13. DELAWARE CORPS (uses Brave Search - catches new tech company formations)
    if settings.brave_search_key:
        try:
            from ..harvester.scrapers.delaware_corps import DelawareCorpsScraper

            async with DelawareCorpsScraper() as scraper:
                articles = await scraper.scrape_all(days_back=days, min_score=3)

            stats = await process_external_articles(articles, "delaware_corps")
            results["delaware_corps"] = stats
            total_deals_saved += stats["deals_saved"]

        except Exception as e:
            logger.error("Delaware Corps scraping failed: %s", e)
            results["delaware_corps"] = {"error": str(e)}

    # 13. Y COMBINATOR (free - Demo Day companies)
    try:
        from ..harvester.scrapers.ycombinator import YCombinatorScraper

        async with YCombinatorScraper() as scraper:
            articles = await scraper.scrape_all(filter_enterprise=True)

        stats = await process_external_articles(articles, "ycombinator")
        results["ycombinator"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("Y Combinator scraping failed: %s", e)
        results["ycombinator"] = {"error": str(e)}

    # 14. GITHUB TRENDING (free - dev tools before funding)
    try:
        from ..harvester.scrapers.github_trending import GitHubTrendingScraper

        async with GitHubTrendingScraper() as scraper:
            repos = await scraper.fetch_all_trending(since="daily")
            articles = await scraper.scrape_all(since="daily", filter_enterprise=True, repos=repos)

        stats = await process_external_articles(articles, "github_trending")
        results["github_trending"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("GitHub Trending scraping failed: %s", e)
        results["github_trending"] = {"error": str(e)}

    # 15. HACKER NEWS (free - launch posts)
    try:
        from ..harvester.scrapers.hackernews import HackerNewsScraper

        async with HackerNewsScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)

        stats = await process_external_articles(articles, "hackernews")
        results["hackernews"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("Hacker News scraping failed: %s", e)
        results["hackernews"] = {"error": str(e)}

    # 16. TECH FUNDING NEWS (free RSS - SaaS/AI/FinTech)
    try:
        from ..harvester.scrapers.tech_funding_news import TechFundingNewsScraper

        async with TechFundingNewsScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)

        stats = await process_external_articles(articles, "tech_funding_news")
        results["tech_funding_news"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("Tech Funding News scraping failed: %s", e)
        results["tech_funding_news"] = {"error": str(e)}

    # 17. VENTUREBURN (free RSS - emerging markets/Africa)
    try:
        from ..harvester.scrapers.ventureburn import VentureburnScraper

        async with VentureburnScraper() as scraper:
            articles = await scraper.scrape_all(hours_back=days * 24)

        stats = await process_external_articles(articles, "ventureburn")
        results["ventureburn"] = stats
        total_deals_saved += stats["deals_saved"]

    except Exception as e:
        logger.error("Ventureburn scraping failed: %s", e)
        results["ventureburn"] = {"error": str(e)}

    results["total_deals_saved"] = total_deals_saved

    # Phase 5: Calculate scraper health metrics
    total_sources = 0
    successful_sources = 0
    failed_sources = []
    total_articles_received = 0
    total_cross_source_skipped = 0

    for source_name, stats in results.items():
        if source_name == "total_deals_saved":
            continue
        total_sources += 1
        if isinstance(stats, dict) and "error" in stats:
            failed_sources.append(source_name)
        else:
            successful_sources += 1
            if isinstance(stats, dict):
                total_articles_received += stats.get("articles_received", 0)
                total_cross_source_skipped += stats.get("articles_skipped_cross_source", 0)

    # Calculate health rate
    health_rate = (successful_sources / total_sources * 100) if total_sources > 0 else 0

    # Add health metrics to results
    results["_health_metrics"] = {
        "total_sources": total_sources,
        "successful_sources": successful_sources,
        "failed_sources": failed_sources,
        "health_rate_pct": round(health_rate, 1),
        "total_articles_received": total_articles_received,
        "cross_source_duplicates_caught": total_cross_source_skipped,
    }

    # Log health summary
    if failed_sources:
        logger.warning(
            f"External sources health: {successful_sources}/{total_sources} sources succeeded "
            f"({health_rate:.0f}%), FAILED: {', '.join(failed_sources)}"
        )
    else:
        logger.info(
            f"External sources health: {successful_sources}/{total_sources} sources succeeded (100%), "
            f"total_articles={total_articles_received}, cross_source_dedup={total_cross_source_skipped}"
        )

    logger.info(f"External sources complete: {total_deals_saved} total deals saved")

    return results


async def scheduled_scrape_job():
    """
    Main scheduled scraping job.

    Scrapes all 18 implemented funds with parallel execution,
    PLUS all external data sources (Brave Search, SEC, etc.).
    Runs every 4 hours.

    This is the FIXED version that includes external sources
    which contain partner name queries for funds like Benchmark,
    Khosla, and First Round that don't have useful website scrapers.
    """
    global _last_job_run, _last_job_status, _last_job_error, _last_job_duration

    job_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logger.info(f"[{job_id}] Starting scheduled scrape job")

    # Track job execution
    _last_job_run = datetime.now(timezone.utc)
    _last_job_status = "running"
    _last_job_error = None

    # CRITICAL FIX: Clear content hash cache at start of each run
    # Without this, duplicate detection prevents processing the same article
    # across different runs (100% data loss after first run)
    from ..analyst.extractor import clear_content_hash_cache
    clear_content_hash_cache()
    logger.info(f"[{job_id}] Content hash cache cleared for fresh run")

    # Phase 5: Clear global URL dedup cache for cross-source deduplication
    clear_global_seen_urls()
    logger.info(f"[{job_id}] Global URL dedup cache cleared for fresh run")

    start_time = datetime.now(timezone.utc)

    try:
        # ===== PHASE 1: Scrape fund websites =====
        fund_slugs = get_implemented_scrapers()
        logger.info(f"[{job_id}] Phase 1: Scraping {len(fund_slugs)} fund websites")

        # Run scrapers with parallel=True, max 3 concurrent
        results = await scrape_all_funds(
            fund_slugs=fund_slugs,
            parallel=True,
            max_parallel_funds=3
        )

        # Calculate Phase 1 stats
        fund_articles = sum(r.articles_found for r in results)
        fund_deals = sum(r.deals_saved for r in results)
        fund_errors = sum(len(r.errors) for r in results)

        logger.info(
            f"[{job_id}] Phase 1 complete: "
            f"articles={fund_articles}, deals={fund_deals}, errors={fund_errors}"
        )

        # ===== PHASE 2: Scrape external sources (THE FIX) =====
        logger.info(f"[{job_id}] Phase 2: Scraping external sources (Brave Search, SEC, etc.)")

        external_results = await scrape_external_sources(days=7)
        external_deals = external_results.get("total_deals_saved", 0)

        logger.info(
            f"[{job_id}] Phase 2 complete: "
            f"external_deals={external_deals}"
        )

        # ===== Calculate total stats =====
        total_deals = fund_deals + external_deals
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        logger.info(
            f"[{job_id}] Scrape complete: "
            f"fund_deals={fund_deals}, external_deals={external_deals}, "
            f"total_deals={total_deals}, duration={duration:.1f}s"
        )

        # ===== PHASE 3: Enrich new deals =====
        # Always run enrichment to catch up on any missing data
        logger.info(f"[{job_id}] Phase 3: Starting automatic enrichment (website + LinkedIn)")
        # Reduced from 200 to 50 to save Brave Search queries (150 fewer/run)
        enriched = await enrich_new_deals(limit=50)
        logger.info(f"[{job_id}] Enrichment complete: {enriched} companies updated")

        # Send notification
        await send_scrape_summary(
            job_id=job_id,
            results=results,
            duration_seconds=duration,
            external_results=external_results,
        )

        # Track job success
        _last_job_status = "success"
        _last_job_duration = duration
        logger.info(f"[{job_id}] Job completed successfully in {duration:.1f}s")

    except Exception as e:
        logger.error(f"[{job_id}] Scrape job failed: {e}", exc_info=True)
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        # Track job failure
        _last_job_status = "failed"
        _last_job_error = str(e)
        _last_job_duration = duration

        # Still try to send error notification
        await send_scrape_summary(
            job_id=job_id,
            results=[],
            duration_seconds=duration,
            error=str(e)
        )


def setup_scheduler() -> AsyncIOScheduler:
    """
    Initialize and start the APScheduler.

    Configures:
    - Scrape job every 4 hours (at 12am, 4am, 8am, 12pm, 4pm, 8pm EST)
    - Job store in memory (stateless)
    - Timezone: America/New_York (EST/EDT)
    """
    global scheduler

    scheduler = AsyncIOScheduler(
        timezone="America/New_York",
        job_defaults={
            "coalesce": False,  # Queue missed runs instead of dropping them
            "max_instances": 1,  # Only one instance at a time
            "misfire_grace_time": 600,  # 10 min grace for misfires (allows long runs)
        }
    )

    # Add scrape job - runs every 4 hours at :00 EST
    scheduler.add_job(
        scheduled_scrape_job,
        trigger=CronTrigger(hour='*/4', minute=0, timezone="America/New_York"),
        id="scheduled_scrape",
        name="VC Fund Scrape (4-hourly)",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started with 4-hourly scrape job")

    # List scheduled jobs
    for job in scheduler.get_jobs():
        logger.info(f"Scheduled job: {job.id} - next run: {job.next_run_time}")

    return scheduler


def shutdown_scheduler():
    """Gracefully shutdown the scheduler."""
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=True)
        logger.info("Scheduler shutdown complete")
        scheduler = None

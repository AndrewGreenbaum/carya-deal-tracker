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
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..harvester import scrape_all_funds, get_implemented_scrapers
from ..harvester.base_scraper import NormalizedArticle
from ..config.settings import settings
from ..config.funds import FUND_REGISTRY
from .notifications import send_scrape_summary

logger = logging.getLogger(__name__)

# =============================================================================
# TOKEN OPTIMIZATION CONSTANTS
# =============================================================================

MAX_ARTICLES_PER_SOURCE = 25  # Cap articles per source to reduce token usage


def is_likely_funding_from_title(title: str) -> bool:
    """
    Quick check if article title suggests funding news.

    Used as pre-filter to skip non-funding articles BEFORE Claude call.
    This saves 10-15% tokens by avoiding obviously irrelevant articles.

    Uses loose matching - any funding keyword passes.
    """
    if not title:
        return True  # Don't filter out articles with no title

    title_lower = title.lower()
    funding_signals = [
        'raises', 'raised', 'funding', 'series', 'seed', 'million', 'billion',
        'led by', 'backed', 'investment', 'round', 'capital', 'vc', 'venture',
        'announces', 'secures', 'closes', 'valuation', 'pre-seed'
    ]
    return any(signal in title_lower for signal in funding_signals)


async def enrich_new_deals(limit: int = 25):
    """
    Enrich newly scraped deals with website and CEO LinkedIn.

    Uses deal context (investor, founders, category) for accurate enrichment.
    Runs after each scrape job to populate company links.
    """
    try:
        from ..enrichment import enrich_company_with_context, DealContext
        from ..archivist import get_session, Deal, PortfolioCompany
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        import json

        async with get_session() as db:
            # Find deals missing company website
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

            enriched_count = 0
            for deal in deals:
                company_name = deal.company.name if deal.company else None
                if not company_name or company_name.startswith("No "):
                    continue

                # Skip invalid names
                if company_name.lower() in ("unknown", "n/a", "<unknown>"):
                    continue

                try:
                    # Build context from deal for accurate enrichment
                    founders = []
                    if deal.founders_json:
                        try:
                            founders = json.loads(deal.founders_json)
                        except (json.JSONDecodeError, TypeError, ValueError):
                            founders = []

                    # Get lead investor from deal_investors
                    lead_investor = None
                    for inv in deal.investors:
                        if inv.is_lead:
                            lead_investor = inv.investor_name
                            break

                    context = DealContext(
                        company_name=company_name,
                        lead_investor=lead_investor,
                        founders=founders,
                        source_url=None,  # Not stored in deal model
                        enterprise_category=deal.enterprise_category,
                    )

                    try:
                        enrichment = await asyncio.wait_for(
                            enrich_company_with_context(context),
                            timeout=15.0  # 15 second timeout per enrichment
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Enrichment timeout for {company_name}")
                        continue

                    if deal.company and enrichment.website:
                        deal.company.website = enrichment.website
                        enriched_count += 1

                    # Update founders with LinkedIn URLs from enrichment
                    if enrichment.founder_linkedins or enrichment.ceo_linkedin:
                        for f in founders:
                            founder_name = f.get("name", "")
                            if founder_name in enrichment.founder_linkedins:
                                if not f.get("linkedin_url"):
                                    f["linkedin_url"] = enrichment.founder_linkedins[founder_name]

                        # Fallback: if no founder matched, add CEO LinkedIn
                        if enrichment.ceo_linkedin:
                            ceo_found = any(f.get("linkedin_url") for f in founders)
                            if not ceo_found:
                                for f in founders:
                                    if f.get("title", "").lower() in ("ceo", "founder", "co-founder"):
                                        if not f.get("linkedin_url"):
                                            f["linkedin_url"] = enrichment.ceo_linkedin
                                        break

                        deal.founders_json = json.dumps(founders)

                    await asyncio.sleep(0.5)  # Rate limit

                except Exception as e:
                    # FIX #40: Include deal ID and stack trace for debugging
                    logger.warning(
                        f"Error enriching {company_name} (deal_id={deal.id}): {e}",
                        exc_info=True,
                    )

            await db.commit()
            logger.info(f"Enriched {enriched_count} companies with website/LinkedIn data")
            return enriched_count

    except Exception as e:
        logger.error("Enrichment job failed: %s", e, exc_info=True)
        return 0

# Global scheduler instance
scheduler: Optional[AsyncIOScheduler] = None


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
        "articles_skipped_title": 0,  # NEW: Title pre-filter
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

    # OPTIMIZATION: Batch check for duplicate URLs in single query (avoids N sessions)
    article_urls = [a.url for a in articles]
    existing_urls: set[str] = set()
    async with get_session() as session:
        stmt = select(Article.url).where(Article.url.in_(article_urls))
        result = await session.execute(stmt)
        existing_urls = {row[0] for row in result.fetchall()}

    # Filter out duplicates
    new_articles = [a for a in articles if a.url not in existing_urls]
    stats["articles_skipped_duplicate"] = len(articles) - len(new_articles)

    if not new_articles:
        logger.info(f"[{source_name}] All {len(articles)} articles already processed")
        return stats

    # OPTIMIZATION: Title pre-filter - skip articles with non-funding titles
    filtered_articles = []
    for article in new_articles:
        if is_likely_funding_from_title(article.title):
            filtered_articles.append(article)
        else:
            stats["articles_skipped_title"] += 1

    if stats["articles_skipped_title"] > 0:
        logger.info(f"[{source_name}] Skipped {stats['articles_skipped_title']} articles by title filter")

    new_articles = filtered_articles

    if not new_articles:
        logger.info(f"[{source_name}] No articles passed title filter")
        return stats

    logger.info(f"[{source_name}] {len(new_articles)} new articles to process (skipped {stats['articles_skipped_duplicate']} duplicates, {stats['articles_skipped_title']} by title)")

    # OPTIMIZATION: Concurrent processing with semaphore (vs serial 0.5s per article)
    MAX_CONCURRENT = 5
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results_lock = asyncio.Lock()

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

                # Send alert AFTER transaction commits
                if alert_info:
                    from .notifications import send_lead_deal_alert
                    await send_lead_deal_alert(
                        company_name=alert_info.company_name,
                        amount=alert_info.amount,
                        round_type=alert_info.round_type,
                        lead_investor=alert_info.lead_investor,
                        enterprise_category=alert_info.enterprise_category,
                        verification_snippet=alert_info.verification_snippet,
                    )

                return extraction

            except Exception as e:
                async with results_lock:
                    stats["errors"] += 1
                logger.warning(f"[{source_name}] Error processing article {article.url}: {e}")
                return None

    # Process all articles concurrently (limited by semaphore)
    results = await asyncio.gather(*[process_article(a) for a in new_articles], return_exceptions=True)

    # Check for unhandled exceptions in results
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"[{source_name}] Unhandled exception processing article {i}: {result}")
            async with results_lock:
                stats["errors"] += 1

    logger.info(
        f"[{source_name}] Extraction complete: "
        f"received={stats['articles_received']}, "
        f"duplicates={stats['articles_skipped_duplicate']}, "
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

        # Save stealth detections (portfolio diff doesn't go through deal extraction)
        stealth_saved = 0
        async with get_session() as session:
            for article in articles:
                try:
                    await save_stealth_detection(
                        session=session,
                        fund_slug=article.fund_slug,
                        detected_url=article.url,
                        company_name=article.title.split(":")[1].strip() if ":" in article.title else None,
                        notes=f"Source: Portfolio Diff | Tags: {', '.join(article.tags)}",
                    )
                    stealth_saved += 1
                except Exception as e:
                    logger.warning(f"Failed to save stealth detection: {e}")

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
    job_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logger.info(f"[{job_id}] Starting scheduled scrape job")

    # CRITICAL FIX: Clear content hash cache at start of each run
    # Without this, duplicate detection prevents processing the same article
    # across different runs (100% data loss after first run)
    from ..analyst.extractor import clear_content_hash_cache
    clear_content_hash_cache()
    logger.info(f"[{job_id}] Content hash cache cleared for fresh run")

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
        # Increased limit to 100 for aggressive coverage (was 50)
        enriched = await enrich_new_deals(limit=200)
        logger.info(f"[{job_id}] Enrichment complete: {enriched} companies updated")

        # Send notification
        await send_scrape_summary(
            job_id=job_id,
            results=results,
            duration_seconds=duration,
            external_results=external_results,
        )

    except Exception as e:
        logger.error(f"[{job_id}] Scrape job failed: {e}", exc_info=True)
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
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

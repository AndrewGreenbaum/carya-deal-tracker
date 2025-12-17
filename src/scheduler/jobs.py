"""
APScheduler job definitions for automated scraping.

Runs scraping every 4 hours of all 18 VC funds with:
- Staggered execution to avoid rate limits
- Error isolation per fund
- Webhook notifications on completion
- Metrics logging
- Automatic enrichment of new deals
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..harvester import scrape_all_funds, get_implemented_scrapers
from ..config.settings import settings
from .notifications import send_scrape_summary

logger = logging.getLogger(__name__)


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

                    enrichment = await enrich_company_with_context(context)

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
                    logger.warning(f"Error enriching {company_name}: {e}")

            await db.commit()
            logger.info(f"Enriched {enriched_count} companies with website/LinkedIn data")
            return enriched_count

    except Exception as e:
        logger.error(f"Enrichment job failed: {e}", exc_info=True)
        return 0

# Global scheduler instance
scheduler: Optional[AsyncIOScheduler] = None


async def scheduled_scrape_job():
    """
    Main scheduled scraping job.

    Scrapes all 18 implemented funds with parallel execution
    and sends summary notification. Runs every 4 hours.
    """
    job_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    logger.info(f"[{job_id}] Starting scheduled scrape job")

    start_time = datetime.utcnow()

    try:
        # Get all implemented scrapers
        fund_slugs = get_implemented_scrapers()
        logger.info(f"[{job_id}] Scraping {len(fund_slugs)} funds: {fund_slugs}")

        # Run scrapers with parallel=True, max 3 concurrent
        results = await scrape_all_funds(
            fund_slugs=fund_slugs,
            parallel=True,
            max_parallel_funds=3
        )

        # Calculate summary stats
        total_articles = sum(r.articles_found for r in results)
        total_deals = sum(r.deals_saved for r in results)
        total_errors = sum(len(r.errors) for r in results)
        duration = (datetime.utcnow() - start_time).total_seconds()

        logger.info(
            f"[{job_id}] Scrape complete: "
            f"articles={total_articles}, deals={total_deals}, "
            f"errors={total_errors}, duration={duration:.1f}s"
        )

        # Enrich new deals with website/LinkedIn data
        if total_deals > 0:
            logger.info(f"[{job_id}] Starting automatic enrichment for new deals")
            enriched = await enrich_new_deals(limit=min(total_deals + 10, 50))
            logger.info(f"[{job_id}] Enrichment complete: {enriched} companies updated")

        # Send notification
        await send_scrape_summary(
            job_id=job_id,
            results=results,
            duration_seconds=duration
        )

    except Exception as e:
        logger.error(f"[{job_id}] Scrape job failed: {e}", exc_info=True)
        duration = (datetime.utcnow() - start_time).total_seconds()
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
    - Scrape job every 4 hours (at 0:00, 4:00, 8:00, 12:00, 16:00, 20:00 UTC)
    - Job store in memory (stateless)
    - Timezone: UTC
    """
    global scheduler

    scheduler = AsyncIOScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,  # Combine missed runs
            "max_instances": 1,  # Only one instance at a time
            "misfire_grace_time": 300,  # 5 min grace for misfires
        }
    )

    # Add scrape job - runs every 4 hours at :00
    scheduler.add_job(
        scheduled_scrape_job,
        trigger=CronTrigger(hour='*/4', minute=0),  # Every 4 hours
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

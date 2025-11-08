"""
APScheduler job definitions for automated scraping.

Runs scraping every 4 hours of all 18 VC funds with:
- Staggered execution to avoid rate limits
- Error isolation per fund
- Webhook notifications on completion
- Metrics logging
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

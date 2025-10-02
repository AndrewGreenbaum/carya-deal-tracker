"""
Notification handlers for scrape job results.

Supports:
- Slack webhooks
- Discord webhooks
- Logging fallback
"""

import logging
from typing import List, Optional

import httpx

from ..config.settings import settings
from ..harvester.orchestrator import ScrapingResult

logger = logging.getLogger(__name__)


async def send_scrape_summary(
    job_id: str,
    results: List[ScrapingResult],
    duration_seconds: float,
    error: Optional[str] = None,
):
    """
    Send scrape summary to configured webhooks.

    Args:
        job_id: Unique identifier for this job run
        results: List of ScrapingResult from each fund
        duration_seconds: Total job duration
        error: Optional error message if job failed
    """
    # Build summary
    if error:
        summary = _build_error_message(job_id, error, duration_seconds)
    else:
        summary = _build_success_message(job_id, results, duration_seconds)

    # Log the summary
    logger.info(summary["text"])

    # Send to Slack
    if settings.slack_webhook_url:
        await _send_slack(summary)

    # Send to Discord
    if settings.discord_webhook_url:
        await _send_discord(summary)


def _build_success_message(
    job_id: str,
    results: List[ScrapingResult],
    duration: float,
) -> dict:
    """Build success notification message."""
    total_articles = sum(r.articles_found for r in results)
    total_deals = sum(r.deals_saved for r in results)
    total_errors = sum(len(r.errors) for r in results)
    funds_with_deals = [r.fund_slug for r in results if r.deals_saved > 0]

    status_emoji = ":white_check_mark:" if total_errors == 0 else ":warning:"

    text = f"""{status_emoji} *Bud Tracker Scrape Complete* [{job_id}]

*Summary:*
- Funds scraped: {len(results)}
- Articles found: {total_articles}
- New deals saved: {total_deals}
- Errors: {total_errors}
- Duration: {duration:.1f}s

*Funds with new deals:* {', '.join(funds_with_deals) or 'None'}"""

    return {
        "text": text.strip(),
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text.strip()}
            }
        ]
    }


def _build_error_message(job_id: str, error: str, duration: float) -> dict:
    """Build error notification message."""
    text = f""":x: *Bud Tracker Scrape FAILED* [{job_id}]

*Error:* {error}
*Duration before failure:* {duration:.1f}s

Please check logs for details."""

    return {"text": text.strip()}


async def _send_slack(message: dict):
    """Send message to Slack webhook."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.slack_webhook_url,
                json=message,
                timeout=10.0
            )
            response.raise_for_status()
            logger.info("Slack notification sent")
    except Exception as e:
        logger.error(f"Failed to send Slack notification: {e}")


async def _send_discord(message: dict):
    """Send message to Discord webhook."""
    try:
        # Discord uses 'content' instead of 'text'
        discord_payload = {"content": message["text"]}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.discord_webhook_url,
                json=discord_payload,
                timeout=10.0
            )
            response.raise_for_status()
            logger.info("Discord notification sent")
    except Exception as e:
        logger.error(f"Failed to send Discord notification: {e}")

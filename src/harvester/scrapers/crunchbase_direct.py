"""
Crunchbase Direct Ingestion - Bypass Claude for structured data.

Processes deals from Crunchbase Pro where data is already structured:
- Company name, amount, round type, date, investors
- No LLM extraction needed
- Direct save to database with deduplication

Usage:
    POST /scrapers/crunchbase-direct with JSON payload from local bot.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from ...analyst.schemas import (
    DealExtraction,
    RoundType,
    LeadStatus,
    InvestorMention,
    ChainOfThought,
    EnterpriseCategory,
)
from ...archivist.storage import save_deal, LeadDealAlertInfo
from ...archivist.database import get_session
from ...harvester.fund_matcher import match_fund_name

logger = logging.getLogger(__name__)


# ----- Input Schemas -----

class CrunchbaseDealInput(BaseModel):
    """Input schema for a single Crunchbase deal."""
    startup_name: str
    round_type: str
    amount: Optional[str] = None
    announced_date: Optional[str] = None  # ISO format: YYYY-MM-DD
    lead_investors: List[str] = Field(default_factory=list)
    participating_investors: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    source: str = "crunchbase_pro"


class CrunchbaseBatchInput(BaseModel):
    """Input schema for batch Crunchbase deals."""
    deals: List[CrunchbaseDealInput]


# ----- Result Schema -----

@dataclass
class CrunchbaseIngestionResult:
    """Result of processing Crunchbase deals."""
    deals_received: int
    deals_saved: int
    deals_duplicate: int
    deals_no_tracked_fund: int
    errors: List[str]


# ----- Helper Functions -----

def _parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse ISO date string to date object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        # Try alternate format from Crunchbase (e.g., "Dec 18, 2025")
        try:
            return datetime.strptime(date_str, "%b %d, %Y").date()
        except ValueError:
            logger.warning(f"Could not parse date: {date_str}")
            return None


def _map_round_type(round_str: str) -> RoundType:
    """Map string to RoundType enum."""
    mapping = {
        "pre_seed": RoundType.PRE_SEED,
        "pre-seed": RoundType.PRE_SEED,
        "seed": RoundType.SEED,
        "series_a": RoundType.SERIES_A,
        "series a": RoundType.SERIES_A,
        "series_b": RoundType.SERIES_B,
        "series b": RoundType.SERIES_B,
        "series_c": RoundType.SERIES_C,
        "series c": RoundType.SERIES_C,
        "series_d": RoundType.SERIES_D,
        "series d": RoundType.SERIES_D,
        "series_e": RoundType.SERIES_E_PLUS,
        "series e": RoundType.SERIES_E_PLUS,
        "series_e_plus": RoundType.SERIES_E_PLUS,
        "series f": RoundType.SERIES_E_PLUS,
        "series g": RoundType.SERIES_E_PLUS,
        "series h": RoundType.SERIES_E_PLUS,
        "growth": RoundType.GROWTH,
        "debt": RoundType.DEBT,
        "unknown": RoundType.UNKNOWN,
    }
    return mapping.get(round_str.lower().strip(), RoundType.UNKNOWN)


def _create_investor_mention(name: str, is_lead: bool) -> InvestorMention:
    """Create InvestorMention from investor name."""
    fund_slug = match_fund_name(name)
    return InvestorMention(
        name=name,
        role=LeadStatus.CONFIRMED_LEAD if is_lead else LeadStatus.PARTICIPANT,
        partner_name=None,
        is_tracked_fund=fund_slug is not None,
    )


def _find_tracked_fund_lead(lead_investors: List[InvestorMention]) -> Tuple[bool, Optional[str]]:
    """
    Check if any tracked fund is a lead investor.

    Returns:
        (is_tracked_lead, fund_name)
    """
    for inv in lead_investors:
        if inv.is_tracked_fund:
            return True, inv.name
    return False, None


# ----- Main Conversion Function -----

def crunchbase_deal_to_extraction(deal: CrunchbaseDealInput) -> DealExtraction:
    """
    Convert CrunchbaseDealInput to DealExtraction.

    Since data is already structured, we create the extraction directly
    without LLM processing. All Crunchbase deals are assumed to be
    new announcements (from their funding database).
    """
    # Create investor mentions
    lead_investors = [
        _create_investor_mention(inv, is_lead=True)
        for inv in deal.lead_investors
    ]
    participating_investors = [
        _create_investor_mention(inv, is_lead=False)
        for inv in deal.participating_investors
    ]

    # Check if any tracked fund is a lead
    tracked_fund_is_lead, tracked_fund_name = _find_tracked_fund_lead(lead_investors)

    return DealExtraction(
        startup_name=deal.startup_name,
        startup_description=None,
        company_website=None,
        company_linkedin=None,
        founders=[],
        round_label=_map_round_type(deal.round_type),
        amount=deal.amount,
        valuation=None,
        round_date=_parse_date(deal.announced_date),
        lead_investors=lead_investors,
        participating_investors=participating_investors,
        tracked_fund_is_lead=tracked_fund_is_lead,
        tracked_fund_name=tracked_fund_name,
        tracked_fund_role=LeadStatus.CONFIRMED_LEAD if tracked_fund_is_lead else None,
        tracked_fund_partner=None,
        reasoning=ChainOfThought(
            final_reasoning="Structured data from Crunchbase Pro (no LLM extraction needed)"
        ),
        # Conservative AI classification - will be enriched later
        enterprise_category=EnterpriseCategory.NOT_AI,
        is_enterprise_ai=False,
        is_ai_deal=False,
        # Verification
        verification_snippet=f"Lead investors: {', '.join(deal.lead_investors)}" if deal.lead_investors else None,
        lead_evidence_weak=False,  # Crunchbase data is reliable
        amount_needs_review=False,
        amount_review_reason=None,
        # High confidence for structured data
        confidence_score=0.95,
        # Crunchbase only shows actual funding announcements
        is_new_announcement=True,
        announcement_evidence="From Crunchbase Pro funding database",
        announcement_rejection_reason=None,
    )


# ----- Main Processing Function -----

async def process_crunchbase_deals(
    deals: List[CrunchbaseDealInput],
    scan_job_id: Optional[int] = None,
) -> CrunchbaseIngestionResult:
    """
    Process a batch of Crunchbase deals.

    Filters for deals where tracked funds are lead investors,
    then saves to database with deduplication.

    Args:
        deals: List of deals from Crunchbase bot
        scan_job_id: Optional scan job ID for tracking

    Returns:
        CrunchbaseIngestionResult with counts and errors
    """
    result = CrunchbaseIngestionResult(
        deals_received=len(deals),
        deals_saved=0,
        deals_duplicate=0,
        deals_no_tracked_fund=0,
        errors=[],
    )

    for deal_input in deals:
        try:
            # Convert to DealExtraction
            extraction = crunchbase_deal_to_extraction(deal_input)

            # Skip if no tracked fund is lead
            if not extraction.tracked_fund_is_lead:
                result.deals_no_tracked_fund += 1
                logger.debug(f"Skipping {deal_input.startup_name} - no tracked fund lead")
                continue

            # Save to database
            async with get_session() as session:
                saved_deal, alert_info = await save_deal(
                    session=session,
                    extraction=extraction,
                    article_url=deal_input.source_url or f"crunchbase://{deal_input.startup_name}",
                    article_title=f"{deal_input.startup_name} {deal_input.round_type}",
                    article_text=None,
                    source_fund_slug="crunchbase_pro",
                    article_published_date=_parse_date(deal_input.announced_date),
                    scan_job_id=scan_job_id,
                    # Crunchbase amounts are from official filings - high quality
                    amount_source="crunchbase",
                )

                if saved_deal is None:
                    result.deals_duplicate += 1
                    logger.debug(f"Duplicate: {deal_input.startup_name}")
                else:
                    result.deals_saved += 1
                    logger.info(
                        f"Saved Crunchbase deal: {deal_input.startup_name} - "
                        f"{deal_input.round_type} - Lead: {extraction.tracked_fund_name}"
                    )

                    # Send alert if this is a lead deal
                    if alert_info:
                        try:
                            from ...scheduler.notifications import send_lead_deal_alert
                            await send_lead_deal_alert(
                                company_name=alert_info.company_name,
                                amount=alert_info.amount,
                                round_type=alert_info.round_type,
                                lead_investor=alert_info.lead_investor,
                                enterprise_category=alert_info.enterprise_category,
                                verification_snippet=alert_info.verification_snippet,
                            )
                        except Exception as alert_err:
                            logger.warning(f"Failed to send alert: {alert_err}")

        except Exception as e:
            error_msg = f"Error processing {deal_input.startup_name}: {e}"
            result.errors.append(error_msg)
            logger.error(error_msg, exc_info=True)

    logger.info(
        f"Crunchbase ingestion complete: {result.deals_saved} saved, "
        f"{result.deals_duplicate} duplicate, {result.deals_no_tracked_fund} no tracked fund"
    )
    return result

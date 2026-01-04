"""
Storage pipeline for persisting extracted deals to the database.
"""

import json
import re
import logging
from datetime import datetime, date, timedelta
from typing import Optional
from sqlalchemy import select, nullslast
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from .models import (
    Fund,
    PortfolioCompany,
    Deal,
    DealInvestor,
    Article,
    StealthDetection,
)
from .database import get_session
from ..analyst.schemas import DealExtraction, LeadStatus, EnterpriseCategory
from ..config.funds import FUND_REGISTRY
# NOTE: match_fund_name is imported lazily inside save_deal() to avoid circular import


def normalize_amount(amount_str: Optional[str]) -> Optional[int]:
    """
    Normalize amount string to integer (in dollars).

    Examples:
        "$30M" → 30000000
        "$30 million" → 30000000
        "$2.5B" → 2500000000
        "$500K" → 500000
        None → None
    """
    if not amount_str:
        return None

    # Remove $ and whitespace
    clean = amount_str.replace("$", "").replace(",", "").strip().lower()

    # Extract number and multiplier
    match = re.match(r'([\d.]+)\s*(million|m|billion|b|thousand|k)?', clean)
    if not match:
        return None

    num = float(match.group(1))
    multiplier = match.group(2) or ""

    if multiplier in ("million", "m"):
        return int(num * 1_000_000)
    elif multiplier in ("billion", "b"):
        return int(num * 1_000_000_000)
    elif multiplier in ("thousand", "k"):
        return int(num * 1_000)
    else:
        return int(num)


def normalize_company_name(name: str) -> str:
    """
    Normalize company name for fuzzy matching.

    Examples:
        "Valerie Health" → "valeriehealth"
        "Valerie" → "valerie"
        "The AI Company, Inc." → "theaicompany"
    """
    # Lowercase
    name = name.lower()
    # Remove common suffixes
    for suffix in [", inc.", ", inc", " inc.", " inc", ", llc", " llc",
                   ", ltd", " ltd", " corp", ", corp"]:
        name = name.replace(suffix, "")
    # Remove non-alphanumeric
    name = re.sub(r'[^a-z0-9]', '', name)
    return name


async def get_or_create_company(
    session: AsyncSession,
    name: str,
    description: Optional[str] = None,
    sector: Optional[str] = None,
    website: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> PortfolioCompany:
    """Get existing company or create new one."""
    stmt = select(PortfolioCompany).where(PortfolioCompany.name == name)
    result = await session.execute(stmt)
    company = result.scalar_one_or_none()

    if company:
        # Update existing company with new info if we have it
        if website and not company.website:
            company.website = website
        if linkedin_url and not company.linkedin_url:
            company.linkedin_url = linkedin_url
        if description and not company.description:
            company.description = description
        company.updated_at = datetime.utcnow()
        return company

    company = PortfolioCompany(
        name=name,
        description=description,
        sector=sector,
        website=website,
        linkedin_url=linkedin_url,
    )
    session.add(company)
    await session.flush()
    return company


async def get_or_create_fund(session: AsyncSession, slug: str) -> Optional[Fund]:
    """Get fund from database or create from registry."""
    if slug not in FUND_REGISTRY:
        return None

    stmt = select(Fund).where(Fund.slug == slug)
    result = await session.execute(stmt)
    fund = result.scalar_one_or_none()

    if fund:
        return fund

    fund_config = FUND_REGISTRY[slug]
    fund = Fund(
        slug=slug,
        name=fund_config.name,
        website=fund_config.ingestion_url,
    )
    session.add(fund)
    await session.flush()
    return fund


async def find_duplicate_deal(
    session: AsyncSession,
    company_name: str,
    round_type: str,
    amount: Optional[str],
    announced_date: Optional[date],
) -> Optional[Deal]:
    """
    Check if a similar deal already exists.

    Match criteria:
    - Company name starts with same normalized prefix (min 6 chars)
    - Same round type
    - Same normalized amount (within 10% tolerance)
    - Date within 14 days

    Returns existing Deal if found, None otherwise.
    """
    # Normalize inputs
    normalized_name = normalize_company_name(company_name)
    normalized_amount = normalize_amount(amount)

    # Query recent deals with same round type
    stmt = select(Deal).join(PortfolioCompany).where(
        Deal.round_type == round_type
    )

    # Date filter: within 14 days if we have a date
    if announced_date:
        date_start = announced_date - timedelta(days=14)
        date_end = announced_date + timedelta(days=14)
        stmt = stmt.where(
            Deal.announced_date.between(date_start, date_end)
        )

    result = await session.execute(stmt)
    candidates = result.scalars().all()

    for deal in candidates:
        # Get company name
        company_stmt = select(PortfolioCompany).where(
            PortfolioCompany.id == deal.company_id
        )
        company_result = await session.execute(company_stmt)
        company = company_result.scalar_one_or_none()
        if not company:
            continue

        existing_normalized = normalize_company_name(company.name)

        # Fuzzy name match: one must be prefix of the other (min 6 chars)
        if len(normalized_name) >= 6 and len(existing_normalized) >= 6:
            if not (normalized_name.startswith(existing_normalized[:6]) or
                    existing_normalized.startswith(normalized_name[:6])):
                continue
        elif normalized_name != existing_normalized:
            continue

        # Amount match: within 10% tolerance
        if normalized_amount and deal.amount:
            existing_amount = normalize_amount(deal.amount)
            if existing_amount:
                tolerance = max(normalized_amount, existing_amount) * 0.1
                if abs(normalized_amount - existing_amount) > tolerance:
                    continue

        # Found a match!
        logger.info(
            f"Found duplicate deal: '{company_name}' matches existing '{company.name}' "
            f"(deal #{deal.id}, {deal.round_type}, {deal.amount})"
        )
        return deal

    return None


async def save_deal(
    session: AsyncSession,
    extraction: DealExtraction,
    article_url: str,
    article_title: str,
    article_text: Optional[str] = None,
    source_fund_slug: Optional[str] = None,
    article_published_date: Optional[date] = None,
) -> Optional[Deal]:
    """
    Save an extracted deal to the database.

    Creates:
    - PortfolioCompany (if not exists)
    - Deal record
    - DealInvestor records for all investors
    - Article record linked to the deal

    Returns None if deal is a duplicate (article is still linked to existing deal).
    """
    # Lazy import to avoid circular dependency
    # (storage.py -> harvester -> orchestrator.py -> storage.py)
    from ..harvester.fund_matcher import match_fund_name

    # Determine announced_date for duplicate check
    announced_date = extraction.round_date
    if announced_date is None and article_published_date:
        announced_date = article_published_date

    # Check for duplicate deal BEFORE creating new records
    existing_deal = await find_duplicate_deal(
        session,
        company_name=extraction.startup_name,
        round_type=extraction.round_label.value,
        amount=extraction.amount,
        announced_date=announced_date,
    )

    if existing_deal:
        logger.info(
            f"Skipping duplicate deal: {extraction.startup_name} "
            f"{extraction.round_label.value} - matches deal #{existing_deal.id}"
        )
        # Still save the article linked to existing deal
        article = Article(
            deal_id=existing_deal.id,
            url=article_url,
            title=article_title,
            source_fund_slug=source_fund_slug,
            extracted_text=article_text,
            is_processed=True,
        )
        session.add(article)
        await session.flush()
        return None  # Signal that this was a duplicate

    # Get or create company with website and LinkedIn
    company = await get_or_create_company(
        session,
        name=extraction.startup_name,
        description=extraction.startup_description,
        website=extraction.company_website,
        linkedin_url=extraction.company_linkedin,
    )

    # Convert founders to JSON for storage
    founders_json = None
    if extraction.founders:
        founders_data = [
            {
                "name": f.name,
                "title": f.title,
                "linkedin_url": f.linkedin_url,
            }
            for f in extraction.founders
        ]
        founders_json = json.dumps(founders_data)

    # announced_date was already determined above for duplicate check

    # Create deal with enterprise AI classification
    deal = Deal(
        company_id=company.id,
        round_type=extraction.round_label.value,
        amount=extraction.amount,
        valuation=extraction.valuation,
        announced_date=announced_date,
        is_lead_confirmed=extraction.tracked_fund_is_lead,
        lead_partner_name=extraction.tracked_fund_partner,
        verification_snippet=extraction.verification_snippet,
        enterprise_category=extraction.enterprise_category.value if extraction.enterprise_category else None,
        is_enterprise_ai=extraction.is_enterprise_ai,
        founders_json=founders_json,
        confidence_score=extraction.confidence_score,
        thesis_drift_score=extraction.thesis_drift_score,
    )
    session.add(deal)
    await session.flush()

    # Add lead investors
    for investor in extraction.lead_investors:
        fund = None
        # Use centralized fund matcher (handles variants + negative keywords)
        fund_slug = match_fund_name(investor.name)
        if fund_slug:
            fund = await get_or_create_fund(session, fund_slug)

        deal_investor = DealInvestor(
            deal_id=deal.id,
            fund_id=fund.id if fund else None,
            investor_name=investor.name,
            is_lead=True,
            is_tracked_fund=fund is not None,  # Use actual match result
            partner_name=investor.partner_name,
            role=investor.role.value,
        )
        session.add(deal_investor)

    # Add participating investors
    for investor in extraction.participating_investors:
        fund = None
        # Use centralized fund matcher (handles variants + negative keywords)
        fund_slug = match_fund_name(investor.name)
        if fund_slug:
            fund = await get_or_create_fund(session, fund_slug)

        deal_investor = DealInvestor(
            deal_id=deal.id,
            fund_id=fund.id if fund else None,
            investor_name=investor.name,
            is_lead=False,
            is_tracked_fund=fund is not None,  # Use actual match result
            partner_name=investor.partner_name,
            role=investor.role.value,
        )
        session.add(deal_investor)

    # Create article record
    article = Article(
        deal_id=deal.id,
        url=article_url,
        title=article_title,
        source_fund_slug=source_fund_slug,
        extracted_text=article_text,
        is_processed=True,
    )
    session.add(article)

    await session.flush()

    # Send Slack/Discord alert for lead deals
    if extraction.tracked_fund_is_lead:
        # Lazy import to avoid circular dependency
        from ..scheduler.notifications import send_lead_deal_alert

        # Find the lead investor name from extraction
        lead_investor_name = "Unknown"
        for inv in extraction.lead_investors:
            if inv.is_tracked_fund:
                lead_investor_name = inv.name
                break

        await send_lead_deal_alert(
            company_name=extraction.startup_name,
            amount=extraction.amount,
            round_type=extraction.round_label.value,
            lead_investor=lead_investor_name,
            enterprise_category=extraction.enterprise_category.value if extraction.enterprise_category else None,
            verification_snippet=extraction.verification_snippet,
        )

    return deal


async def get_deals(
    session: AsyncSession,
    fund_slug: Optional[str] = None,
    stage: Optional[str] = None,
    is_lead: Optional[bool] = None,
    is_enterprise_ai: Optional[bool] = None,
    enterprise_category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Deal]:
    """
    Fetch deals from database with optional filters.

    Args:
        fund_slug: Filter by specific fund
        stage: Filter by round type (seed, series_a, etc.)
        is_lead: Filter by lead status (True = tracked fund led)
        is_enterprise_ai: Filter for Enterprise AI only (True = B2B AI)
        enterprise_category: Filter by category (infrastructure, security, etc.)
        limit: Max results
        offset: Pagination offset
    """
    stmt = select(Deal).order_by(nullslast(Deal.announced_date.desc()), Deal.created_at.desc())

    if stage:
        stmt = stmt.where(Deal.round_type == stage)

    if is_lead is not None:
        stmt = stmt.where(Deal.is_lead_confirmed == is_lead)

    if is_enterprise_ai is not None:
        stmt = stmt.where(Deal.is_enterprise_ai == is_enterprise_ai)

    if enterprise_category:
        stmt = stmt.where(Deal.enterprise_category == enterprise_category)

    if fund_slug:
        # Join to deal_investors to filter by fund
        stmt = stmt.join(DealInvestor).join(Fund).where(Fund.slug == fund_slug)

    stmt = stmt.offset(offset).limit(limit)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_deal_with_details(session: AsyncSession, deal_id: int) -> Optional[Deal]:
    """Get a deal with all related data."""
    stmt = select(Deal).where(Deal.id == deal_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def seed_funds(session: AsyncSession) -> None:
    """Seed the database with all 18 tracked funds."""
    for slug, config in FUND_REGISTRY.items():
        await get_or_create_fund(session, slug)
    await session.commit()


async def save_stealth_detection(
    session: AsyncSession,
    fund_slug: str,
    detected_url: str,
    company_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> StealthDetection:
    """
    Save a stealth detection to the database.

    Stealth detections track portfolio additions found via:
    - Portfolio page diffing
    - LinkedIn job scraping
    - SEC EDGAR Form D filings
    - Delaware corporate filings

    Args:
        session: Database session
        fund_slug: The fund that backed the company
        detected_url: URL where the stealth signal was found
        company_name: Name of the company (if known)
        notes: Additional context about the detection

    Returns:
        The created StealthDetection record
    """
    # Check for existing detection with same URL to avoid duplicates
    stmt = select(StealthDetection).where(StealthDetection.detected_url == detected_url)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing detection if we have new info
        if company_name and not existing.company_name:
            existing.company_name = company_name
        if notes and not existing.notes:
            existing.notes = notes
        return existing

    detection = StealthDetection(
        fund_slug=fund_slug,
        detected_url=detected_url,
        company_name=company_name,
        notes=notes,
    )
    session.add(detection)
    await session.flush()
    return detection


async def get_stealth_detections(
    session: AsyncSession,
    fund_slug: Optional[str] = None,
    is_confirmed: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[StealthDetection]:
    """
    Fetch stealth detections with optional filters.

    Args:
        session: Database session
        fund_slug: Filter by specific fund
        is_confirmed: Filter by confirmation status
        limit: Max results
        offset: Pagination offset

    Returns:
        List of StealthDetection records
    """
    stmt = select(StealthDetection).order_by(StealthDetection.detected_at.desc())

    if fund_slug:
        stmt = stmt.where(StealthDetection.fund_slug == fund_slug)

    if is_confirmed is not None:
        stmt = stmt.where(StealthDetection.is_confirmed == is_confirmed)

    stmt = stmt.offset(offset).limit(limit)

    result = await session.execute(stmt)
    return list(result.scalars().all())

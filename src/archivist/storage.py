"""
Storage pipeline for persisting extracted deals to the database.
"""

import json
import re
import logging
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy import select, nullslast
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# FIX #20: Alert info returned from save_deal for post-commit sending
@dataclass
class LeadDealAlertInfo:
    """Info needed to send a lead deal alert after transaction commits."""
    company_name: str
    amount: Optional[str]
    round_type: str
    lead_investor: str
    enterprise_category: Optional[str]
    verification_snippet: Optional[str]

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
# NOTE: fund_matcher import moved inside save_deal() to avoid circular import


def normalize_amount(amount_str: Optional[str]) -> Optional[int]:
    """
    Normalize amount string to integer (in USD equivalent).

    Examples:
        "$30M" → 30000000
        "$30 million" → 30000000
        "$30mn" → 30000000
        "$2.5B" → 2500000000
        "$500K" → 500000
        "$25-30 million" → 25000000 (takes first number)
        "€100 million" → 100000000 (treats as equivalent USD)
        "₹97 crore" → 11640000 (converts to USD equivalent)
        "INR 100 Cr" → 12000000 (converts to USD equivalent)
        None → None
    """
    if not amount_str:
        return None

    # Remove currency symbols and whitespace, lowercase
    clean = amount_str.replace("$", "").replace("€", "").replace("£", "").replace("₹", "")
    clean = clean.replace(",", "").strip().lower()

    # Remove currency prefixes
    clean = re.sub(r'^(usd|eur|gbp|inr)\s*', '', clean)

    # Handle "undisclosed" or similar
    if "undisclosed" in clean or "unknown" in clean or "<unknown>" in clean:
        return None

    # Handle ranges like "25-30 million" - take the first number
    # Also handles "25 to 30 million"
    clean = re.sub(r'(\d+)\s*[-–—to]+\s*\d+', r'\1', clean)

    # Extract number and multiplier
    # Added "mn" as alternative for million (common in some sources)
    # Added "cr" and "crore" for Indian currency (1 crore ≈ $120K USD)
    match = re.match(r'([\d.]+)\s*(million|mn|mm|m|billion|bn|b|thousand|k|crore|cr|lakh|lac)?', clean)
    if not match:
        return None

    try:
        num = float(match.group(1))
    except ValueError:
        return None

    multiplier = match.group(2) or ""

    if multiplier in ("million", "m", "mn", "mm"):
        return int(num * 1_000_000)
    elif multiplier in ("billion", "b", "bn"):
        return int(num * 1_000_000_000)
    elif multiplier in ("thousand", "k"):
        return int(num * 1_000)
    elif multiplier in ("crore", "cr"):
        # 1 crore = 10 million INR ≈ $120K USD (rough conversion)
        return int(num * 120_000)
    elif multiplier in ("lakh", "lac"):
        # 1 lakh = 100K INR ≈ $1.2K USD
        return int(num * 1_200)
    else:
        # If no multiplier and number is small, assume it might be in millions
        # e.g., "raised 30" often means $30M
        if num < 1000:
            return int(num)  # Return raw number, don't assume
        return int(num)


def normalize_company_name(name: str) -> str:
    """
    Normalize company name for fuzzy matching.

    Examples:
        "Valerie Health" → "valeriehealth"
        "Valerie" → "valerie"
        "The AI Company, Inc." → "aicompany"
        "OpenAI Technologies" → "openai"
        "Anthropic, Inc." → "anthropic"
    """
    # Lowercase first
    name = name.lower().strip()

    # Remove "the" prefix (common in company names)
    if name.startswith("the "):
        name = name[4:]

    # Remove common suffixes (order matters - longer first)
    suffixes = [
        ", incorporated", " incorporated",
        ", technologies", " technologies",
        ", corporation", " corporation",
        ", limited", " limited",
        ", company", " company",
        ", inc.", " inc.",
        ", inc", " inc",
        ", llc", " llc",
        ", ltd.", " ltd.",
        ", ltd", " ltd",
        ", corp.", " corp.",
        ", corp", " corp",
        ", co.", " co.",
        ", co", " co",
        " labs", " lab",
        " tech",
        " ai",  # Be careful: only remove if it's a suffix
    ]

    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break  # Only remove one suffix

    # Remove non-alphanumeric characters
    name = re.sub(r'[^a-z0-9]', '', name)

    return name


def company_names_match(name1: str, name2: str) -> bool:
    """
    Check if two company names likely refer to the same company.

    Uses normalized comparison with strict suffix matching.
    Errs on the side of NOT matching (false negatives are better than false positives).

    Examples:
        ("Valerie Health", "Valerie") → True  (health is a known suffix)
        ("Ramp", "Ramp AI") → True  (ai is a known suffix)
        ("OpenAI", "OpenAPI") → False  (different names)
        ("Glean", "Glean AI") → True  (ai is a known suffix)
        ("Amazon", "Amazonia") → False  (ia is not a known suffix)
    """
    norm1 = normalize_company_name(name1)
    norm2 = normalize_company_name(name2)

    # Exact match after normalization
    if norm1 == norm2:
        return True

    # Empty check
    if not norm1 or not norm2:
        return False

    # One must be a prefix of the other (for short names)
    # This handles "Ramp" vs "Ramp AI" → "ramp" vs "rampai"
    shorter, longer = (norm1, norm2) if len(norm1) <= len(norm2) else (norm2, norm1)

    # Shorter name must be at least 3 chars to be a valid prefix
    if len(shorter) >= 3 and longer.startswith(shorter):
        # The suffix MUST be in our explicit list of known company suffixes
        # This is strict to avoid false positives like Amazon/Amazonia
        suffix = longer[len(shorter):]
        # Only allow known company name suffixes (NOT generic word endings)
        # Be strict - 'data' removed because Meta vs Metadata is a false positive
        known_company_suffixes = {
            'ai', 'hq', 'app', 'io', 'labs', 'lab', 'tech',
            'health', 'cloud', 'ml', 'ops', 'dev',
            'x', 'go', 'ly', 'fy', 'ify'  # Common startup suffixes
        }
        if suffix in known_company_suffixes:
            return True

    # NO fuzzy matching - too many false positives
    # If names don't match exactly or with known suffix, they're different companies
    return False


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
        company.updated_at = datetime.now(timezone.utc)
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

    Match criteria (TWO-TIER approach):

    TIER 1 - Strict Match (same company + same round + similar amount):
    - Company names match (using smart fuzzy matching)
    - Same round type
    - Same normalized amount (within 20% tolerance) OR one/both amounts missing
    - Date within 30 days OR deals from last 90 days if no date

    TIER 2 - Same-Day Match (catches same-day duplicates from multiple sources):
    - Company names match exactly (case-insensitive)
    - Same round type
    - Same announced date (exact match)
    - Any amount (news sources often report different numbers)

    Returns existing Deal if found, None otherwise.

    OPTIMIZED:
    - Single query with JOIN (no N+1 problem)
    - LIMIT clause to prevent scanning thousands of rows
    - Date fallback for deals without announced_date
    """
    from sqlalchemy import or_, func

    # Normalize inputs for comparison
    normalized_amount = normalize_amount(amount)
    normalized_company = normalize_company_name(company_name)

    # Build query with JOIN to get company name in one query (fixes N+1)
    stmt = (
        select(Deal, PortfolioCompany)
        .join(PortfolioCompany, Deal.company_id == PortfolioCompany.id)
        .where(Deal.round_type == round_type)
    )

    # Date filter with fallback for missing dates - EXPANDED to 30 days
    if announced_date:
        # Look within 30 days of the announced date (increased from 14)
        date_start = announced_date - timedelta(days=30)
        date_end = announced_date + timedelta(days=30)
        # Include deals with matching date OR deals with NULL date (recent ones)
        recent_cutoff = date.today() - timedelta(days=90)  # Increased from 60
        stmt = stmt.where(
            or_(
                Deal.announced_date.between(date_start, date_end),
                # Also check deals with NULL date created recently
                (Deal.announced_date.is_(None) & (Deal.created_at >= datetime.combine(recent_cutoff, datetime.min.time())))
            )
        )
    else:
        # No date provided: only look at recent deals (last 90 days)
        # This prevents matching against ancient deals
        recent_cutoff = date.today() - timedelta(days=90)
        stmt = stmt.where(
            or_(
                Deal.announced_date >= recent_cutoff,
                # Also include NULL dates created recently
                (Deal.announced_date.is_(None) & (Deal.created_at >= datetime.combine(recent_cutoff, datetime.min.time())))
            )
        )

    # LIMIT to prevent scanning too many rows
    stmt = stmt.limit(300)

    result = await session.execute(stmt)
    candidates = result.all()  # Returns list of (Deal, PortfolioCompany) tuples

    for deal, company in candidates:
        existing_normalized = normalize_company_name(company.name)

        # TIER 2: Exact same-day match (catches duplicates from multiple sources)
        if announced_date and deal.announced_date == announced_date:
            if normalized_company == existing_normalized:
                logger.info(
                    f"Found same-day duplicate: '{company_name}' matches '{company.name}' "
                    f"(deal #{deal.id}, {deal.round_type}, {deal.amount}, date={deal.announced_date})"
                )
                return deal

        # TIER 1: Fuzzy company name match with amount tolerance
        if not company_names_match(company_name, company.name):
            continue

        # Amount match: within 10% tolerance (reduced from 20% to prevent false positives)
        # 20% was too broad: $50M and $60M would match, causing separate deals to merge
        # If either amount is missing, we still consider it a potential match
        # (articles sometimes don't mention the amount)
        if normalized_amount and deal.amount:
            existing_amount = normalize_amount(deal.amount)
            if existing_amount:
                tolerance = max(normalized_amount, existing_amount) * 0.1  # 10% tolerance
                if abs(normalized_amount - existing_amount) > tolerance:
                    continue

        # Found a match!
        logger.info(
            f"Found duplicate deal: '{company_name}' matches existing '{company.name}' "
            f"(deal #{deal.id}, {deal.round_type}, {deal.amount})"
        )
        return deal

    return None


async def check_article_url_exists(session: AsyncSession, url: str) -> bool:
    """
    Check if an article with this URL already exists in the database.

    Used to prevent duplicate article records when linking to existing deals.

    FIX: Use EXISTS subquery instead of SELECT with LIMIT 1 for efficiency.
    EXISTS stops at first match without fetching row data.
    """
    from sqlalchemy import exists
    stmt = select(exists().where(Article.url == url))
    result = await session.execute(stmt)
    return result.scalar()


async def save_deal(
    session: AsyncSession,
    extraction: DealExtraction,
    article_url: str,
    article_title: str,
    article_text: Optional[str] = None,
    source_fund_slug: Optional[str] = None,
    article_published_date: Optional[date] = None,
) -> Tuple[Optional[Deal], Optional[LeadDealAlertInfo]]:
    """
    Save an extracted deal to the database.

    Creates:
    - PortfolioCompany (if not exists)
    - Deal record
    - DealInvestor records for all investors
    - Article record linked to the deal

    Returns:
        Tuple of (Deal, LeadDealAlertInfo) where:
        - Deal is None if duplicate (article still linked to existing deal)
        - LeadDealAlertInfo is provided if this is a lead deal that needs an alert
          (FIX #20: alert should be sent AFTER transaction commits)
    """
    # Lazy import to avoid circular import (storage -> harvester -> orchestrator -> storage)
    from ..harvester.fund_matcher import match_fund_name

    # Determine announced_date for duplicate check
    # FIX: Improved date validation to prevent wrong dates
    announced_date = extraction.round_date

    # Cross-validate: warn if LLM date differs significantly from article date
    if announced_date and article_published_date:
        diff_days = abs((announced_date - article_published_date).days)
        if diff_days > 14:  # More than 2 weeks difference
            logger.warning(
                f"Date mismatch for {extraction.startup_name}: "
                f"LLM extracted {announced_date}, article published {article_published_date}, "
                f"diff={diff_days} days - using LLM date but flagging for review"
            )

    # Fallback to article date only if:
    # 1. LLM didn't extract a date
    # 2. Article date is not in the future (prevents wrong indexing dates)
    if announced_date is None and article_published_date:
        today = date.today()
        if article_published_date <= today:
            announced_date = article_published_date
        else:
            logger.warning(
                f"Skipping future article date for {extraction.startup_name}: "
                f"{article_published_date} is after today ({today})"
            )

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

        # Cross-reference: Update to earlier date if this article has one
        # This ensures we use the earliest known announcement date
        if announced_date and existing_deal.announced_date:
            if announced_date < existing_deal.announced_date:
                logger.info(
                    f"Updating deal #{existing_deal.id} date: "
                    f"{existing_deal.announced_date} → {announced_date} (earlier source found)"
                )
                existing_deal.announced_date = announced_date
                existing_deal.updated_at = datetime.now(timezone.utc)
        elif announced_date and not existing_deal.announced_date:
            # Deal had no date, now we have one
            logger.info(
                f"Setting deal #{existing_deal.id} date: {announced_date} (previously null)"
            )
            existing_deal.announced_date = announced_date
            existing_deal.updated_at = datetime.now(timezone.utc)

        # Check if this article URL already exists (prevent duplicate articles)
        if not await check_article_url_exists(session, article_url):
            # Link article to existing deal
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
            logger.debug(f"Linked new article to existing deal #{existing_deal.id}")
        else:
            logger.debug(f"Article URL already exists, skipping: {article_url}")
        return None, None  # Signal that this was a duplicate

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

    # FIX #20: Return alert info for lead deals instead of sending directly
    # Caller should send alert AFTER transaction commits to avoid alerting for rolled-back deals
    alert_info = None
    if extraction.tracked_fund_is_lead:
        # Find the lead investor name from extraction
        lead_investor_name = "Unknown"
        for inv in extraction.lead_investors:
            if inv.is_tracked_fund:
                lead_investor_name = inv.name
                break

        alert_info = LeadDealAlertInfo(
            company_name=extraction.startup_name,
            amount=extraction.amount,
            round_type=extraction.round_label.value,
            lead_investor=lead_investor_name,
            enterprise_category=extraction.enterprise_category.value if extraction.enterprise_category else None,
            verification_snippet=extraction.verification_snippet,
        )

    return deal, alert_info


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

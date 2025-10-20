"""
Storage pipeline for persisting extracted deals to the database.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Fund,
    PortfolioCompany,
    Deal,
    DealInvestor,
    Article,
)
from .database import get_session
from ..analyst.schemas import DealExtraction, LeadStatus, EnterpriseCategory
from ..config.funds import FUND_REGISTRY
from ..scheduler.notifications import send_lead_deal_alert


async def get_or_create_company(
    session: AsyncSession,
    name: str,
    description: Optional[str] = None,
    sector: Optional[str] = None,
) -> PortfolioCompany:
    """Get existing company or create new one."""
    stmt = select(PortfolioCompany).where(PortfolioCompany.name == name)
    result = await session.execute(stmt)
    company = result.scalar_one_or_none()

    if company:
        return company

    company = PortfolioCompany(
        name=name,
        description=description,
        sector=sector,
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


async def save_deal(
    session: AsyncSession,
    extraction: DealExtraction,
    article_url: str,
    article_title: str,
    article_text: Optional[str] = None,
    source_fund_slug: Optional[str] = None,
) -> Deal:
    """
    Save an extracted deal to the database.

    Creates:
    - PortfolioCompany (if not exists)
    - Deal record
    - DealInvestor records for all investors
    - Article record linked to the deal
    """
    # Get or create company
    company = await get_or_create_company(
        session,
        name=extraction.startup_name,
        description=extraction.startup_description,
    )

    # Create deal with enterprise AI classification
    deal = Deal(
        company_id=company.id,
        round_type=extraction.round_label.value,
        amount=extraction.amount,
        valuation=extraction.valuation,
        announced_date=extraction.round_date,
        is_lead_confirmed=extraction.tracked_fund_is_lead,
        lead_partner_name=extraction.tracked_fund_partner,
        verification_snippet=extraction.verification_snippet,
        enterprise_category=extraction.enterprise_category.value if extraction.enterprise_category else None,
        is_enterprise_ai=extraction.is_enterprise_ai,
        confidence_score=extraction.confidence_score,
        thesis_drift_score=extraction.thesis_drift_score,
    )
    session.add(deal)
    await session.flush()

    # Add lead investors
    for investor in extraction.lead_investors:
        fund = None
        if investor.is_tracked_fund:
            # Try to match to a tracked fund
            for slug in FUND_REGISTRY:
                if FUND_REGISTRY[slug].name.lower() in investor.name.lower():
                    fund = await get_or_create_fund(session, slug)
                    break

        deal_investor = DealInvestor(
            deal_id=deal.id,
            fund_id=fund.id if fund else None,
            investor_name=investor.name,
            is_lead=True,
            is_tracked_fund=investor.is_tracked_fund,
            partner_name=investor.partner_name,
            role=investor.role.value,
        )
        session.add(deal_investor)

    # Add participating investors
    for investor in extraction.participating_investors:
        fund = None
        if investor.is_tracked_fund:
            for slug in FUND_REGISTRY:
                if FUND_REGISTRY[slug].name.lower() in investor.name.lower():
                    fund = await get_or_create_fund(session, slug)
                    break

        deal_investor = DealInvestor(
            deal_id=deal.id,
            fund_id=fund.id if fund else None,
            investor_name=investor.name,
            is_lead=False,
            is_tracked_fund=investor.is_tracked_fund,
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
    stmt = select(Deal).order_by(Deal.created_at.desc())

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

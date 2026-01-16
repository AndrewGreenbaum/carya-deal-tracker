"""
Database models using SQLModel (SQLAlchemy + Pydantic).

Schema:
- Fund: The 18 tracked VC funds
- Partner: Partners at each fund
- PortfolioCompany: Startups that received funding
- Deal: Individual funding rounds
- DealInvestor: Join table linking deals to investors with role
- Article: Source articles with embeddings for deduplication
"""

from datetime import datetime, date, timezone
from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime
import uuid


class Fund(SQLModel, table=True):
    """A tracked VC fund."""
    __tablename__ = "funds"

    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    website: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    partners: List["Partner"] = Relationship(back_populates="fund")
    deals: List["DealInvestor"] = Relationship(back_populates="fund")


class Partner(SQLModel, table=True):
    """A partner at a VC fund."""
    __tablename__ = "partners"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    fund_id: int = Field(foreign_key="funds.id")
    linkedin_url: Optional[str] = None
    twitter_handle: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    fund: Fund = Relationship(back_populates="partners")


class PortfolioCompany(SQLModel, table=True):
    """A startup that received funding."""
    __tablename__ = "portfolio_companies"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None  # Company LinkedIn page
    sector: Optional[str] = None
    founded_year: Optional[int] = None
    hq_location: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    deals: List["Deal"] = Relationship(back_populates="company")


class Deal(SQLModel, table=True):
    """A funding round."""
    __tablename__ = "deals"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="portfolio_companies.id")

    # Round details
    round_type: str  # seed, series_a, series_b, etc.
    amount: Optional[str] = None  # "$50M"
    amount_usd: Optional[int] = None  # 50000000 (normalized)
    valuation: Optional[str] = None
    announced_date: Optional[date] = None

    # Lead investor tracking
    # FIX #43: Add index=True for frequently filtered fields
    is_lead_confirmed: bool = Field(default=False, index=True)  # True if we confirmed a tracked fund led
    lead_partner_name: Optional[str] = None  # Partner who led the deal
    verification_snippet: Optional[str] = None  # Exact quote proving lead status

    # AI classification
    enterprise_category: Optional[str] = None  # infrastructure, security, vertical_saas, agentic, data_intelligence, consumer_ai, gaming_ai, social_ai, not_ai
    is_enterprise_ai: bool = Field(default=False, index=True)  # True if B2B Enterprise AI (not consumer)
    is_ai_deal: bool = Field(default=False, index=True)  # True if AI company (enterprise OR consumer)

    # Founders (stored as JSON list)
    founders_json: Optional[str] = None  # JSON: [{"name": "...", "title": "...", "linkedin_url": "..."}]

    # Quality scores
    confidence_score: float = 0.0
    thesis_drift_score: float = 0.0

    # Metadata
    # FIX #43: Add index=True for date range queries
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    company: PortfolioCompany = Relationship(back_populates="deals")
    investors: List["DealInvestor"] = Relationship(
        back_populates="deal",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    articles: List["Article"] = Relationship(
        back_populates="deal",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class DealInvestor(SQLModel, table=True):
    """Join table: which investors participated in which deals."""
    __tablename__ = "deal_investors"

    id: Optional[int] = Field(default=None, primary_key=True)
    deal_id: int = Field(foreign_key="deals.id")
    fund_id: Optional[int] = Field(default=None, foreign_key="funds.id")

    # Investor details (for non-tracked funds)
    investor_name: str
    is_lead: bool = False
    is_tracked_fund: bool = False
    partner_name: Optional[str] = None
    role: str = "participant"  # confirmed_lead, likely_lead, participant, unresolved

    # Relationships
    deal: Deal = Relationship(back_populates="investors")
    fund: Optional[Fund] = Relationship(back_populates="deals")


class Article(SQLModel, table=True):
    """Source article with embedding for deduplication."""
    __tablename__ = "articles"

    id: Optional[int] = Field(default=None, primary_key=True)
    deal_id: Optional[int] = Field(default=None, foreign_key="deals.id")

    # Article metadata
    url: str = Field(unique=True, index=True)
    title: str
    source_fund_slug: Optional[str] = None  # Which fund's feed we found it on
    published_date: Optional[date] = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Content
    raw_html: Optional[str] = None
    extracted_text: Optional[str] = None
    summary: Optional[str] = None

    # Embedding for deduplication (1536 dimensions for text-embedding-3-small)
    # Note: Using Column directly for pgvector support
    embedding: Optional[List[float]] = Field(
        default=None,
        sa_column=Column(Vector(1536))
    )

    # Deduplication
    is_duplicate: bool = False
    duplicate_of_id: Optional[int] = Field(default=None, foreign_key="articles.id")

    # Processing status
    is_processed: bool = False
    processing_error: Optional[str] = None

    # Relationships
    deal: Optional[Deal] = Relationship(back_populates="articles")


class StealthDetection(SQLModel, table=True):
    """Track stealth portfolio additions detected via sitemap diffing."""
    __tablename__ = "stealth_detections"

    id: Optional[int] = Field(default=None, primary_key=True)
    fund_slug: str = Field(index=True)
    detected_url: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    company_name: Optional[str] = None
    is_confirmed: bool = False
    notes: Optional[str] = None


class PortfolioSnapshot(SQLModel, table=True):
    """Store portfolio page snapshots for diff detection.

    Replaces ephemeral /tmp/ storage with persistent database storage.
    This survives Railway redeploys and ensures accurate diff detection.
    """
    __tablename__ = "portfolio_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    fund_slug: str = Field(unique=True, index=True)  # One snapshot per fund
    companies_json: str  # JSON list of company names (lowercase)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ThesisDrift(SQLModel, table=True):
    """Track thesis drift scores over time."""
    __tablename__ = "thesis_drift"

    id: Optional[int] = Field(default=None, primary_key=True)
    fund_slug: str = Field(index=True)
    deal_id: int = Field(foreign_key="deals.id")
    drift_score: float
    sector_embedding: Optional[List[float]] = Field(
        default=None,
        sa_column=Column(Vector(1536))
    )
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TrackerItem(SQLModel, table=True):
    """CRM tracker item for managing deal pipeline.

    Represents a company being tracked through the investment pipeline.
    Can be linked to an existing Deal or created manually.

    Status columns (Kanban):
    - watching: Monitoring, not yet reached out
    - reached_out: Initial contact made
    - in_conversation: Active dialogue ongoing
    - closing_spv: Finalizing SPV/investment
    """
    __tablename__ = "tracker_items"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Company info
    company_name: str = Field(max_length=255)
    round_type: Optional[str] = Field(default=None, max_length=50)  # seed, series_a, etc.
    amount: Optional[str] = Field(default=None, max_length=100)  # "$50M"
    lead_investor: Optional[str] = Field(default=None, max_length=255)
    website: Optional[str] = Field(default=None, max_length=500)

    # CRM/Pipeline fields
    status: str = Field(default="watching", max_length=50, index=True)
    notes: Optional[str] = None
    last_contact_date: Optional[date] = None
    next_step: Optional[str] = None
    position: int = Field(default=0)  # Order within column for drag-drop

    # Optional link to existing deal (for deals added from Dashboard)
    deal_id: Optional[int] = Field(default=None, foreign_key="deals.id")

    # Metadata - use sa_column for proper PostgreSQL TIMESTAMPTZ type
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    )


class Feedback(SQLModel, table=True):
    """User feedback: flags and suggestions.

    Replaces Google Sheets integration - stores directly in PostgreSQL.

    feedback_type:
    - 'flag': Report a problem with a deal
    - 'suggestion': Suggest a missing company or report an error
    """
    __tablename__ = "feedback"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Feedback type and content
    feedback_type: str = Field(max_length=20, index=True)  # 'flag' or 'suggestion'
    company_name: str = Field(max_length=255)
    deal_id: Optional[int] = Field(default=None, foreign_key="deals.id")
    reason: Optional[str] = None  # Flag reason or suggestion details
    suggestion_type: Optional[str] = Field(default=None, max_length=50)  # 'missing_company', 'error', 'other'
    source_url: Optional[str] = None
    reporter_email: Optional[str] = Field(default=None, max_length=255)

    # Review tracking
    reviewed: bool = Field(default=False, index=True)
    reviewed_at: Optional[datetime] = None

    # Metadata
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    )


class TokenUsage(SQLModel, table=True):
    """Track Claude API token usage per extraction.

    Records input/output tokens for each Claude API call to enable:
    - Cost breakdown by source (brave_search, techcrunch, a16z, etc.)
    - Daily/weekly/monthly usage trends
    - Identifying which sources consume the most tokens

    Pricing (Claude 3.5 Haiku as of Dec 2024):
    - Input: $0.25 per 1M tokens
    - Output: $1.25 per 1M tokens
    - Cache read: $0.025 per 1M tokens (90% discount)
    - Cache write: $0.3125 per 1M tokens
    """
    __tablename__ = "token_usage"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Source identification
    source_name: str = Field(index=True)  # "brave_search", "techcrunch", "a16z", etc.
    scan_id: Optional[str] = Field(default=None)  # Job ID for grouping
    article_url: Optional[str] = Field(default=None)

    # Token counts
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cache_read_tokens: int = Field(default=0)
    cache_write_tokens: int = Field(default=0)

    # Model info
    model: str = Field(default="claude-3-5-haiku-20241022")

    # Calculated cost (USD)
    estimated_cost_usd: float = Field(default=0.0)

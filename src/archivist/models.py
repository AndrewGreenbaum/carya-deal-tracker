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

from datetime import datetime, date
from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
import uuid


class Fund(SQLModel, table=True):
    """A tracked VC fund."""
    __tablename__ = "funds"

    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    website: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

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
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    fund: Fund = Relationship(back_populates="partners")


class PortfolioCompany(SQLModel, table=True):
    """A startup that received funding."""
    __tablename__ = "portfolio_companies"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = None
    website: Optional[str] = None
    sector: Optional[str] = None
    founded_year: Optional[int] = None
    hq_location: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

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

    # Status
    is_lead_confirmed: bool = False  # True if we confirmed a tracked fund led
    confidence_score: float = 0.0
    thesis_drift_score: float = 0.0

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    company: PortfolioCompany = Relationship(back_populates="deals")
    investors: List["DealInvestor"] = Relationship(back_populates="deal")
    articles: List["Article"] = Relationship(back_populates="deal")


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
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

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
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    company_name: Optional[str] = None
    is_confirmed: bool = False
    notes: Optional[str] = None


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
    calculated_at: datetime = Field(default_factory=datetime.utcnow)

"""
Database session management for async SQLAlchemy operations.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlmodel import SQLModel

from ..config.settings import settings

logger = logging.getLogger(__name__)


# Create async engine with connection pooling optimizations
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,   # Detect stale connections before use
    pool_recycle=3600,    # Recycle connections every hour
    pool_timeout=30,      # Wait max 30s for connection from pool
    connect_args={
        "command_timeout": 30,  # Timeout for individual queries (asyncpg)
        "server_settings": {
            "statement_timeout": "30000",  # PostgreSQL statement timeout (ms)
        },
    },
)

# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def close_db():
    """Close database connections."""
    await engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            logger.error(f"Database session error, rolling back: {e}")
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions.

    Use get_session() for non-FastAPI code (scheduler, orchestrator, etc).
    Use get_db() only as a FastAPI Depends() injection.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            logger.error(f"Database session error, rolling back: {e}")
            await session.rollback()
            raise

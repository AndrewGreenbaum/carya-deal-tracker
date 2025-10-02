"""
Application settings loaded from environment variables.
"""

import os
from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache


class Settings(BaseSettings):
    """Application configuration from environment."""

    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Database - Railway provides DATABASE_URL
    database_url: str = "postgresql+asyncpg://bud:tracker@localhost:5432/budtracker"

    @field_validator("database_url", mode="before")
    @classmethod
    def convert_database_url(cls, v: str) -> str:
        """Convert standard postgres:// URL to asyncpg format."""
        # Railway uses postgres:// or postgresql://, we need postgresql+asyncpg://
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    # LLM Settings
    llm_model: str = "claude-sonnet-4-20250514"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096

    # Embedding Settings
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Scraping Settings
    request_timeout: int = 30
    rate_limit_delay: float = 1.0
    max_retries: int = 3

    # Deduplication
    similarity_threshold: float = 0.95
    dedup_window_days: int = 7

    # Webhooks (optional)
    slack_webhook_url: str = ""
    discord_webhook_url: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # Allow reading DATABASE_URL from environment
        extra = "ignore"


def get_settings() -> Settings:
    """Get settings instance (no caching to pick up env vars)."""
    return Settings()


# Create settings instance at import time
settings = Settings()

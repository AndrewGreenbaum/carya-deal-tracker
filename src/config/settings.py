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
    brave_search_key: str = ""  # Brave Search API for news discovery
    firecrawl_api_key: str = ""  # Firecrawl for JS-heavy PR sites
    twitter_bearer_token: str = ""  # Twitter API v2 Bearer Token
    google_alerts_feeds: str = ""  # Comma-separated Google Alerts RSS feed URLs
    api_keys: str = "dev-key"  # Comma-separated valid API keys for authentication

    # Y Combinator Algolia credentials (public, from YC website)
    yc_algolia_app_id: str = "45BWZJ1SGC"
    yc_algolia_api_key: str = "<SET_IN_ENV>"

    @property
    def valid_api_keys(self) -> list[str]:
        """Get list of valid API keys."""
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    # Frontend URL for CORS (production)
    frontend_url: str = ""  # e.g., https://your-frontend.railway.app

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
    llm_model: str = "claude-3-haiku-20240307"
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

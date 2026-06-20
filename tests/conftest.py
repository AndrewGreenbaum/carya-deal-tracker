"""
Pytest fixtures for test infrastructure.

This module is automatically loaded by pytest and provides shared fixtures.
For skip markers and helper functions, see test_helpers.py.
"""

import pytest


# =============================================================================
# Shared fixtures
# =============================================================================
@pytest.fixture
def sample_article_text():
    """Sample article text for testing extraction."""
    return """
    TechStartup Inc. Raises $50 Million Series B Led by Sequoia Capital

    SAN FRANCISCO, Dec 17, 2024 — TechStartup Inc., the leading provider of AI-powered
    workflow automation, today announced it has raised $50 million in Series B funding.
    The round was led by Sequoia Capital, with participation from Accel.
    """


@pytest.fixture
def sample_deal_data():
    """Sample deal data for testing storage/deduplication."""
    from datetime import date
    return {
        "company_name": "TechStartup Inc",
        "round_type": "series_b",
        "amount_usd": 50_000_000,
        "announced_date": date(2026, 1, 15),
        "fund_slug": "sequoia",
    }

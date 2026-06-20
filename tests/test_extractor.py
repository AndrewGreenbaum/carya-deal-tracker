"""
Test harness for the intelligence layer with sample press releases.

Run with: pytest tests/test_extractor.py -v

Note: These tests require src.analyst which needs Python 3.10+ and instructor.
Tests will be skipped if imports fail.
"""

import pytest
import asyncio

# Import test helpers
from tests.test_helpers import skip_no_analyst, can_import_analyst

# Skip entire module if analyst imports fail (Python 3.9 or missing instructor)
pytestmark = [skip_no_analyst]

# Lazy imports - only executed if module isn't skipped
if can_import_analyst():
    from src.analyst import extract_deal, LeadStatus, RoundType
    from src.analyst.extractor import clear_content_hash_cache
else:
    # Dummy values for when imports fail (tests will be skipped anyway)
    extract_deal = None
    LeadStatus = None
    RoundType = None
    clear_content_hash_cache = None


@pytest.fixture(autouse=True)
def clear_cache_before_test():
    """Clear the content hash cache before each test to avoid false duplicates."""
    if clear_content_hash_cache:
        clear_content_hash_cache()
    yield


# Sample press releases for testing
SAMPLE_ARTICLES = {
    "clear_lead": """
    TechStartup Inc. Raises $50 Million Series B Led by Sequoia Capital

    SAN FRANCISCO, Dec 17, 2024 — TechStartup Inc., the leading provider of AI-powered
    workflow automation, today announced it has raised $50 million in Series B funding.
    The round was led by Sequoia Capital, with participation from Accel and existing
    investor First Round Capital.

    "We're thrilled to partner with Sequoia as we scale our platform," said Jane Doe,
    CEO of TechStartup Inc. Alfred Lin, Partner at Sequoia Capital, will join the board.

    The funding will be used to expand the engineering team and accelerate product
    development.
    """,

    "ambiguous_lead": """
    DataCo Secures $30M to Expand Analytics Platform

    DataCo, the enterprise analytics startup, has raised $30 million in new funding.
    Existing investors led the round, with new investor a16z also participating.

    The company plans to use the funds to expand into European markets.
    """,

    "multiple_co_leads": """
    HealthTech Startup Closes $100M Series C

    HealthTech Inc. announced today a $100 million Series C round co-led by
    Andreessen Horowitz (a16z) and General Catalyst. Bessemer Venture Partners
    and GV also participated in the round.

    The company has now raised $150 million total and is valued at $800 million.
    """,

    "benchmark_disambiguation": """
    Benchmark Capital Leads $25M Round in Developer Tools Startup

    DevTools Corp raised $25 million in Series A funding led by Benchmark.
    Partner Sarah Tavel will join the board.

    Note: This is unrelated to Benchmark International's recent acquisition.
    """,

    "gv_disambiguation": """
    GV Invests in Climate Tech

    Google Ventures (GV) has led a $40 million investment in CleanEnergy Corp,
    a startup developing next-generation solar technology.

    (Note: GV the ticker symbol for Visionary Holdings traded separately today.)
    """,

    "participation_only": """
    Fintech Startup Raises $75M

    PaymentsCo raised $75 million in Series B funding led by Tiger Global.
    Sequoia Capital and Thrive Capital participated in the round.
    """,

    "not_funding_news": """
    Sequoia Capital Partner Shares Thoughts on AI

    In a recent blog post, a partner at Sequoia Capital discussed the future
    of artificial intelligence and its impact on startups.

    This is an opinion piece, not funding news.
    """
}


@pytest.mark.asyncio
async def test_clear_lead_identification():
    """Test that clear lead language is correctly identified."""
    result = await extract_deal(SAMPLE_ARTICLES["clear_lead"])

    assert result.startup_name == "TechStartup Inc."
    assert result.round_label == RoundType.SERIES_B
    assert result.amount == "$50 million" or result.amount == "$50M"
    assert result.tracked_fund_is_lead is True
    assert result.tracked_fund_name == "Sequoia Capital"
    assert any(inv.name.lower().find("sequoia") >= 0 for inv in result.lead_investors)


@pytest.mark.asyncio
async def test_ambiguous_lead_flagged():
    """Test that ambiguous 'existing investors led' is flagged."""
    result = await extract_deal(SAMPLE_ARTICLES["ambiguous_lead"])

    # Should flag ambiguity in reasoning or role
    # ChainOfThought only has final_reasoning field
    assert "ambiguous" in result.reasoning.final_reasoning.lower() or \
           "existing" in result.reasoning.final_reasoning.lower() or \
           "participant" in result.reasoning.final_reasoning.lower() or \
           result.tracked_fund_role == LeadStatus.UNRESOLVED or \
           result.tracked_fund_is_lead is False

    # a16z should be identified as participant
    assert any(inv.name.lower().find("a16z") >= 0 or
               inv.name.lower().find("andreessen") >= 0
               for inv in result.participating_investors)


@pytest.mark.asyncio
async def test_co_lead_identification():
    """Test that co-leads are both identified as leads."""
    result = await extract_deal(SAMPLE_ARTICLES["multiple_co_leads"])

    lead_names = [inv.name.lower() for inv in result.lead_investors]
    lead_str = " ".join(lead_names)

    # Both a16z and General Catalyst should be leads
    has_a16z = "a16z" in lead_str or "andreessen" in lead_str
    has_gc = "general catalyst" in lead_str

    assert has_a16z and has_gc, f"Expected both co-leads, got: {lead_names}"


@pytest.mark.asyncio
async def test_benchmark_disambiguation():
    """Test that Benchmark Capital is distinguished from Benchmark International."""
    result = await extract_deal(SAMPLE_ARTICLES["benchmark_disambiguation"])

    assert result.tracked_fund_is_lead is True
    assert result.tracked_fund_name == "Benchmark"
    # Should capture partner name
    assert result.tracked_fund_partner == "Sarah Tavel" or \
           any(inv.partner_name == "Sarah Tavel" for inv in result.lead_investors)


@pytest.mark.asyncio
async def test_gv_disambiguation():
    """Test that GV (Google Ventures) is distinguished from GV ticker."""
    result = await extract_deal(SAMPLE_ARTICLES["gv_disambiguation"])

    assert result.tracked_fund_is_lead is True
    # Should be identified as GV/Google Ventures, not stock ticker
    assert result.tracked_fund_name in ["GV (Google Ventures)", "GV", "Google Ventures"]


@pytest.mark.asyncio
async def test_participation_only():
    """Test that participation is distinguished from leading."""
    result = await extract_deal(SAMPLE_ARTICLES["participation_only"])

    # Sequoia and Thrive should be participants, not leads
    assert result.tracked_fund_is_lead is False

    participant_names = " ".join([inv.name.lower() for inv in result.participating_investors])
    assert "sequoia" in participant_names or "thrive" in participant_names


@pytest.mark.asyncio
async def test_chain_of_thought_populated():
    """Test that reasoning chain is populated."""
    result = await extract_deal(SAMPLE_ARTICLES["clear_lead"])

    assert result is not None, "extract_deal returned None - API may be unavailable"

    # ChainOfThought has been simplified to only final_reasoning
    assert result.reasoning is not None
    assert len(result.reasoning.final_reasoning) > 0


# Run tests manually
if __name__ == "__main__":
    async def run_all():
        print("Running extraction tests...\n")

        for name, article in SAMPLE_ARTICLES.items():
            print(f"\n{'='*60}")
            print(f"TEST: {name}")
            print('='*60)
            try:
                result = await extract_deal(article)
                print(f"Startup: {result.startup_name}")
                print(f"Round: {result.round_label}")
                print(f"Amount: {result.amount}")
                print(f"Tracked Fund Lead: {result.tracked_fund_is_lead}")
                print(f"Tracked Fund: {result.tracked_fund_name}")
                print(f"Role: {result.tracked_fund_role}")
                print(f"Confidence: {result.confidence_score}")
                print(f"\nReasoning: {result.reasoning.final_reasoning[:200]}...")
            except Exception as e:
                print(f"ERROR: {e}")

    asyncio.run(run_all())

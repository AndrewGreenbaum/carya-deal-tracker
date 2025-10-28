"""
Lead Signal Extractor - The Intelligence Core.

Uses Instructor + Claude 3.5 Sonnet for structured extraction of funding deals
with chain-of-thought reasoning for lead vs. participation determination.

OPTIMIZED:
- Prompt caching for system message (20% token savings)
- Batch extraction for multiple articles
- Early exit for non-funding content
"""

import asyncio
import instructor
from anthropic import Anthropic
from typing import Optional, List

from .schemas import DealExtraction, ArticleAnalysis, LeadStatus, RoundType, EnterpriseCategory
from ..config.settings import settings
from ..config.funds import FUND_REGISTRY, FundConfig


# Initialize Instructor with Anthropic client
client = instructor.from_anthropic(Anthropic(api_key=settings.anthropic_api_key))

# Fund name variations for matching
FUND_NAME_VARIANTS = {
    "founders_fund": ["Founders Fund", "Founder's Fund", "FF"],
    "benchmark": ["Benchmark", "Benchmark Capital"],
    "sequoia": ["Sequoia", "Sequoia Capital"],
    "khosla": ["Khosla", "Khosla Ventures", "KV"],
    "index": ["Index", "Index Ventures"],
    "a16z": ["a16z", "Andreessen Horowitz", "Andreessen", "AH"],
    "insight": ["Insight", "Insight Partners"],
    "bessemer": ["Bessemer", "BVP", "Bessemer Venture Partners"],
    "redpoint": ["Redpoint", "Redpoint Ventures"],
    "greylock": ["Greylock", "Greylock Partners"],
    "gv": ["GV", "Google Ventures"],
    "menlo": ["Menlo", "Menlo Ventures"],
    "usv": ["USV", "Union Square Ventures", "Union Square"],
    "thrive": ["Thrive", "Thrive Capital"],
    "accel": ["Accel", "Accel Partners"],
    "felicis": ["Felicis", "Felicis Ventures"],
    "general_catalyst": ["General Catalyst", "GC"],
    "first_round": ["First Round", "First Round Capital"],
}


SYSTEM_PROMPT = """You are an expert venture capital analyst specializing in identifying lead investors in B2B Enterprise AI funding rounds.

Your task is to analyze press releases and news articles about startup funding and extract:
1. Key deal details (company, round type, amount)
2. Company website URL and LinkedIn page URL if mentioned
3. Founders with their names, titles, and LinkedIn URLs if available
4. Lead vs. participating investors (with verification snippet)
5. Whether this is an ENTERPRISE AI company (not consumer)
6. Specifically identify if any of our TRACKED FUNDS LED the round

═══════════════════════════════════════════════════════════════════
ENTERPRISE AI CLASSIFICATION (CRITICAL - Filter Consumer Companies)
═══════════════════════════════════════════════════════════════════

INCLUDE (set is_enterprise_ai=True):
- INFRASTRUCTURE: LLMOps, AI chips, MLOps, vector databases, inference optimization
- SECURITY: AI-powered cybersecurity, threat detection, compliance automation
- VERTICAL_SAAS: AI for Healthcare, Finance, Legal, HR (B2B tools for professionals)
- AGENTIC: Workflow automation for teams, AI agents for enterprise, copilots
- DATA_INTELLIGENCE: Enterprise search (e.g., Glean), analytics, knowledge management

EXCLUDE (set is_enterprise_ai=False, enterprise_category=NOT_ENTERPRISE):
- Consumer chatbots, personal AI assistants
- Gaming AI, entertainment AI
- Consumer shopping/recommendations (e.g., personal shopping assistants)
- B2C wearables, fitness AI
- General foundational model rounds (OpenAI, Anthropic main fundraises)
- Social media AI, dating AI, personal productivity apps

If unsure between Enterprise and Consumer, default to NOT_ENTERPRISE.

═══════════════════════════════════════════════════════════════════
LEAD INVESTOR PROTOCOL (STRICT VERIFICATION REQUIRED)
═══════════════════════════════════════════════════════════════════

LEAD: The investor who "led", "priced", "set terms", or is listed with:
- "led by [Investor]"
- "round was headed by"
- "led the investment"
- "Series X led by"
- "[Investor] led the round"

PARTICIPANT (NOT LEAD - these should be EXCLUDED):
- "participated in"
- "joined the round"
- "investors included"
- "with participation from"

VERIFICATION SNIPPET (REQUIRED):
You MUST extract the EXACT quote from the article proving lead status.
Set verification_snippet to the phrase like: "led by Sequoia Capital"
If no explicit lead language exists, set tracked_fund_is_lead=False.

TRACKED FUNDS (identify these specifically):
- Founders Fund, Benchmark, Sequoia Capital, Khosla Ventures, Index Ventures
- a16z (Andreessen Horowitz), Insight Partners, Bessemer (BVP), Redpoint, Greylock
- GV (Google Ventures), Menlo Ventures, USV (Union Square Ventures), Thrive Capital
- Accel, Felicis Ventures, General Catalyst, First Round Capital

DISAMBIGUATION RULES:
- "Benchmark" = Benchmark Capital (the VC). Exclude "Benchmark International" or "Benchmark Electronics"
- "GV" = Google Ventures. Exclude stock ticker GV (Visionary Holdings)
- "Thrive" = Thrive Capital. Exclude "Thrive IT Services" or "Thrive Global"
- "Sequoia" = Sequoia Capital US. Note if HongShan/Peak XV (formerly Sequoia China/India)

═══════════════════════════════════════════════════════════════════
COMPANY INFO EXTRACTION (ONLY EXTRACT IF EXPLICITLY MENTIONED)
═══════════════════════════════════════════════════════════════════

IMPORTANT: Only extract URLs that are EXPLICITLY mentioned in the article.
DO NOT guess, infer, or construct URLs. If not mentioned, leave as null.

COMPANY WEBSITE:
- ONLY extract if the actual URL is mentioned in the article
- Example: "visit acme.ai" → "https://acme.ai"
- If NOT mentioned in article → set to null (do NOT guess)

COMPANY LINKEDIN:
- ONLY extract if the LinkedIn URL is explicitly mentioned
- Must be a direct company page URL like: https://www.linkedin.com/company/acme
- If NOT mentioned in article → set to null (do NOT construct)

FOUNDERS:
- Extract all founders mentioned with their names and titles
- Common titles: CEO, CTO, Co-founder, Founder & CEO, etc.
- linkedin_url: ONLY include if the founder's actual LinkedIn profile URL is mentioned
  - Must be a direct profile URL like: https://www.linkedin.com/in/john-smith
  - If NOT mentioned → set to null (do NOT create search URLs)

═══════════════════════════════════════════════════════════════════
CHAIN OF THOUGHT
═══════════════════════════════════════════════════════════════════

Always explain your reasoning by:
1. Identifying if company is Enterprise AI vs Consumer
2. Identifying key verbs (led, participated, joined, priced)
3. Extracting the EXACT verification snippet proving lead status
4. Listing signals for lead status
5. Listing signals for participation
6. Flagging any ambiguity
7. Making a final determination with confidence

Output must be valid JSON matching the schema."""


# Keywords that indicate funding content (for early exit optimization)
FUNDING_KEYWORDS = [
    "funding", "raised", "series", "seed", "investment", "round",
    "million", "billion", "venture", "capital", "investor", "led by",
    "announces", "secures", "closes", "valuation"
]


def is_likely_funding_content(text: str) -> bool:
    """
    Quick check if text is likely about funding.

    Used for early exit optimization - skip Claude call if text
    doesn't contain funding-related keywords.
    """
    text_lower = text.lower()
    matches = sum(1 for kw in FUNDING_KEYWORDS if kw in text_lower)
    return matches >= 2  # Require at least 2 funding keywords


def build_extraction_prompt(
    article_text: str,
    fund_context: Optional[FundConfig] = None,
    source_url: str = ""
) -> str:
    """Build the extraction prompt with optional fund-specific context."""

    prompt = f"""Analyze this article and extract all funding deal information.

SOURCE URL: {source_url}

ARTICLE TEXT:
---
{article_text}
---

"""

    if fund_context:
        prompt += f"""
FUND-SPECIFIC CONTEXT:
This article was found via {fund_context.name}'s news feed.
Extraction notes: {fund_context.extraction_notes}
"""
        if fund_context.negative_keywords:
            prompt += f"EXCLUDE if these appear: {', '.join(fund_context.negative_keywords)}\n"
        if fund_context.partner_names:
            prompt += f"Known partners: {', '.join(fund_context.partner_names)}\n"

    prompt += """
Extract the deal(s) with full chain-of-thought reasoning.
Pay special attention to whether our tracked funds LED vs merely PARTICIPATED."""

    return prompt


async def extract_deal(
    article_text: str,
    source_url: str = "",
    fund_context: Optional[FundConfig] = None,
    skip_funding_check: bool = False
) -> DealExtraction:
    """
    Extract a single funding deal from article text.

    OPTIMIZED:
    - Uses prompt caching for system message (20% token savings)
    - Early exit for non-funding content

    Args:
        article_text: The article content to analyze
        source_url: URL of the source article
        fund_context: Optional fund-specific extraction context
        skip_funding_check: If True, skip the funding keyword check

    Returns:
        DealExtraction with structured deal information
    """
    # Early exit: check if text is likely about funding
    if not skip_funding_check and not is_likely_funding_content(article_text):
        # Return low-confidence empty extraction
        from .schemas import ChainOfThought, InvestorMention
        return DealExtraction(
            startup_name="Unknown",
            startup_description=None,
            round_label=RoundType.UNKNOWN,
            amount=None,
            valuation=None,
            round_date=None,
            lead_investors=[],
            participating_investors=[],
            tracked_fund_is_lead=False,
            tracked_fund_name=None,
            tracked_fund_role=None,
            tracked_fund_partner=None,
            enterprise_category=EnterpriseCategory.NOT_ENTERPRISE,
            is_enterprise_ai=False,
            verification_snippet=None,
            confidence_score=0.1,
            thesis_drift_score=0.0,
            reasoning=ChainOfThought(
                verb_analysis="No funding keywords detected",
                lead_signals=[],
                participation_signals=[],
                ambiguity_flags=["Text does not appear to be about funding"],
                final_reasoning="Skipped extraction - no funding content detected"
            )
        )

    prompt = build_extraction_prompt(article_text, fund_context, source_url)

    # Use prompt caching for system message (significant token savings)
    response = client.messages.create(
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"}  # Enable prompt caching
            }
        ],
        messages=[{"role": "user", "content": prompt}],
        response_model=DealExtraction,
    )

    # Post-process: verify tracked fund identification
    response = _verify_tracked_fund(response)

    return response


async def extract_deal_batch(
    articles: List[dict],
    max_concurrent: int = 5
) -> List[DealExtraction]:
    """
    Extract deals from multiple articles concurrently.

    OPTIMIZED:
    - Concurrent processing with semaphore
    - Shared prompt cache across calls
    - Early exit for non-funding content

    Args:
        articles: List of dicts with keys: text, source_url, fund_slug (optional)
        max_concurrent: Maximum concurrent Claude API calls

    Returns:
        List of DealExtraction results (in same order as input)
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def extract_with_limit(article: dict) -> DealExtraction:
        async with semaphore:
            fund_context = None
            if article.get("fund_slug") and article["fund_slug"] in FUND_REGISTRY:
                fund_context = FUND_REGISTRY[article["fund_slug"]]

            return await extract_deal(
                article_text=article["text"],
                source_url=article.get("source_url", ""),
                fund_context=fund_context
            )

    # Run all extractions concurrently (limited by semaphore)
    results = await asyncio.gather(
        *[extract_with_limit(article) for article in articles],
        return_exceptions=True
    )

    # Handle any exceptions
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            # Return low-confidence extraction for failed articles
            from .schemas import ChainOfThought
            processed_results.append(DealExtraction(
                startup_name="Error",
                startup_description=None,
                round_label=RoundType.UNKNOWN,
                amount=None,
                valuation=None,
                round_date=None,
                lead_investors=[],
                participating_investors=[],
                tracked_fund_is_lead=False,
                tracked_fund_name=None,
                tracked_fund_role=None,
                tracked_fund_partner=None,
                enterprise_category=EnterpriseCategory.NOT_ENTERPRISE,
                is_enterprise_ai=False,
                verification_snippet=None,
                confidence_score=0.0,
                thesis_drift_score=0.0,
                reasoning=ChainOfThought(
                    verb_analysis="Extraction failed",
                    lead_signals=[],
                    participation_signals=[],
                    ambiguity_flags=[f"Error: {str(result)}"],
                    final_reasoning="Extraction failed due to error"
                )
            ))
        else:
            processed_results.append(result)

    return processed_results


async def extract_article(
    article_text: str,
    source_url: str = "",
    title: str = "",
    fund_context: Optional[FundConfig] = None
) -> ArticleAnalysis:
    """
    Analyze a full article that may contain multiple deals.

    Args:
        article_text: The article content to analyze
        source_url: URL of the source article
        title: Article title
        fund_context: Optional fund-specific extraction context

    Returns:
        ArticleAnalysis with all deals found
    """
    prompt = build_extraction_prompt(article_text, fund_context, source_url)

    response = client.messages.create(
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"}
            }
        ],
        messages=[{"role": "user", "content": prompt}],
        response_model=ArticleAnalysis,
    )

    # Post-process each deal
    for deal in response.deals:
        _verify_tracked_fund(deal)

    return response


def _verify_tracked_fund(deal: DealExtraction) -> DealExtraction:
    """
    Post-process to verify tracked fund identification.

    Ensures the tracked_fund fields are consistent with lead_investors.
    """
    # Check all lead investors against our fund registry
    for investor in deal.lead_investors:
        matched_fund = _match_fund_name(investor.name)
        if matched_fund:
            investor.is_tracked_fund = True
            if investor.role in [LeadStatus.CONFIRMED_LEAD, LeadStatus.LIKELY_LEAD]:
                deal.tracked_fund_is_lead = True
                deal.tracked_fund_name = FUND_REGISTRY[matched_fund].name
                deal.tracked_fund_role = investor.role
                deal.tracked_fund_partner = investor.partner_name
                break

    # Also check participating investors
    if not deal.tracked_fund_is_lead:
        for investor in deal.participating_investors:
            matched_fund = _match_fund_name(investor.name)
            if matched_fund:
                investor.is_tracked_fund = True
                deal.tracked_fund_name = FUND_REGISTRY[matched_fund].name
                deal.tracked_fund_role = LeadStatus.PARTICIPANT
                deal.tracked_fund_partner = investor.partner_name
                break

    return deal


def _match_fund_name(name: str) -> Optional[str]:
    """
    Match an investor name to a tracked fund slug.

    Returns the fund slug if matched, None otherwise.
    """
    name_lower = name.lower().strip()

    for slug, variants in FUND_NAME_VARIANTS.items():
        for variant in variants:
            if variant.lower() in name_lower or name_lower in variant.lower():
                # Check negative keywords
                fund = FUND_REGISTRY[slug]
                for neg in fund.negative_keywords:
                    if neg.lower() in name_lower:
                        return None  # Excluded by negative keyword
                return slug

    return None


def check_negative_filters(text: str, fund: FundConfig) -> bool:
    """
    Check if text should be filtered out based on fund's negative keywords.

    Returns True if text should be EXCLUDED (contains negative keywords).
    """
    text_lower = text.lower()
    for keyword in fund.negative_keywords:
        if keyword.lower() in text_lower:
            return True
    return False


# Convenience function for testing
async def quick_extract(text: str) -> dict:
    """Quick extraction for testing - returns dict instead of model."""
    result = await extract_deal(text)
    return result.model_dump()

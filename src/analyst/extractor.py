"""
Lead Signal Extractor - The Intelligence Core.

Uses Instructor + Claude 3.5 Sonnet for structured extraction of funding deals
with chain-of-thought reasoning for lead vs. participation determination.

OPTIMIZED:
- Prompt caching for system message (20% token savings)
- Batch extraction for multiple articles
- Early exit for non-funding content
- Content hash deduplication to avoid duplicate API calls
- Token usage logging for cost tracking
"""

import asyncio
import hashlib
import logging
import re
import instructor
import httpx
from anthropic import Anthropic, APITimeoutError, APIError, RateLimitError
from instructor.core import InstructorRetryException
from typing import Optional, List, Set
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Thread-local storage for source tracking (set by callers)
_current_source_name: Optional[str] = None
_current_scan_id: Optional[str] = None

# API call configuration - loaded from settings (see below after settings import)

# Session-based content hash cache (cleared between runs)
# Prevents duplicate Claude calls for same article content
_content_hash_cache: Set[str] = set()
_content_hash_lock = asyncio.Lock()  # FIX: Async-safe cache access

from .schemas import DealExtraction, ArticleAnalysis, LeadStatus, RoundType, EnterpriseCategory
from ..config.settings import settings
from ..config.funds import FUND_REGISTRY, FundConfig
# Note: match_fund_name is imported lazily in _verify_tracked_fund to avoid circular import

# FIX #42: Initialize module-level constants from settings (not hardcoded)
API_TIMEOUT_SECONDS = settings.llm_timeout
MAX_RETRIES = settings.llm_max_retries

# Initialize Instructor with Anthropic client
# FIX: Configure timeout at client level for reliable timeout handling
_anthropic_client = Anthropic(
    api_key=settings.anthropic_api_key,
    timeout=httpx.Timeout(settings.llm_timeout, connect=settings.llm_connect_timeout),
)
client = instructor.from_anthropic(_anthropic_client)

# Pre-built cached system message (optimization: created once, reused across calls)
# Anthropic's prompt caching uses content-based caching, but we save object creation overhead
CACHED_SYSTEM_MESSAGE = [
    {
        "type": "text",
        "text": None,  # Will be set after SYSTEM_PROMPT is defined
        "cache_control": {"type": "ephemeral"}
    }
]


SYSTEM_PROMPT = """You are a VC analyst extracting funding deals from articles. Extract:
1. Deal details (company, round, amount, date)
2. Lead vs participating investors (with proof snippet)
3. Whether company is Enterprise AI (B2B, not consumer)
4. If any TRACKED FUND led the round

ENTERPRISE AI CLASSIFICATION
INCLUDE (is_enterprise_ai=True): Infrastructure (LLMOps, MLOps, vector DBs), Security, Vertical SaaS (Healthcare/Finance/Legal/HR), Agentic (workflow automation), Data Intelligence (enterprise search)
EXCLUDE (is_enterprise_ai=False): Consumer AI, gaming, crypto/blockchain/Web3/DeFi/NFT, social/dating apps. If unsure or crypto-related, default to NOT_ENTERPRISE.

LEAD VERIFICATION (STRICT)
LEAD phrases: "led by", "led the", "leads", "co-led", "headed by", "spearheaded"
NOT LEAD: "backed by", "participated in", "invested in", "joined"

CRITICAL: Only set tracked_fund_is_lead=True if verification_snippet contains ACTUAL lead language.
If no lead phrase exists → put in participating_investors with role=participant, NOT lead_investors.

TRACKED FUNDS: Founders Fund, Benchmark, Sequoia, Khosla, Index, a16z, Insight Partners, Bessemer, Redpoint, Greylock, GV, Menlo, USV, Thrive, Accel, Felicis, General Catalyst, First Round

DISAMBIGUATION: Benchmark≠Benchmark International/Electronics, GV≠NYSE:GV, Thrive≠Thrive IT/Global, Sequoia=US only (not HongShan/Peak XV)

COMPANY NAME: Must be actual company, NOT article titles like "America's Construction Problem". If can't identify → startup_name="<UNKNOWN>", is_new_announcement=False

URLS: Only extract if EXPLICITLY mentioned. Never guess/construct URLs. Set to null if not in article.

DATE: Extract from article (explicit or relative dates). If not found → round_date=null

NEW ANNOUNCEMENT CHECK
TRUE (is_new_announcement=True): "X raises $50M Series A", "Y closes seed round" - funding IS the topic
FALSE: "X, backed by Sequoia, launches product..." - funding is background context

Set announcement_evidence (if true) or announcement_rejection_reason (if false).

EXAMPLE 1: "BuildOps, backed by Founders Fund, expands integration"
→ is_new_announcement=False, reason="backed by is historical, not new funding"

EXAMPLE 2: "Acme raises $50M Series B led by Sequoia"
→ is_new_announcement=True, evidence="raises $50M Series B led by Sequoia"

Return ONE deal per article (the primary/new announcement, not historical mentions).
Output valid JSON matching the schema."""

# Initialize the cached system message with the prompt text
CACHED_SYSTEM_MESSAGE[0]["text"] = SYSTEM_PROMPT


# =============================================================================
# TOKEN USAGE LOGGING
# =============================================================================

# Haiku pricing (December 2024)
HAIKU_INPUT_COST_PER_MILLION = 0.25  # $0.25 per 1M input tokens
HAIKU_OUTPUT_COST_PER_MILLION = 1.25  # $1.25 per 1M output tokens
HAIKU_CACHE_READ_COST_PER_MILLION = 0.025  # $0.025 per 1M cache read tokens
HAIKU_CACHE_WRITE_COST_PER_MILLION = 0.3125  # $0.3125 per 1M cache write tokens


def set_extraction_context(source_name: str, scan_id: Optional[str] = None) -> None:
    """Set the current extraction context for token logging.

    Call this before starting extraction to track which source is being processed.

    Args:
        source_name: Name of the source (e.g., "brave_search", "techcrunch", "a16z")
        scan_id: Optional job ID for grouping (e.g., scan timestamp)
    """
    global _current_source_name, _current_scan_id
    _current_source_name = source_name
    _current_scan_id = scan_id


def clear_extraction_context() -> None:
    """Clear the extraction context after processing."""
    global _current_source_name, _current_scan_id
    _current_source_name = None
    _current_scan_id = None


def _calculate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0
) -> float:
    """Calculate estimated cost in USD for token usage."""
    cost = (
        (input_tokens * HAIKU_INPUT_COST_PER_MILLION / 1_000_000) +
        (output_tokens * HAIKU_OUTPUT_COST_PER_MILLION / 1_000_000) +
        (cache_read_tokens * HAIKU_CACHE_READ_COST_PER_MILLION / 1_000_000) +
        (cache_write_tokens * HAIKU_CACHE_WRITE_COST_PER_MILLION / 1_000_000)
    )
    return round(cost, 6)


async def log_token_usage(
    input_tokens: int,
    output_tokens: int,
    source_name: Optional[str] = None,
    scan_id: Optional[str] = None,
    article_url: Optional[str] = None,
    model: str = "claude-3-5-haiku-20241022",
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0
) -> None:
    """Log token usage to database for cost tracking.

    Args:
        input_tokens: Number of input tokens used
        output_tokens: Number of output tokens generated
        source_name: Source identifier (uses context if not provided)
        scan_id: Job ID for grouping (uses context if not provided)
        article_url: URL of the article being processed
        model: Model name used
        cache_read_tokens: Tokens read from cache
        cache_write_tokens: Tokens written to cache
    """
    # Use context values if not explicitly provided
    source = source_name or _current_source_name or "unknown"
    job_id = scan_id or _current_scan_id

    cost = _calculate_cost(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)

    try:
        # Import here to avoid circular imports
        from ..archivist.database import get_session
        from ..archivist.models import TokenUsage

        async with get_session() as session:
            usage = TokenUsage(
                timestamp=datetime.now(timezone.utc),
                source_name=source,
                scan_id=job_id,
                article_url=article_url[:2000] if article_url and len(article_url) > 2000 else article_url,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                model=model,
                estimated_cost_usd=cost
            )
            session.add(usage)
            # Session commits on exit

        logger.debug(
            f"Token usage logged: source={source}, input={input_tokens}, "
            f"output={output_tokens}, cost=${cost:.4f}"
        )
    except Exception as e:
        # Don't fail extraction if logging fails
        logger.warning(f"Failed to log token usage: {e}")


# Keywords that indicate funding content (for early exit optimization)
# Split into strong (must have) and supporting (help confirm) keywords
STRONG_FUNDING_KEYWORDS = [
    "raised", "funding", "series", "seed round", "led by", "investment round",
    "secures", "closes round", "announces funding"
]
AMOUNT_KEYWORDS = ["million", "billion", "$"]
SUPPORTING_KEYWORDS = [
    "investment", "round", "venture", "capital", "investor",
    "announces", "valuation", "pre-seed", "series a", "series b", "series c"
]


def is_likely_funding_content(text: str) -> bool:
    """
    Quick check if text is likely about funding.

    Used for early exit optimization - skip Claude call if text
    doesn't contain funding-related keywords.

    TIGHTENED: Requires either:
    - 1 strong keyword + 1 amount keyword (e.g., "raised $50 million")
    - 3+ total keywords (broader coverage)
    """
    text_lower = text.lower()

    # Check for strong keyword + amount (most reliable signal)
    has_strong = any(kw in text_lower for kw in STRONG_FUNDING_KEYWORDS)
    has_amount = any(kw in text_lower for kw in AMOUNT_KEYWORDS)
    if has_strong and has_amount:
        return True

    # Fallback: require 3+ keywords total
    all_keywords = STRONG_FUNDING_KEYWORDS + AMOUNT_KEYWORDS + SUPPORTING_KEYWORDS
    matches = sum(1 for kw in all_keywords if kw in text_lower)
    return matches >= 3


def _compute_content_hash(text: str) -> str:
    """Compute a hash of the article content for deduplication.

    FIX: Extended from 16 to 32 chars (128-bit) for lower collision risk.
    """
    # Normalize whitespace and lowercase for consistent hashing
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]  # 128-bit


async def _is_duplicate_content(text: str) -> bool:
    """
    Check if we've already processed this content in this session.

    Returns True if duplicate (should skip), False if new.

    FIX: Made async with lock for thread-safety in concurrent extraction.
    """
    content_hash = _compute_content_hash(text)
    text_preview = text[:100].replace('\n', ' ') if text else "(empty)"

    async with _content_hash_lock:
        if content_hash in _content_hash_cache:
            logger.warning(
                f"Duplicate content detected - hash={content_hash}, "
                f"cache_size={len(_content_hash_cache)}, "
                f"text_preview='{text_preview}...'"
            )
            return True

        logger.debug(f"New content - hash={content_hash}, adding to cache (size={len(_content_hash_cache)})")
        _content_hash_cache.add(content_hash)
        return False


def clear_content_hash_cache() -> None:
    """Clear the content hash cache. Call between scrape runs."""
    global _content_hash_cache
    cache_size = len(_content_hash_cache)
    _content_hash_cache.clear()
    logger.info(f"Content hash cache cleared (had {cache_size} entries)")


# Keywords that indicate crypto/blockchain content (not Enterprise AI)
CRYPTO_KEYWORDS = [
    "crypto", "blockchain", "web3", "defi", "nft", "token",
    "protocol", "layer 2", "l2", "dao", "smart contract",
    "ethereum", "bitcoin", "solana", "polygon", "avalanche",
    "decentralized", "on-chain", "tokenomics", "staking",
]

# Company name patterns that indicate crypto (for stricter detection)
# "0x" prefix is common in crypto (0xMiden, 0xSplits, 0x Protocol)
CRYPTO_COMPANY_PATTERNS = ["protocol", "chain", "token", "dao", "coin", "web3", "0x"]


def is_likely_crypto_article(text: str) -> bool:
    """
    PRE-CHECK: Detect if article is crypto-focused BEFORE calling Claude.

    Saves tokens by skipping crypto articles that would be rejected anyway.
    Only triggers on HIGH crypto signal (4+ keywords) to avoid false positives.
    """
    text_lower = text.lower()
    crypto_count = sum(1 for kw in CRYPTO_KEYWORDS if kw in text_lower)

    # High threshold to avoid rejecting enterprise blockchain companies
    if crypto_count >= 4:
        logger.debug(f"Pre-check: Skipping crypto article ({crypto_count} keywords)")
        return True

    return False


def _is_crypto_deal(deal: DealExtraction, article_text: str) -> bool:
    """
    Detect if deal is crypto-related (should NOT be Enterprise AI).

    Called as post-extraction validation to catch crypto deals that the LLM
    might have incorrectly classified as Enterprise AI.

    Returns:
        True if deal is crypto-related, False otherwise
    """
    text_lower = article_text.lower()
    name_lower = deal.startup_name.lower() if deal.startup_name else ""

    # Check 1: Company name contains crypto patterns
    if any(pattern in name_lower for pattern in CRYPTO_COMPANY_PATTERNS):
        logger.debug(f"Crypto detected by company name: {deal.startup_name}")
        return True

    # Check 2: Article is dominated by crypto keywords (3+ = likely crypto)
    crypto_count = sum(1 for kw in CRYPTO_KEYWORDS if kw in text_lower)
    if crypto_count >= 3:
        logger.debug(f"Crypto detected by keyword density ({crypto_count}): {deal.startup_name}")
        return True

    # Check 3: Lead investor has "crypto" in name (e.g., "a16z crypto")
    if deal.tracked_fund_name and "crypto" in deal.tracked_fund_name.lower():
        logger.debug(f"Crypto detected by investor name: {deal.tracked_fund_name}")
        return True

    # Check 4: Check lead investors in the extraction
    for investor in deal.lead_investors:
        if "crypto" in investor.name.lower():
            logger.debug(f"Crypto detected by lead investor: {investor.name}")
            return True

    return False


# Patterns that indicate text is an article title, not a company name
# FIX #34: Made patterns more specific to avoid rejecting valid company names
ARTICLE_TITLE_PATTERNS = [
    r"'s\s+(problem|challenge|crisis|issue|opportunity|dilemma|future|guide)",
    # FIX #34: Only reject question words, not "the"/"a" which appear in valid names
    # Valid: "The Trade Desk", "A Cloud Guru" - these should NOT be rejected
    r"^(how|why|what|when)\s+",
    # "the" + article-specific words only (not generic "the company")
    r"^the\s+(problem|future|guide|art|science|rise|fall|state|end|best|worst|top|only|real|truth|power|secret|key|way)\b",
    # "a" + article-specific words only (not generic "a startup")
    r"^a\s+(guide|look|deep|new|better|simple|complete|brief|quick|closer|comprehensive)\b",
    r"(problem|challenge|crisis|issue|opportunity|future|guide|revolution)$",
    r"^\d+\s+(ways?|tips|secrets|lessons|habits|mistakes)",
    r"^(building|creating|scaling|growing|launching)\s+",
]


def _looks_like_article_title(text: str) -> bool:
    """
    Check if text looks like an article title, not a company name.

    Used to catch false positives where LLM extracts headline as company name.
    E.g., "America's Construction Problem" should be rejected.
    """
    if not text or len(text) < 3:
        return True

    text_lower = text.lower().strip()

    # Check regex patterns
    for pattern in ARTICLE_TITLE_PATTERNS:
        if re.search(pattern, text_lower):
            return True

    # Company names are usually 1-4 words, titles are often longer
    word_count = len(text.split())
    if word_count > 5:
        return True

    # Check for known invalid patterns
    invalid_substrings = ["problem", "challenge", "crisis", "issue", "'s construction", "'s future"]
    if any(sub in text_lower for sub in invalid_substrings):
        return True

    return False


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
    skip_funding_check: bool = False,
    skip_dedup_check: bool = False
) -> Optional[DealExtraction]:
    """
    Extract a single funding deal from article text.

    OPTIMIZED:
    - Uses prompt caching for system message (20% token savings)
    - Early exit for non-funding content
    - Content hash deduplication to avoid duplicate API calls

    Args:
        article_text: The article content to analyze
        source_url: URL of the source article
        fund_context: Optional fund-specific extraction context
        skip_funding_check: If True, skip the funding keyword check
        skip_dedup_check: If True, skip the duplicate content check

    Returns:
        DealExtraction with structured deal information, or None if duplicate
    """
    # Content hash dedup: Skip if we've already processed identical content in this run
    # Cache is cleared ONCE at job start in scheduler/jobs.py for cross-source dedup
    if not skip_dedup_check and await _is_duplicate_content(article_text):
        logger.info(f"Skipping duplicate content for: {source_url}")
        return None

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
            # NEW: Mark as not a new announcement
            is_new_announcement=False,
            announcement_evidence=None,
            announcement_rejection_reason="No funding keywords detected - not a funding article",
            reasoning=ChainOfThought(
                verb_analysis="No funding keywords detected",
                lead_signals=[],
                participation_signals=[],
                ambiguity_flags=["Text does not appear to be about funding"],
                final_reasoning="Skipped extraction - no funding content detected"
            )
        )

    # PRE-CHECK: Skip crypto-heavy articles before calling Claude (saves tokens)
    if not skip_funding_check and is_likely_crypto_article(article_text):
        from .schemas import ChainOfThought
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
            is_new_announcement=False,
            announcement_evidence=None,
            announcement_rejection_reason="Crypto/blockchain article - not Enterprise AI",
            reasoning=ChainOfThought(
                verb_analysis="Crypto keywords detected",
                lead_signals=[],
                participation_signals=[],
                ambiguity_flags=["Article appears to be about crypto/blockchain"],
                final_reasoning="Skipped extraction - crypto article detected"
            )
        )

    prompt = build_extraction_prompt(article_text, fund_context, source_url)

    # Use pre-built cached system message (optimization: created once at module load)
    # FIX: Add retry logic and error handling for API calls
    last_error = None
    article_len = len(article_text)

    for attempt in range(MAX_RETRIES + 1):
        try:
            # Use create_with_completion to get both parsed model AND raw response with usage
            response, completion = client.messages.create_with_completion(
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                system=CACHED_SYSTEM_MESSAGE,
                messages=[{"role": "user", "content": prompt}],
                response_model=DealExtraction,
            )

            # Log token usage from the completion
            if completion and hasattr(completion, 'usage'):
                usage = completion.usage
                # Extract cache tokens if available (Anthropic-specific)
                cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
                cache_write = getattr(usage, 'cache_creation_input_tokens', 0) or 0
                await log_token_usage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    article_url=source_url,
                    model=settings.llm_model,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write
                )

            # Post-process: verify tracked fund identification
            response = _verify_tracked_fund(response)

            # Post-process: detect and reclassify crypto deals
            if response and response.is_enterprise_ai and _is_crypto_deal(response, article_text):
                logger.info(
                    f"Reclassifying {response.startup_name} as NOT_ENTERPRISE: "
                    f"crypto/blockchain detected"
                )
                response.is_enterprise_ai = False
                response.enterprise_category = EnterpriseCategory.NOT_ENTERPRISE

            # Post-process: validate company name isn't an article title
            if response and _looks_like_article_title(response.startup_name):
                logger.warning(
                    f"Rejecting article title as company name: '{response.startup_name}' "
                    f"from {source_url}"
                )
                response.is_new_announcement = False
                response.announcement_rejection_reason = (
                    f"Company name looks like article title: {response.startup_name}"
                )

            return response

        except APITimeoutError as e:
            last_error = e
            backoff = 2 ** attempt
            logger.warning(
                f"Claude API timeout (attempt {attempt + 1}/{MAX_RETRIES + 1}, "
                f"article_len={article_len}, backoff={backoff}s): {source_url}"
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
            continue

        except RateLimitError as e:
            last_error = e
            backoff = 10 * (attempt + 1)  # 10s, 20s, 30s, 40s
            logger.warning(
                f"Claude API rate limit (attempt {attempt + 1}/{MAX_RETRIES + 1}, "
                f"backoff={backoff}s): {e}"
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
            continue

        except APIError as e:
            last_error = e
            logger.error(f"Claude API error (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}")
            if attempt < MAX_RETRIES and e.status_code >= 500:
                await asyncio.sleep(2 ** attempt)  # Retry server errors
                continue
            break  # Don't retry client errors (4xx)

        except InstructorRetryException as e:
            # Instructor validation failed - log details to diagnose
            logger.error(
                f"Instructor validation failed for {source_url}: {e}\n"
                f"  Last attempt messages: {getattr(e, 'last_completion', 'N/A')}\n"
                f"  Validation errors: {getattr(e, 'errors', 'N/A')}"
            )
            # This is a validation error, not transient - don't retry
            return None

        except Exception as e:
            logger.error(f"Unexpected error during extraction for {source_url}: {type(e).__name__}: {e}")
            return None

    # All retries exhausted
    logger.error(
        f"Extraction failed after {MAX_RETRIES + 1} attempts for {source_url} "
        f"(article_len={article_len}): {type(last_error).__name__}: {last_error}"
    )
    return None


async def extract_deal_batch(
    articles: List[dict],
    max_concurrent: int = 5
) -> List[Optional[DealExtraction]]:
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

    # Handle any exceptions - log errors and skip failed extractions
    processed_results = []
    error_count = 0
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            # Log the error and skip this extraction (return None)
            source_url = articles[i].get("source_url", "unknown")
            logger.error(f"Extraction failed for article {i} ({source_url}): {result}")
            processed_results.append(None)  # Caller should filter out None values
            error_count += 1
        else:
            processed_results.append(result)

    # FIX #22: Warn when failure rate exceeds 50% threshold
    total = len(articles)
    if total > 0 and error_count > 0:
        failure_rate = error_count / total
        if failure_rate > 0.5:
            logger.warning(
                f"High extraction failure rate: {error_count}/{total} ({failure_rate:.0%}) "
                f"articles failed - check Claude API status or rate limits"
            )

    return processed_results


async def extract_article(
    article_text: str,
    source_url: str = "",
    title: str = "",
    fund_context: Optional[FundConfig] = None
) -> Optional[ArticleAnalysis]:
    """
    Analyze a full article that may contain multiple deals.

    Args:
        article_text: The article content to analyze
        source_url: URL of the source article
        title: Article title
        fund_context: Optional fund-specific extraction context

    Returns:
        ArticleAnalysis with all deals found, or None if extraction failed
    """
    prompt = build_extraction_prompt(article_text, fund_context, source_url)

    # Use pre-built cached system message (optimization: created once at module load)
    last_error = None
    article_len = len(article_text)

    for attempt in range(MAX_RETRIES + 1):
        try:
            # Use create_with_completion to get both parsed model AND raw response with usage
            response, completion = client.messages.create_with_completion(
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                system=CACHED_SYSTEM_MESSAGE,
                messages=[{"role": "user", "content": prompt}],
                response_model=ArticleAnalysis,
            )

            # Log token usage from the completion
            if completion and hasattr(completion, 'usage'):
                usage = completion.usage
                cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
                cache_write = getattr(usage, 'cache_creation_input_tokens', 0) or 0
                await log_token_usage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    article_url=source_url,
                    model=settings.llm_model,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write
                )

            # Post-process each deal
            for deal in response.deals:
                _verify_tracked_fund(deal)

            return response

        except APITimeoutError as e:
            last_error = e
            backoff = 2 ** attempt
            logger.warning(
                f"Claude API timeout (attempt {attempt + 1}/{MAX_RETRIES + 1}, "
                f"article_len={article_len}, backoff={backoff}s): {source_url}"
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
            continue

        except RateLimitError as e:
            last_error = e
            backoff = 10 * (attempt + 1)
            logger.warning(
                f"Claude API rate limit (attempt {attempt + 1}/{MAX_RETRIES + 1}, "
                f"backoff={backoff}s): {e}"
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
            continue

        except APIError as e:
            last_error = e
            logger.error(f"Claude API error (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}")
            if attempt < MAX_RETRIES and e.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                continue
            break

        except Exception as e:
            logger.error(f"Unexpected error during article extraction for {source_url}: {type(e).__name__}: {e}")
            return None

    logger.error(
        f"Article extraction failed after {MAX_RETRIES + 1} attempts for {source_url} "
        f"(article_len={article_len}): {type(last_error).__name__}: {last_error}"
    )
    return None


def _has_lead_language(snippet: str) -> bool:
    """
    Check if a verification snippet contains lead investor language.

    Returns True if snippet proves lead status, False otherwise.
    """
    import re

    if not snippet:
        return False

    snippet_lower = snippet.lower()

    # Strong lead phrases (require word boundaries to avoid false positives)
    lead_patterns = [
        r'\bled\s+by\b',           # 'led by'
        r'\bled\s+the\b',          # 'led the round'
        r'\bleads?\s+',            # 'lead ', 'leads '
        r'\bleading\b',            # 'leading'
        r'\bco-led\b',             # 'co-led'
        r'\bco-leads?\b',          # 'co-lead'
        r'\bheaded\s+by\b',        # 'headed by'
        r'\blead\s+investor\b',    # 'lead investor'
        r'\bspearheaded\b',        # 'spearheaded'
    ]

    for pattern in lead_patterns:
        if re.search(pattern, snippet_lower):
            return True

    return False


def _verify_tracked_fund(deal: DealExtraction) -> DealExtraction:
    """
    Post-process to verify tracked fund identification.

    STRICT VERIFICATION:
    - Only sets tracked_fund_is_lead=True if:
      1. A tracked fund is in lead_investors with CONFIRMED_LEAD role
      2. The verification_snippet contains actual lead language
    - Downgrades to participant if snippet is missing or invalid

    This prevents false positives where the LLM claims lead status
    without proper evidence.
    """
    # Lazy import to avoid circular dependency
    from ..harvester.fund_matcher import match_fund_name

    # Track all matched funds for logging
    matched_leads = []
    matched_participants = []

    # Validate verification snippet FIRST - STRICT CHECK
    has_valid_snippet = _has_lead_language(deal.verification_snippet)

    # CRITICAL FIX: If snippet exists but lacks lead language, REJECT lead claim immediately
    # This prevents false positives where LLM says "confirmed_lead" without proof
    if deal.verification_snippet and not has_valid_snippet:
        logger.warning(
            f"REJECTING lead claim for {deal.startup_name}: "
            f"snippet '{deal.verification_snippet[:80]}...' lacks lead language"
        )
        # Override LLM's incorrect assignment - NOT a lead
        deal.tracked_fund_is_lead = False
        deal.tracked_fund_role = LeadStatus.PARTICIPANT
        # Continue processing to identify which fund is involved, but as participant

    # Also reject if NO snippet provided but deal claims lead status
    if deal.tracked_fund_is_lead and not deal.verification_snippet:
        logger.warning(
            f"REJECTING lead claim for {deal.startup_name}: "
            f"no verification_snippet provided"
        )
        deal.tracked_fund_is_lead = False
        deal.tracked_fund_role = LeadStatus.PARTICIPANT

    # Check ALL lead investors against our fund registry
    for investor in deal.lead_investors:
        matched_fund = match_fund_name(investor.name)
        # FIX: Validate matched_fund exists in FUND_REGISTRY before using
        if matched_fund and matched_fund in FUND_REGISTRY:
            investor.is_tracked_fund = True
            matched_leads.append((matched_fund, investor))

            # Set deal-level fields for the FIRST matching lead
            # STRICT: Require CONFIRMED_LEAD role AND valid snippet
            if not deal.tracked_fund_is_lead:
                if investor.role == LeadStatus.CONFIRMED_LEAD and has_valid_snippet:
                    deal.tracked_fund_is_lead = True
                    deal.tracked_fund_name = FUND_REGISTRY[matched_fund].name
                    deal.tracked_fund_role = investor.role
                    deal.tracked_fund_partner = investor.partner_name
                elif investor.role == LeadStatus.LIKELY_LEAD and has_valid_snippet:
                    # Accept LIKELY_LEAD only with valid snippet
                    deal.tracked_fund_is_lead = True
                    deal.tracked_fund_name = FUND_REGISTRY[matched_fund].name
                    deal.tracked_fund_role = investor.role
                    deal.tracked_fund_partner = investor.partner_name
                else:
                    # Downgrade: no snippet or invalid snippet
                    logger.info(
                        f"Downgrading {deal.startup_name} lead claim: "
                        f"role={investor.role.value}, has_valid_snippet={has_valid_snippet}"
                    )
                    # Set as tracked fund but NOT as lead
                    if not deal.tracked_fund_name:
                        deal.tracked_fund_name = FUND_REGISTRY[matched_fund].name
                        deal.tracked_fund_role = LeadStatus.PARTICIPANT
                        deal.tracked_fund_partner = investor.partner_name
        elif matched_fund:
            # Fund matched but not in registry - log warning
            logger.warning(f"Matched fund '{matched_fund}' not in FUND_REGISTRY")

    # Log if multiple tracked funds co-led
    if len(matched_leads) > 1:
        # FIX: Validate each fund in FUND_REGISTRY before accessing
        fund_names = [
            FUND_REGISTRY[f].name for f, _ in matched_leads
            if f in FUND_REGISTRY
        ]
        logger.info(f"Multiple tracked funds co-led deal for {deal.startup_name}: {fund_names}")

    # Also check ALL participating investors
    for investor in deal.participating_investors:
        matched_fund = match_fund_name(investor.name)
        # FIX: Validate matched_fund exists in FUND_REGISTRY before using
        if matched_fund and matched_fund in FUND_REGISTRY:
            investor.is_tracked_fund = True
            matched_participants.append((matched_fund, investor))
            # Only set deal-level fields if no lead was found
            if not deal.tracked_fund_is_lead and not deal.tracked_fund_name:
                deal.tracked_fund_name = FUND_REGISTRY[matched_fund].name
                deal.tracked_fund_role = LeadStatus.PARTICIPANT
                deal.tracked_fund_partner = investor.partner_name
        elif matched_fund:
            # Fund matched but not in registry - log warning
            logger.warning(f"Matched fund '{matched_fund}' not in FUND_REGISTRY")

    return deal


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

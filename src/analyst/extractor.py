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
# FIX (2026-01): Now stores (hash, length) to prefer longer content
_content_hash_cache: dict[str, int] = {}  # hash -> content length
_content_hash_lock = asyncio.Lock()  # FIX: Async-safe cache access

# Minimum content length improvement to replace cached version
# If new content is 2x longer, it's worth re-processing
MIN_LENGTH_IMPROVEMENT_RATIO = 2.0

from .schemas import DealExtraction, ArticleAnalysis, LeadStatus, RoundType, EnterpriseCategory
from ..config.settings import settings
from ..config.funds import FUND_REGISTRY, FundConfig, ScraperType
# Note: match_fund_name is imported lazily in _verify_tracked_fund to avoid circular import

# External-only funds that need more lenient processing
# These funds don't have working website scrapers, so deals come from news sources
# with shorter/less reliable content
EXTERNAL_ONLY_FUNDS = frozenset({
    "thrive", "benchmark", "first_round",  # ScraperType.EXTERNAL
    "greylock", "gv", "redpoint", "founders_fund",  # Low coverage from website scrapers
})

# Source names that are external (used for auto-detection)
EXTERNAL_SOURCE_NAMES = frozenset({
    "google_news", "brave_search", "sec_edgar", "news_aggregator",
})

# Lower confidence threshold for external sources
# Standard is 0.5, but external sources often have less context
EXTERNAL_SOURCE_CONFIDENCE_THRESHOLD = 0.35

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
3. AI classification (enterprise, consumer, or not AI)
4. If any TRACKED FUND led the round

AI CLASSIFICATION (set is_ai_deal=True for ALL AI companies)

ENTERPRISE AI (is_enterprise_ai=True, is_ai_deal=True):
- infrastructure: LLMOps, MLOps, vector DBs, AI chips
- security: AI-powered cybersecurity, threat detection
- vertical_saas: AI for Healthcare/Finance/Legal/HR (B2B)
- agentic: Workflow automation, AI agents for business
- data_intelligence: Enterprise search, analytics

CONSUMER AI (is_enterprise_ai=False, is_ai_deal=True):
- consumer_ai: Consumer-facing AI apps, personal assistants, AI photo editors
- gaming_ai: AI for games, game engines, AI-powered NPCs
- social_ai: AI for social/dating apps, AI content creation for consumers

NOT AI (is_enterprise_ai=False, is_ai_deal=False):
- not_ai: Crypto/blockchain/Web3/DeFi/NFT, non-AI startups

LEAD VERIFICATION (STRICT)
LEAD phrases: "led by", "led the", "leads", "co-led", "headed by", "spearheaded"
NOT LEAD: "backed by", "participated in", "invested in", "joined"

CRITICAL: Only set tracked_fund_is_lead=True if verification_snippet contains ACTUAL lead language.
If no lead phrase exists → put in participating_investors with role=participant, NOT lead_investors.

FOUNDERS (EXTRACT ALL MENTIONED)
- CEO/Founder (REQUIRED if mentioned in article)
- Co-founders, CTO, COO, etc. (if mentioned)
- For each founder include: name (full name), title (CEO, CTO, Co-founder, etc.)
- LinkedIn URL: ONLY extract if explicitly mentioned in article text. Never guess/construct URLs.
- If no founders mentioned → leave founders list empty

TRACKED FUNDS: Founders Fund, Benchmark, Sequoia, Khosla, Index, a16z, Insight Partners, Bessemer, Redpoint, Greylock, GV, Menlo, USV, Thrive, Accel, Felicis, General Catalyst, First Round

DISAMBIGUATION: Benchmark≠Benchmark International/Electronics, GV≠NYSE:GV, Thrive≠Thrive IT/Global, Sequoia=US only (not HongShan/Peak XV)

COMPANY NAME: Must be actual company, NOT article titles like "America's Construction Problem". If can't identify → startup_name="<UNKNOWN>", is_new_announcement=False

URLS: Only extract if EXPLICITLY mentioned. Never guess/construct URLs. Set to null if not in article.

DATE EXTRACTION (IMPORTANT - affects data accuracy):
- Extract the ANNOUNCEMENT/CLOSING date of the funding round, NOT the article's publication date
- Look for phrases like: "announced today", "closed on", "raised on [date]", "as of [date]"
- Relative dates: "yesterday", "last week", "earlier this month" → calculate actual date
- If multiple dates in article, prefer the one closest to announcement language
- If no date can be determined → round_date=null (don't guess)
- Reject dates >1 year old (likely historical mention, not new announcement)

ROUND TYPES: pre_seed, seed, seed_plus_series_a (when Seed AND Series A announced together as single round), series_a, series_b, series_c, series_d, series_e_plus, growth, debt, unknown

NEW ANNOUNCEMENT CHECK (CRITICAL - prevents false positives)
TRUE (is_new_announcement=True): Funding IS the article's primary topic
FALSE (is_new_announcement=False): Funding mentioned as background/context

ALWAYS SET FALSE FOR:
- Competitor comparisons: "X competes with Y, which raised $48M..." → Y is background
- Company profiles: "X, backed by Sequoia, launches..." → historical funding
- Partnership/product news mentioning past funding
- Any company that is NOT the article's primary subject

ONLY SET TRUE IF:
- Article headline/title is about THIS company's funding
- Direct announcement: "X raises $50M", "X closes Series A"
- Press release FROM the company raising funds

Set announcement_evidence (if true) or announcement_rejection_reason (if false).

EXAMPLE 1: "BuildOps, backed by Founders Fund, expands integration"
→ is_new_announcement=False, reason="backed by is historical, not new funding"

EXAMPLE 2: "Acme raises $50M Series B led by Sequoia"
→ is_new_announcement=True, evidence="raises $50M Series B led by Sequoia"

EXAMPLE 3: "ResolveAI raises $30M Series A; competes with Traversal, which raised $48M"
→ Extract ResolveAI (the PRIMARY subject raising funds)
→ IGNORE Traversal (background mention, not primary subject)

HEADLINE-ONLY CONTENT:
Some articles may only contain a headline with minimal context. Extract what you can:
- "Thrive Capital Leads $34M Investment Round" → tracked_fund=Thrive, amount=34M, is_lead=True
- "Acme raises $50M in Series B funding" → company=Acme, amount=50M, round=series_b
- Set confidence_score=0.4-0.6 for headline-only (less context = lower confidence)
- If company name is missing from headline → startup_name="<UNKNOWN>", is_new_announcement=False
- Headlines with clear lead language ("leads", "led") ARE sufficient for is_lead=True

Return ONE deal per article (the primary/new announcement, not historical mentions).
Output valid JSON matching the schema."""

# Initialize the cached system message with the prompt text
CACHED_SYSTEM_MESSAGE[0]["text"] = SYSTEM_PROMPT


# =============================================================================
# TOKEN USAGE LOGGING
# =============================================================================

# Claude 3.5 Haiku pricing (December 2024 - after 20% price drop)
# See: https://simonwillison.net/2024/Dec/5/claude-35-haiku-price-drops-by-20/
HAIKU_INPUT_COST_PER_MILLION = 0.80  # $0.80 per 1M input tokens
HAIKU_OUTPUT_COST_PER_MILLION = 4.00  # $4.00 per 1M output tokens
HAIKU_CACHE_READ_COST_PER_MILLION = 0.08  # $0.08 per 1M cache read tokens (10% of input)
HAIKU_CACHE_WRITE_COST_PER_MILLION = 1.00  # $1.00 per 1M cache write tokens (125% of input)


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

    # DEBUG: Log detailed attribution info when falling back to unknown
    if source == "unknown":
        logger.warning(
            f"[TOKEN ATTRIBUTION] Falling back to 'unknown' - "
            f"source_name={repr(source_name)}, "
            f"_current_source_name={repr(_current_source_name)}, "
            f"article_url={article_url[:80] if article_url else 'None'}..."
        )

    cost = _calculate_cost(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
    logger.info(f"[TOKEN DEBUG] log_token_usage called: source={source}, in={input_tokens}, out={output_tokens}")

    try:
        # Import here to avoid circular imports
        from ..archivist.database import get_session
        from ..archivist.models import TokenUsage

        logger.info(f"[TOKEN DEBUG] About to create TokenUsage object")
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
            logger.info(f"[TOKEN DEBUG] TokenUsage created, adding to session")
            session.add(usage)
            logger.info(f"[TOKEN DEBUG] Committing...")
            await session.commit()  # Explicit commit to ensure data is saved
            logger.info(f"[TOKEN DEBUG] Commit complete!")

        logger.info(
            f"Token usage saved: source={source}, input={input_tokens}, "
            f"output={output_tokens}, cost=${cost:.4f}"
        )
    except Exception as e:
        # Don't fail extraction if logging fails, but log details for debugging
        logger.error(f"Failed to log token usage: {e}", exc_info=True)
        logger.error(
            f"  Token logging params: source={source}, input={input_tokens}, "
            f"output={output_tokens}, cache_read={cache_read_tokens}, "
            f"cache_write={cache_write_tokens}, cost=${cost:.4f}"
        )


# Keywords that indicate funding content (for early exit optimization)
# Split into strong (must have) and supporting (help confirm) keywords
# FIX (2026-01): Added "leads" for headline-only content like "Thrive Capital Leads $34M"
STRONG_FUNDING_KEYWORDS = [
    "raised", "raises", "funding", "series", "seed round", "led by", "leads",
    "investment round", "secures", "closes round", "announces funding"
]
AMOUNT_KEYWORDS = ["million", "billion", "$", "mn", "m funding"]  # "mn" = Indian format
SUPPORTING_KEYWORDS = [
    "investment", "round", "venture", "capital", "investor",
    "announces", "valuation", "pre-seed", "series a", "series b", "series c"
]

# FIX: Pre-compute combined keyword list at module level (was created on every call)
ALL_FUNDING_KEYWORDS = STRONG_FUNDING_KEYWORDS + AMOUNT_KEYWORDS + SUPPORTING_KEYWORDS


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
    # FIX: Use pre-computed module-level constant (was creating list on every call)
    matches = sum(1 for kw in ALL_FUNDING_KEYWORDS if kw in text_lower)
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

    Returns True if duplicate (should skip), False if new or if this version
    is significantly longer than the cached version.

    FIX (2026-01): Now prefers longer content. If a headline-only version was
    processed first, a full article version will still be processed. This fixes
    the issue where Google News headlines blocked Brave Search full articles.

    FIX: Made async with lock for thread-safety in concurrent extraction.
    """
    content_hash = _compute_content_hash(text)
    text_len = len(text) if text else 0
    text_preview = text[:100].replace('\n', ' ') if text else "(empty)"

    async with _content_hash_lock:
        if content_hash in _content_hash_cache:
            cached_len = _content_hash_cache[content_hash]

            # If new content is significantly longer, process it anyway
            # This catches cases where headline was processed before full article
            if text_len > cached_len * MIN_LENGTH_IMPROVEMENT_RATIO:
                logger.info(
                    f"Replacing short cache entry with longer content - "
                    f"hash={content_hash}, cached_len={cached_len}, new_len={text_len}"
                )
                _content_hash_cache[content_hash] = text_len
                return False  # Process this longer version

            logger.debug(
                f"Duplicate content detected - hash={content_hash}, "
                f"cache_size={len(_content_hash_cache)}, len={text_len}, "
                f"text_preview='{text_preview}...'"
            )
            return True

        logger.debug(f"New content - hash={content_hash}, len={text_len}, adding to cache (size={len(_content_hash_cache)})")
        _content_hash_cache[content_hash] = text_len
        return False


def clear_content_hash_cache() -> None:
    """Clear the content hash cache. Call between scrape runs."""
    global _content_hash_cache
    cache_size = len(_content_hash_cache)
    _content_hash_cache = {}
    logger.info(f"Content hash cache cleared (had {cache_size} entries)")


# Keywords that indicate crypto/blockchain content (not Enterprise AI)
# OPTIMIZED: Added more keywords to catch crypto articles earlier (saves ~15% tokens)
CRYPTO_KEYWORDS = [
    # Core crypto terms
    "crypto", "blockchain", "web3", "defi", "nft", "token",
    "protocol", "layer 2", "l2", "dao", "smart contract",
    # Specific chains
    "ethereum", "bitcoin", "solana", "polygon", "avalanche",
    "arbitrum", "optimism", "base chain", "cosmos", "polkadot",
    # DeFi mechanics
    "decentralized", "on-chain", "tokenomics", "staking",
    "yield farming", "liquidity pool", "airdrop", "bridge",
    "validator", "consensus", "gas fee", "mint",
    # Crypto-specific terms
    "wallet connect", "metamask", "dex", "amm", "tvl",
]

# Company name patterns that indicate crypto (for stricter detection)
# "0x" prefix is common in crypto (0xMiden, 0xSplits, 0x Protocol)
CRYPTO_COMPANY_PATTERNS = [
    "protocol", "chain", "token", "dao", "coin", "web3", "0x",
    "finance",  # e.g., "Compound Finance", "Aave Finance"
    "swap",     # e.g., "Uniswap", "Sushiswap"
    "bridge",   # e.g., "Wormhole Bridge"
    "vault",    # e.g., "Yearn Vault"
]

# URL patterns that indicate crypto websites
CRYPTO_URL_PATTERNS = [
    ".eth", ".crypto", ".sol",  # Crypto TLDs
    "crypto", "defi", "nft", "web3",  # In domain name
    "blockchain", "token", "chain",
]

# Keywords that indicate consumer AI (NOT Enterprise AI)
# FIX: Detect consumer AI to prevent false Enterprise AI classification
CONSUMER_AI_KEYWORDS = [
    # Dating/social apps
    "dating app", "dating platform", "matchmaking", "relationship app",
    "social network", "social media app", "social platform",
    # Personal consumer apps
    "photo editor", "photo editing", "selfie", "face filter",
    "personal assistant", "life coach", "habit tracker",
    "fitness app", "workout app", "diet app", "meditation app",
    "journaling app", "diary app",
    # Consumer entertainment
    "game", "gaming", "mobile game", "casual game",
    "entertainment app", "music app", "video app", "streaming app",
    # Consumer-focused signals
    "b2c", "consumer app", "consumer product", "consumer-facing",
    "app store", "play store", "mobile-first", "consumer startup",
    "individual users", "personal use", "everyday users",
]

# Company name patterns that indicate consumer AI
CONSUMER_AI_COMPANY_PATTERNS = [
    "dating", "social", "selfie", "photo", "game", "gaming",
    "match", "love", "friend", "chat", "snap", "gram",
    "music", "video", "stream", "play", "fun",
]


def is_likely_crypto_article(text: str, source_url: str = "") -> bool:
    """
    PRE-CHECK: Detect if article is crypto-focused BEFORE calling Claude.

    Saves tokens by skipping crypto articles that would be rejected anyway.
    Enhanced with URL pattern detection for crypto websites.
    """
    text_lower = text.lower()
    url_lower = source_url.lower() if source_url else ""

    # Check 1: URL contains crypto patterns (strong signal)
    if url_lower:
        for pattern in CRYPTO_URL_PATTERNS:
            if pattern in url_lower:
                logger.debug(f"Pre-check: Skipping crypto article (URL pattern: {pattern})")
                return True

    # Check 2: Keyword density in article text
    crypto_count = sum(1 for kw in CRYPTO_KEYWORDS if kw in text_lower)

    # Threshold of 3+ keywords indicates crypto content
    if crypto_count >= 3:
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


# =============================================================================
# AMOUNT VALIDATION (Detect market size confusion)
# =============================================================================

# Typical check sizes by round (upper bounds for flagging)
# Based on 2024-2025 market data for AI deals
ROUND_AMOUNT_THRESHOLDS = {
    RoundType.PRE_SEED: 5_000_000,      # $5M max typical for pre-seed
    RoundType.SEED: 25_000_000,          # $25M max typical for seed
    RoundType.SERIES_A: 100_000_000,     # $100M max typical for Series A
    RoundType.SERIES_B: 250_000_000,     # $250M max typical for Series B
    RoundType.SERIES_C: 500_000_000,     # $500M max typical for Series C
}

# Patterns that indicate market size / TAM (not funding amount)
MARKET_SIZE_PATTERNS = [
    r'\$\s*(\d+(?:\.\d+)?)\s*(?:billion|B)\s*(?:market|industry|opportunity|TAM|sector)',
    r'(?:market|industry|opportunity|TAM|sector)\s*(?:size|worth|valued at|of)\s*\$\s*(\d+(?:\.\d+)?)\s*(?:billion|B)',
    r'(?:spend|spending|spent)\s*(?:over|approximately|about|around)?\s*\$\s*(\d+(?:\.\d+)?)\s*(?:billion|B)',
    r'\$\s*(\d+(?:\.\d+)?)\s*(?:billion|B)\s*(?:annually|per year|a year)',
]


def _parse_amount_to_usd(amount_str: Optional[str]) -> Optional[int]:
    """Parse amount string to USD integer.

    Handles formats like: $50M, $50 million, 50M, $1.5B, etc.
    Returns None if parsing fails.
    """
    if not amount_str:
        return None

    # Clean the string
    amount_clean = amount_str.lower().strip()

    # Remove common prefixes
    for prefix in ['approximately', 'around', 'about', 'up to', 'over', '~', '$']:
        amount_clean = amount_clean.replace(prefix, '').strip()

    # Extract number and multiplier
    match = re.search(r'([\d,.]+)\s*(billion|b|million|m|mn)?', amount_clean)
    if not match:
        return None

    try:
        number = float(match.group(1).replace(',', ''))
        multiplier = match.group(2) or ''

        if multiplier in ('billion', 'b'):
            return int(number * 1_000_000_000)
        elif multiplier in ('million', 'm', 'mn'):
            return int(number * 1_000_000)
        elif number < 1000:
            # Small number without multiplier = assume millions (funding context)
            return int(number * 1_000_000)
        else:
            return int(number)
    except (ValueError, TypeError):
        return None


def _check_market_size_confusion(
    deal: DealExtraction,
    article_text: str,
) -> tuple[bool, Optional[str]]:
    """
    Check if extracted amount might be confused with market size.

    This catches cases like the Agency bug where:
    - Article mentions "$150 billion market size"
    - LLM extracts "$150 million" as funding amount

    Returns:
        (needs_review, reason): True if suspicious, with explanation
    """
    amount_usd = _parse_amount_to_usd(deal.amount)
    if not amount_usd:
        return False, None

    text_lower = article_text.lower()

    # Check 1: Round type vs typical check size
    threshold = ROUND_AMOUNT_THRESHOLDS.get(deal.round_label)
    if threshold and amount_usd > threshold:
        # Check if article mentions similar billion figure that could be confused
        for pattern in MARKET_SIZE_PATTERNS:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                try:
                    billion_value = float(match)
                    # If extracted amount (in millions) matches a market size (in billions)
                    # e.g., extracted $150M, article has $150B market size
                    if abs(amount_usd / 1_000_000 - billion_value) < 1:  # Within $1M
                        return True, (
                            f"Amount ${amount_usd/1_000_000:.0f}M may be confused with "
                            f"${billion_value}B market size mentioned in article"
                        )
                except (ValueError, TypeError):
                    continue

        # Generic warning for unusually large amounts
        round_name = deal.round_label.value.replace('_', ' ').title()
        return True, (
            f"{round_name} of ${amount_usd/1_000_000:.0f}M exceeds typical threshold "
            f"(${threshold/1_000_000:.0f}M) - verify against source"
        )

    return False, None


def _validate_deal_amount(deal: DealExtraction, article_text: str) -> DealExtraction:
    """
    Post-process validation for deal amounts.

    Checks for:
    1. Unusually large amounts for the round type
    2. Possible confusion with market size figures

    Sets amount_needs_review=True if suspicious.
    """
    needs_review, reason = _check_market_size_confusion(deal, article_text)

    if needs_review:
        logger.warning(
            f"AMOUNT REVIEW NEEDED for {deal.startup_name}: {reason}"
        )
        deal.amount_needs_review = True
        deal.amount_review_reason = reason

    return deal


def _is_consumer_ai_deal(deal: DealExtraction, article_text: str) -> bool:
    """
    Detect if deal is consumer AI (should NOT be Enterprise AI).

    Called as post-extraction validation to catch consumer AI deals that the LLM
    might have incorrectly classified as Enterprise AI.

    Consumer AI includes:
    - Dating/social apps with AI matching
    - Photo editors with AI filters
    - Gaming with AI NPCs
    - Personal assistant apps
    - B2C entertainment apps

    Returns:
        True if deal is consumer AI, False otherwise
    """
    # If already marked as consumer AI category, don't need to detect
    if deal.enterprise_category in (
        EnterpriseCategory.CONSUMER_AI,
        EnterpriseCategory.GAMING_AI,
        EnterpriseCategory.SOCIAL_AI,
    ):
        return True

    text_lower = article_text.lower()
    name_lower = deal.startup_name.lower() if deal.startup_name else ""
    desc_lower = deal.startup_description.lower() if deal.startup_description else ""

    # Check 1: Company name contains consumer AI patterns
    if any(pattern in name_lower for pattern in CONSUMER_AI_COMPANY_PATTERNS):
        logger.debug(f"Consumer AI detected by company name: {deal.startup_name}")
        return True

    # Check 2: Article is dominated by consumer AI keywords (3+ = likely consumer)
    consumer_count = sum(1 for kw in CONSUMER_AI_KEYWORDS if kw in text_lower)
    if consumer_count >= 3:
        logger.debug(f"Consumer AI detected by keyword density ({consumer_count}): {deal.startup_name}")
        return True

    # Check 3: Description explicitly mentions consumer focus
    consumer_desc_signals = [
        "consumer", "b2c", "dating", "social", "gaming", "game",
        "personal use", "individual users", "app store", "mobile app",
    ]
    if any(signal in desc_lower for signal in consumer_desc_signals):
        # Only trigger if NOT also mentioning B2B/enterprise signals
        enterprise_signals = ["b2b", "enterprise", "business", "saas", "platform", "api"]
        if not any(sig in desc_lower for sig in enterprise_signals):
            logger.debug(f"Consumer AI detected by description: {deal.startup_name}")
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


def _is_background_mention(
    company_name: str,
    article_text: str,
    is_external_source: bool = False,
) -> tuple[bool, str]:
    """
    Check if extracted company is likely a background mention, not the article's subject.

    Only call this when is_new_announcement=True (already checked at call site).

    Args:
        company_name: The extracted company name
        article_text: Full article text
        is_external_source: If True, use lenient checking (for external-only funds)

    Returns:
        (is_background, reason): True if background mention, with explanation

    Heuristics:
    - If company name not in first 500 chars (headline/lede), likely background
    - 500 chars accounts for wire service headers, bylines, dates
    - Exception: Very short articles where everything is "first 500 chars"
    - FIX (2026-01): Skip this check for external sources (Thrive, Benchmark, etc.)
      as they often have less structured content
    """
    if not company_name or not article_text:
        return False, ""

    # FIX (2026-01): Skip background check for external sources
    # These often have fund name in headline but company name later in article
    if is_external_source:
        logger.debug(f"Skipping background check for external source: {company_name}")
        return False, ""

    # FIX (2026-01): Increase short article threshold to 1500 chars
    # Many legitimate funding articles are 800-1500 chars
    if len(article_text) < 1500:
        return False, ""

    # Get the headline/lede (at least 500 chars to account for wire headers/bylines)
    # Previous 150 char limit was too aggressive - rejected 92% of valid extractions
    first_line_end = article_text.find('\n')
    if first_line_end == -1:
        first_line_end = 500
    else:
        first_line_end = max(first_line_end, 500)  # At least 500 chars
    headline_area = article_text[:first_line_end].lower()

    company_lower = company_name.lower().strip()

    # Check if company name appears in headline/lede
    if company_lower not in headline_area:
        # Check for partial match (e.g., "Traversal" in "Traversal AI")
        company_words = company_lower.split()
        main_word = company_words[0] if company_words else company_lower
        if len(main_word) >= 4 and main_word not in headline_area:  # FIX: Require 4+ chars (was 2)
            return True, f"Company '{company_name}' not in article headline - likely background mention"

    return False, ""


# =============================================================================
# ARTICLE TRUNCATION (Token Optimization)
# =============================================================================

MAX_ARTICLE_CHARS = 4000  # ~1,200 tokens (3.3 chars/token avg)


def truncate_article_smart(text: str, max_chars: int = MAX_ARTICLE_CHARS) -> str:
    """
    Truncate article while preserving funding-relevant content.

    Strategy:
    - Keep first 70% of max_chars (funding info usually in first paragraphs)
    - Scan remaining text for $ amounts, "led by", "series" mentions
    - Append relevant snippets with context

    This reduces token usage by 50-60% while maintaining extraction quality.
    """
    if not text or len(text) <= max_chars:
        return text

    # Keep first 70% of allowed length
    first_portion = int(max_chars * 0.7)
    result = text[:first_portion]

    # Scan remaining text for key funding mentions
    remaining = text[first_portion:]
    funding_patterns = [
        r'\$[\d,]+\s*(?:million|billion|M|B)?',  # $50 million, $50M
        r'(?:raised|raises|securing|secures)\s+\$[\d,]+',  # raises $50M
        r'series\s+[a-z]',  # Series A, B, C
        r'(?:led|co-led)\s+by',  # led by Sequoia
        r'seed\s+(?:round|funding)',  # seed round
        r'pre-seed',  # pre-seed
    ]

    # Find and append relevant snippets
    added_snippets = set()  # Avoid duplicates
    for pattern in funding_patterns:
        for match in re.finditer(pattern, remaining, re.IGNORECASE):
            # Get surrounding context (100 chars before/after)
            start = max(0, match.start() - 100)
            end = min(len(remaining), match.end() + 100)
            snippet = remaining[start:end].strip()

            # Skip if we'd exceed limit or already added similar snippet
            snippet_key = snippet[:50]  # Use first 50 chars as key
            if snippet_key in added_snippets:
                continue
            if len(result) + len(snippet) + 10 > max_chars:
                break

            added_snippets.add(snippet_key)
            result += f"\n...\n{snippet}"

    if len(text) > len(result):
        result += "\n[TRUNCATED]"

    return result


def build_extraction_prompt(
    article_text: str,
    fund_context: Optional[FundConfig] = None,
    source_url: str = ""
) -> str:
    """Build the extraction prompt with optional fund-specific context."""

    # OPTIMIZATION: Truncate long articles to reduce token usage
    truncated_text = truncate_article_smart(article_text)

    prompt = f"""Analyze this article and extract all funding deal information.

SOURCE URL: {source_url}

ARTICLE TEXT:
---
{truncated_text}
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
    source_name: str = "unknown",
    fund_context: Optional[FundConfig] = None,
    skip_funding_check: bool = False,
    skip_dedup_check: bool = False,
    is_external_source: bool = False,
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
        source_name: Source identifier for token logging (e.g., "brave_search", "a16z")
        fund_context: Optional fund-specific extraction context
        skip_funding_check: If True, skip the funding keyword check
        skip_dedup_check: If True, skip the duplicate content check
        is_external_source: If True, use lenient processing for external-only funds

    Returns:
        DealExtraction with structured deal information, or None if duplicate
    """
    # Auto-detect external source if not explicitly set
    # Check fund slug (for external-only funds like thrive, benchmark)
    if not is_external_source and source_name in EXTERNAL_ONLY_FUNDS:
        is_external_source = True
    # Check source name (for external sources like google_news, brave_search)
    if not is_external_source and source_name in EXTERNAL_SOURCE_NAMES:
        is_external_source = True
    # Check URL patterns (fallback for external source detection)
    if not is_external_source and ('news.google' in source_url or 'brave' in source_url.lower()):
        is_external_source = True
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
            enterprise_category=EnterpriseCategory.NOT_AI,
            is_enterprise_ai=False,
            is_ai_deal=False,
            verification_snippet=None,
            confidence_score=0.1,
            # NEW: Mark as not a new announcement
            is_new_announcement=False,
            announcement_evidence=None,
            announcement_rejection_reason="No funding keywords detected - not a funding article",
            reasoning=ChainOfThought(
                final_reasoning="Skipped extraction - no funding content detected"
            )
        )

    # PRE-CHECK: Skip crypto-heavy articles before calling Claude (saves tokens)
    # Now also checks URL patterns for crypto websites
    if not skip_funding_check and is_likely_crypto_article(article_text, source_url):
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
            enterprise_category=EnterpriseCategory.NOT_AI,
            is_enterprise_ai=False,
            is_ai_deal=False,
            verification_snippet=None,
            confidence_score=0.1,
            is_new_announcement=False,
            announcement_evidence=None,
            announcement_rejection_reason="Crypto/blockchain article - not an AI company",
            reasoning=ChainOfThought(
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
            # DEBUG: Log completion object details
            logger.info(f"[TOKEN DEBUG] completion type={type(completion)}, has_usage={hasattr(completion, 'usage') if completion else 'None'}")
            if completion and hasattr(completion, 'usage'):
                usage = completion.usage
                # Extract cache tokens if available (Anthropic-specific)
                cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
                cache_write = getattr(usage, 'cache_creation_input_tokens', 0) or 0
                logger.info(f"Claude call tokens: in={usage.input_tokens}, out={usage.output_tokens}")
                try:
                    await log_token_usage(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        source_name=source_name,
                        article_url=source_url,
                        model=settings.llm_model,
                        cache_read_tokens=cache_read,
                        cache_write_tokens=cache_write
                    )
                    logger.info(f"[TOKEN DEBUG] log_token_usage completed successfully for source={source_name}")
                except Exception as e:
                    logger.error(f"[TOKEN DEBUG] log_token_usage raised: {e}", exc_info=True)
            else:
                # Debug: log when completion doesn't have usage
                logger.warning(
                    f"No usage data in completion: type={type(completion)}, "
                    f"has_usage={hasattr(completion, 'usage') if completion else 'N/A'}"
                )

            # Post-process: verify tracked fund identification
            response = _verify_tracked_fund(response)

            # Post-process: detect and reclassify crypto deals
            if response and response.is_ai_deal and _is_crypto_deal(response, article_text):
                logger.info(
                    f"Reclassifying {response.startup_name} as NOT_AI: "
                    f"crypto/blockchain detected"
                )
                response.is_enterprise_ai = False
                response.is_ai_deal = False
                response.enterprise_category = EnterpriseCategory.NOT_AI

            # Post-process: detect and reclassify consumer AI deals
            # FIX: Consumer AI should not be counted as Enterprise AI
            if response and response.is_enterprise_ai and _is_consumer_ai_deal(response, article_text):
                logger.info(
                    f"Reclassifying {response.startup_name} as Consumer AI: "
                    f"consumer/gaming/social AI detected"
                )
                response.is_enterprise_ai = False
                # Keep is_ai_deal=True (it's still AI, just not enterprise)
                if response.enterprise_category not in (
                    EnterpriseCategory.CONSUMER_AI,
                    EnterpriseCategory.GAMING_AI,
                    EnterpriseCategory.SOCIAL_AI,
                ):
                    response.enterprise_category = EnterpriseCategory.CONSUMER_AI

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

            # Post-process: check if company is background mention (not in headline)
            # FIX (2026-01): Skip for external sources to avoid false rejections
            if response and response.is_new_announcement:
                is_background, reason = _is_background_mention(
                    response.startup_name,
                    article_text,
                    is_external_source=is_external_source,
                )
                if is_background:
                    logger.warning(
                        f"Rejecting background mention: '{response.startup_name}' "
                        f"from {source_url} - {reason}"
                    )
                    response.is_new_announcement = False
                    response.announcement_rejection_reason = reason

            # Post-process: validate deal amount (detect market size confusion)
            # FIX (2026-01): Catches cases like $150B market size → $150M funding
            if response and response.amount:
                response = _validate_deal_amount(response, article_text)

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
                source_name=article.get("fund_slug", "unknown"),
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
    source_name: str = "unknown",
    title: str = "",
    fund_context: Optional[FundConfig] = None
) -> Optional[ArticleAnalysis]:
    """
    Analyze a full article that may contain multiple deals.

    Args:
        article_text: The article content to analyze
        source_url: URL of the source article
        source_name: Source identifier for token logging (e.g., "brave_search", "a16z")
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
                logger.info(f"Claude article tokens: in={usage.input_tokens}, out={usage.output_tokens}")
                await log_token_usage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    source_name=source_name,
                    article_url=source_url,
                    model=settings.llm_model,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write
                )
            else:
                logger.warning(f"No usage data in article completion: type={type(completion)}")

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

    # Validate verification snippet - WARNING ONLY (don't reject)
    # Trust Claude's lead determination, but log for review if snippet lacks explicit lead language
    has_valid_snippet = _has_lead_language(deal.verification_snippet)

    if deal.tracked_fund_is_lead and deal.verification_snippet and not has_valid_snippet:
        logger.warning(
            f"WEAK lead evidence for {deal.startup_name}: "
            f"snippet '{deal.verification_snippet[:80]}...' lacks explicit lead language - trusting Claude"
        )
        deal.lead_evidence_weak = True  # Flag for frontend display

    if deal.tracked_fund_is_lead and not deal.verification_snippet:
        logger.warning(
            f"WEAK lead evidence for {deal.startup_name}: "
            f"no verification_snippet provided - trusting Claude"
        )
        deal.lead_evidence_weak = True  # Flag for frontend display

    # FIX: Track ALL confirmed lead funds to record co-leads
    confirmed_lead_funds: list[tuple[str, str, str]] = []  # (slug, name, partner)

    # Check ALL lead investors against our fund registry
    for investor in deal.lead_investors:
        matched_fund = match_fund_name(investor.name)
        # FIX: Validate matched_fund exists in FUND_REGISTRY before using
        if matched_fund and matched_fund in FUND_REGISTRY:
            investor.is_tracked_fund = True
            matched_leads.append((matched_fund, investor))

            # Track confirmed/likely leads with valid snippets
            if investor.role in (LeadStatus.CONFIRMED_LEAD, LeadStatus.LIKELY_LEAD) and has_valid_snippet:
                confirmed_lead_funds.append((
                    matched_fund,
                    FUND_REGISTRY[matched_fund].name,
                    investor.partner_name or ""
                ))
            elif not deal.tracked_fund_name:
                # Downgrade: no snippet or invalid snippet - set as participant
                logger.info(
                    f"Downgrading {deal.startup_name} lead claim: "
                    f"role={investor.role.value}, has_valid_snippet={has_valid_snippet}"
                )
                deal.tracked_fund_name = FUND_REGISTRY[matched_fund].name
                deal.tracked_fund_role = LeadStatus.PARTICIPANT
                deal.tracked_fund_partner = investor.partner_name

        elif matched_fund:
            # Fund matched but not in registry - log warning
            logger.warning(f"Matched fund '{matched_fund}' not in FUND_REGISTRY")

    # FIX: Set deal-level fields to include ALL co-lead tracked funds
    if confirmed_lead_funds:
        deal.tracked_fund_is_lead = True
        # Join all co-lead fund names (e.g., "Sequoia, Benchmark")
        fund_names = [name for _, name, _ in confirmed_lead_funds]
        deal.tracked_fund_name = ", ".join(fund_names)
        deal.tracked_fund_role = LeadStatus.CONFIRMED_LEAD
        # Collect all partner names if present
        partner_names = [p for _, _, p in confirmed_lead_funds if p]
        deal.tracked_fund_partner = ", ".join(partner_names) if partner_names else None

    # Log if multiple tracked funds co-led
    if len(confirmed_lead_funds) > 1:
        logger.info(
            f"Multiple tracked funds co-led deal for {deal.startup_name}: "
            f"{[n for _, n, _ in confirmed_lead_funds]}"
        )

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

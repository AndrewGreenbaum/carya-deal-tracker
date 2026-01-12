"""
Centralized Fund Name Matching.

Used by both extractor.py and storage.py to consistently match investor names
to tracked fund slugs. Prevents deal mislinks caused by order-dependent substring matching.

FIX: Added word boundary matching to prevent false positives like "menlo business" → "menlo".
"""

import re
from typing import Optional


# Fund name variants for matching
# Order matters within each fund's list - more specific variants first
FUND_NAME_VARIANTS: dict[str, list[str]] = {
    "a16z": [
        "andreessen horowitz",
        # NOTE: "a16z crypto" removed - it's a SEPARATE fund with different thesis
        # Crypto deals should NOT count toward main a16z Enterprise AI portfolio
        "a16z bio",
        "a16z games",
        "a16z",
    ],
    "sequoia": [
        "sequoia capital",
        "sequoia",
    ],
    "benchmark": [
        "benchmark capital",
        "benchmark",
        "bill gurley",  # Well-known Benchmark partner
        "sarah tavel",
        "peter fenton",
        "eric vishria",
    ],
    "founders_fund": [
        "founders fund",
        "peter thiel",
    ],
    "greylock": [
        "greylock partners",
        "greylock",
        "reid hoffman",  # Co-founder LinkedIn, Greylock partner
        "jerry chen",
        "saam motamedi",
    ],
    "accel": [
        "accel partners",
        "accel",
    ],
    "index": [
        "index ventures",
    ],
    "insight": [
        "insight partners",
    ],
    "felicis": [
        "felicis ventures",
        "felicis",
    ],
    "redpoint": [
        "redpoint ventures",
        "redpoint",
        "tomasz tunguz",  # Well-known Redpoint partner
        "logan bartlett",
    ],
    "khosla": [
        "khosla ventures",
        "khosla",
    ],
    "menlo": [
        "menlo ventures",
    ],
    "bessemer": [
        "bessemer venture partners",
        "bessemer",
        "bvp",
    ],
    "gv": [
        "google ventures",
        "gv ",  # Note the space to avoid matching "GV" in other contexts
        "david krane",  # GV CEO
        "mg siegler",
    ],
    "usv": [
        "union square ventures",
        "usv",
    ],
    "thrive": [
        "thrive capital",
    ],
    "general_catalyst": [
        "general catalyst",
    ],
    "first_round": [
        "first round capital",
        "first round",
        "josh kopelman",  # First Round founder
        "phin barnes",
    ],
}


# Negative keywords - if these appear, DON'T match to that fund
# NOTE: Be careful not to be too aggressive - these should only exclude
# clear false positives, not valid mentions that happen to contain similar words
NEGATIVE_KEYWORDS: dict[str, list[str]] = {
    "a16z": [
        "a16z crypto",           # Separate fund - different thesis (crypto-focused)
        "crypto fund",           # Crypto context
        "a16z cryptocurrency",   # Crypto variant
    ],
    "gv": [
        "nyse:gv",
        "nyse: gv",
        "gv stock",
        "visionary holdings",
        "global ventures",  # Different fund
    ],
    "thrive": [
        "thrive global",
        "thrive it",
        "thrive market",
        "thrive services",
    ],
    "sequoia": [
        "hongshan",
        "peak xv",
        "sequoia india",
        "sequoia china",
        "sequoia heritage",  # Different entity
    ],
    "benchmark": [
        "benchmark international",
        "benchmark electronics",
        "benchmark capital management",  # Different fund
        "benchmark mineral",
        "benchmark litigation",
    ],
    "insight": [
        "insight venture management",  # Subsidiary - actually still valid
        # Removed: "market insight", "business insight", "consumer insight"
        # These were too aggressive and could appear in valid Insight Partners articles
    ],
    "index": [
        "index fund",
        "stock index",
        "s&p index",
        "market index",
        # These are specific enough to not exclude valid Index Ventures mentions
    ],
    "accel": [
        # Removed: "accelerate", "acceleration" - too aggressive
        # These words commonly appear in valid startup funding news
        "accelerator program",  # Keep - refers to accelerators, not Accel
        "accel entertainment",  # Different company (gaming)
    ],
    "menlo": [
        # Removed: "menlo park" - Menlo Ventures IS based in Menlo Park
        # and many articles mention both legitimately
        "menlo college",
        "menlo school",
    ],
    "redpoint": [
        "redpoint bio",
        "redpoint positioning",
        "redpoint global",  # Different fund
    ],
    "general_catalyst": [
        # Removed: "general catalyst partners" - this is just their full legal name
        # and shouldn't be excluded
    ],
}


def match_fund_name(investor_name: str) -> Optional[str]:
    """
    Match an investor name to a tracked fund slug.

    Uses a two-pass approach:
    1. Check negative keywords first to exclude false positives
    2. Check positive variants to find matches

    Args:
        investor_name: The investor name to match (e.g., "Felicis Ventures")

    Returns:
        Fund slug if matched (e.g., "felicis"), None if no match

    Examples:
        >>> match_fund_name("Felicis Ventures")
        'felicis'
        >>> match_fund_name("Andreessen Horowitz")
        'a16z'
        >>> match_fund_name("Benchmark International")  # Excluded by negative keyword
        None
        >>> match_fund_name("Unknown VC")
        None
    """
    if not investor_name:
        return None

    name_lower = investor_name.lower().strip()

    # Build a map of all matches found
    matches: list[tuple[str, int]] = []  # (slug, match_position)

    for slug, variants in FUND_NAME_VARIANTS.items():
        # Check negative keywords first
        negative_list = NEGATIVE_KEYWORDS.get(slug, [])
        has_negative = any(neg in name_lower for neg in negative_list)
        if has_negative:
            continue  # Skip this fund entirely

        # Check for positive matches with word boundary
        # FIX: Use regex word boundaries to prevent false positives
        # e.g., "menlo business" should NOT match "menlo"
        for variant in variants:
            # Build regex pattern with word boundaries
            # Escape special regex chars and add word boundary markers
            pattern = rf'\b{re.escape(variant)}\b'
            match = re.search(pattern, name_lower)
            if match:
                # Record match with position for priority
                pos = match.start()
                matches.append((slug, pos))
                break  # Only count first matching variant per fund

    if not matches:
        return None

    # Return the match that appears earliest in the string
    # This handles cases like "Accel and Felicis" - returns the first one
    matches.sort(key=lambda x: x[1])
    return matches[0][0]


def get_all_fund_slugs() -> list[str]:
    """Get list of all tracked fund slugs."""
    return list(FUND_NAME_VARIANTS.keys())


def get_fund_variants(slug: str) -> list[str]:
    """Get all name variants for a fund."""
    return FUND_NAME_VARIANTS.get(slug, [])


def is_excluded_by_negative_keyword(investor_name: str, slug: str) -> bool:
    """Check if an investor name is excluded for a specific fund by negative keywords."""
    if not investor_name:
        return False
    name_lower = investor_name.lower()
    negative_list = NEGATIVE_KEYWORDS.get(slug, [])
    return any(neg in name_lower for neg in negative_list)

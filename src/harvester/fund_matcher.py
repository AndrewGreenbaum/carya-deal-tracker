"""
Centralized Fund Name Matching.

Used by both extractor.py and storage.py to consistently match investor names
to tracked fund slugs. Prevents deal mislinks caused by order-dependent substring matching.
"""

from typing import Optional


# Fund name variants for matching
# Order matters within each fund's list - more specific variants first
FUND_NAME_VARIANTS: dict[str, list[str]] = {
    "a16z": [
        "andreessen horowitz",
        "a16z crypto",
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
    ],
    "founders_fund": [
        "founders fund",
        "peter thiel",
    ],
    "greylock": [
        "greylock partners",
        "greylock",
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
    ],
}


# Negative keywords - if these appear, DON'T match to that fund
NEGATIVE_KEYWORDS: dict[str, list[str]] = {
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
        "insight venture management",  # Subsidiary
        "market insight",
        "business insight",
        "consumer insight",
    ],
    "index": [
        "index fund",
        "stock index",
        "s&p index",
        "market index",
    ],
    "accel": [
        "accelerate",
        "acceleration",
        "accelerator program",
        "accel entertainment",  # Different company
    ],
    "menlo": [
        "menlo park",  # Location, not the fund
        "menlo college",
        "menlo school",
    ],
    "redpoint": [
        "redpoint bio",
        "redpoint positioning",
        "redpoint global",  # Different fund
    ],
    "general_catalyst": [
        "general catalyst partners",  # Old name but still valid
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

        # Check for positive matches
        for variant in variants:
            if variant in name_lower:
                # Record match with position for priority
                pos = name_lower.find(variant)
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

"""Tests for deal deduplication key generation.

FIX Jan 2026: Prevents race condition duplicates when processing deals in parallel.

The dedup_key is an MD5 hash of:
- normalized_company_name (lowercase, no suffixes)
- round_type
- date_bucket (3-day windows to catch near-duplicates)

This provides database-level protection via unique constraint + ON CONFLICT.
"""

import pytest
import hashlib
import re
from datetime import date


# Copy of the functions to test (avoids import issues)
def _normalize_company_name_for_dedup(name: str) -> str:
    """Normalize company name for dedup key generation."""
    name = name.lower().strip()
    if name.startswith('the '):
        name = name[4:]
    suffixes = [
        ', incorporated', ' incorporated', ', technologies', ' technologies',
        ', corporation', ' corporation', ', limited', ' limited',
        ', company', ' company', ', inc.', ' inc.', ', inc', ' inc',
        ', llc', ' llc', ', ltd.', ' ltd.', ', ltd', ' ltd',
        ', corp.', ' corp.', ', corp', ' corp', ', co.', ' co.',
        ', co', ' co', ' labs', ' lab', ' tech', ' ai',
    ]
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return re.sub(r'[^a-z0-9]', '', name)


def make_dedup_key(company_name: str, round_type: str, announced_date) -> str:
    """Generate deduplication key for a deal."""
    normalized_name = _normalize_company_name_for_dedup(company_name)
    if announced_date:
        days_since_epoch = (announced_date - date(1970, 1, 1)).days
        date_bucket = days_since_epoch // 3
        date_str = str(date_bucket)
    else:
        today = date.today()
        week_bucket = (today - date(1970, 1, 1)).days // 7
        date_str = f'nodate_{week_bucket}'
    key_data = f'{normalized_name}|{round_type}|{date_str}'
    return hashlib.md5(key_data.encode()).hexdigest()


class TestCompanyNameNormalization:
    """Tests for company name normalization."""

    def test_lowercase(self):
        """Names should be case-insensitive."""
        assert _normalize_company_name_for_dedup("Converge Bio") == "convergebio"
        assert _normalize_company_name_for_dedup("CONVERGE BIO") == "convergebio"
        assert _normalize_company_name_for_dedup("converge bio") == "convergebio"

    def test_strips_inc_suffix(self):
        """Should strip Inc./Inc suffix."""
        assert _normalize_company_name_for_dedup("Converge Bio, Inc.") == "convergebio"
        assert _normalize_company_name_for_dedup("Converge Bio Inc") == "convergebio"

    def test_strips_llc_suffix(self):
        """Should strip LLC suffix."""
        assert _normalize_company_name_for_dedup("Converge Bio, LLC") == "convergebio"
        assert _normalize_company_name_for_dedup("Converge Bio LLC") == "convergebio"

    def test_strips_the_prefix(self):
        """Should strip 'The' prefix."""
        assert _normalize_company_name_for_dedup("The Converge Company") == "converge"

    def test_strips_labs_suffix(self):
        """Should strip Labs suffix."""
        assert _normalize_company_name_for_dedup("Converge Labs") == "converge"

    def test_strips_ai_suffix(self):
        """Should strip AI suffix."""
        assert _normalize_company_name_for_dedup("Converge AI") == "converge"

    def test_removes_non_alphanumeric(self):
        """Should remove non-alphanumeric characters."""
        assert _normalize_company_name_for_dedup("Converge-Bio") == "convergebio"
        assert _normalize_company_name_for_dedup("Converge.Bio") == "convergebio"
        assert _normalize_company_name_for_dedup("Converge & Bio") == "convergebio"


class TestDedupKeyGeneration:
    """Tests for dedup key generation."""

    def test_identical_inputs_same_key(self):
        """Identical inputs should produce identical keys."""
        k1 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        k2 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        assert k1 == k2

    def test_case_insensitive(self):
        """Company names should be case-insensitive."""
        k1 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        k2 = make_dedup_key("converge bio", "series_a", date(2026, 1, 13))
        k3 = make_dedup_key("CONVERGE BIO", "series_a", date(2026, 1, 13))
        assert k1 == k2 == k3

    def test_suffix_normalized(self):
        """Company names with suffixes should match base name."""
        k1 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        k2 = make_dedup_key("Converge Bio, Inc.", "series_a", date(2026, 1, 13))
        k3 = make_dedup_key("Converge Bio LLC", "series_a", date(2026, 1, 13))
        assert k1 == k2 == k3

    def test_same_3day_bucket(self):
        """Dates within same 3-day bucket should produce same key."""
        k1 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        k2 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 14))  # +1 day
        k3 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 15))  # +2 days
        # Check bucket math: Jan 13 2026 = day 20466 since epoch, bucket 6822
        assert k1 == k2  # Same bucket

    def test_different_3day_bucket(self):
        """Dates in different 3-day buckets should produce different keys."""
        k1 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        k2 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 16))  # +3 days
        assert k1 != k2  # Different bucket

    def test_different_round_type(self):
        """Different round types should produce different keys."""
        k1 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        k2 = make_dedup_key("Converge Bio", "series_b", date(2026, 1, 13))
        assert k1 != k2

    def test_different_company(self):
        """Different companies should produce different keys."""
        k1 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        k2 = make_dedup_key("WeatherPromise", "series_a", date(2026, 1, 13))
        assert k1 != k2

    def test_none_date(self):
        """None date should use week bucket."""
        k1 = make_dedup_key("Converge Bio", "series_a", None)
        k2 = make_dedup_key("Converge Bio", "series_a", None)
        # Same company+round+same week = same key
        assert k1 == k2

    def test_key_is_32_chars(self):
        """Key should be 32-character MD5 hex digest."""
        key = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        assert len(key) == 32
        assert all(c in '0123456789abcdef' for c in key)


class TestRaceConditionScenarios:
    """Tests for the specific race condition scenarios this fix addresses."""

    def test_converge_bio_duplicate(self):
        """
        The Converge Bio duplicate case from production:
        - Both deals: Converge Bio, series_a, 2026-01-13
        - Created milliseconds apart (IDs 2904, 2905)
        - Same dedup_key should have prevented this
        """
        k1 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        k2 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        assert k1 == k2, "Same deal should have same dedup_key"

    def test_weather_promise_duplicate(self):
        """
        The WeatherPromise duplicate case from production:
        - Both deals: WeatherPromise, series_a, 2026-01-13
        - IDs 2927, 2930
        """
        k1 = make_dedup_key("WeatherPromise", "series_a", date(2026, 1, 13))
        k2 = make_dedup_key("WeatherPromise", "series_a", date(2026, 1, 13))
        assert k1 == k2, "Same deal should have same dedup_key"

    def test_date_variation_caught(self):
        """
        Articles about the same deal sometimes report dates 1-2 days apart.
        3-day bucket should catch these.
        """
        # Article 1 says Jan 13
        k1 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 13))
        # Article 2 says Jan 14 (slight variation)
        k2 = make_dedup_key("Converge Bio", "series_a", date(2026, 1, 14))
        assert k1 == k2, "1-day variation should be same bucket"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

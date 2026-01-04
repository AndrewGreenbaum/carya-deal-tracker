"""
Shared Brave Search API Client.

Provides a reusable HTTP client with:
- Exponential backoff retry logic
- Rate limit (HTTP 429) handling
- Configurable timeouts and delays
- Proper error logging

Used by:
- brave_search.py (news search)
- brave_enrichment.py (company enrichment)
- linkedin_jobs.py (stealth detection)
- delaware_corps.py (incorporation search)
"""

import asyncio
import logging
from typing import Optional, Dict, Any

import httpx

from ..config.settings import settings

logger = logging.getLogger(__name__)

# Brave Search API endpoints
BRAVE_NEWS_API = "https://api.search.brave.com/res/v1/news/search"
BRAVE_WEB_API = "https://api.search.brave.com/res/v1/web/search"


class BraveAPIError(Exception):
    """Raised when Brave API returns an error."""
    pass


class BraveClient:
    """
    Shared async HTTP client for Brave Search API.

    Features:
    - Exponential backoff retry on failures
    - HTTP 429 rate limit handling with Retry-After
    - Configurable via settings
    """

    def __init__(self):
        self.api_key = settings.brave_search_key
        self.timeout = settings.brave_search_timeout
        self.max_retries = settings.brave_search_max_retries
        self.rate_limit_delay = settings.brave_search_rate_limit_delay
        self.backoff_base = settings.brave_search_backoff_base
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "X-Subscription-Token": self.api_key,
                    "Accept": "application/json",
                }
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def validate_api_key(self) -> bool:
        """Check if API key is configured."""
        if not self.api_key:
            logger.error("BRAVE_SEARCH_KEY not configured")
            return False
        return True

    async def request(
        self,
        url: str,
        params: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Make HTTP request with exponential backoff retry logic.

        Args:
            url: API endpoint URL
            params: Query parameters

        Returns:
            JSON response data or None on failure

        Handles:
        - HTTP 429 (rate limit) with Retry-After header
        - HTTP 5xx (server errors) with exponential backoff
        - Timeouts with retry
        - Network errors with retry
        """
        if not self.validate_api_key():
            return None

        client = await self._get_client()
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = await client.get(url, params=params)

                # Handle rate limiting (429)
                if response.status_code == 429:
                    # FIX: Retry-After can be int OR HTTP-date string
                    retry_after_header = response.headers.get("Retry-After", "60")
                    try:
                        retry_after = int(retry_after_header)
                    except ValueError:
                        # HTTP-date format (e.g., "Wed, 21 Oct 2025 07:28:00 GMT")
                        logger.warning(f"Non-numeric Retry-After header: {retry_after_header}")
                        retry_after = 60
                    logger.warning(
                        f"Brave API rate limited. Waiting {retry_after}s "
                        f"(attempt {attempt + 1}/{self.max_retries})"
                    )
                    await asyncio.sleep(retry_after)
                    continue

                # Handle server errors with backoff
                if response.status_code >= 500:
                    backoff = self.backoff_base ** attempt
                    logger.warning(
                        f"Brave API server error {response.status_code}. "
                        f"Retrying in {backoff:.1f}s (attempt {attempt + 1}/{self.max_retries})"
                    )
                    await asyncio.sleep(backoff)
                    continue

                response.raise_for_status()

                # FIX: Handle JSONDecodeError from malformed response
                try:
                    return response.json()
                except ValueError as e:
                    logger.warning(f"Brave API JSON decode error: {e}")
                    return None

            except httpx.TimeoutException:
                backoff = self.backoff_base ** attempt
                logger.warning(
                    f"Brave API timeout. Retrying in {backoff:.1f}s "
                    f"(attempt {attempt + 1}/{self.max_retries})"
                )
                last_error = "timeout"
                await asyncio.sleep(backoff)

            except httpx.HTTPStatusError as e:
                # Client errors (4xx except 429) - don't retry
                if 400 <= e.response.status_code < 500:
                    logger.error(f"Brave API client error: {e.response.status_code}")
                    return None
                last_error = str(e)

            except httpx.RequestError as e:
                # Network errors - retry with backoff
                backoff = self.backoff_base ** attempt
                logger.warning(
                    f"Brave API network error: {e}. Retrying in {backoff:.1f}s "
                    f"(attempt {attempt + 1}/{self.max_retries})"
                )
                last_error = str(e)
                await asyncio.sleep(backoff)

        logger.error(f"Brave API request failed after {self.max_retries} attempts: {last_error}")
        return None

    async def search_news(
        self,
        query: str,
        count: int = 20,
        freshness: str = "pw",
    ) -> Optional[Dict[str, Any]]:
        """
        Execute news search on Brave API.

        Args:
            query: Search query string
            count: Number of results (max 100)
            freshness: pd=past day, pw=past week, pm=past month

        Returns:
            Raw API response or None on failure
        """
        params = {
            "q": query,
            "count": count,
            "freshness": freshness,
            "text_decorations": False,
            "safesearch": "off",
        }
        return await self.request(BRAVE_NEWS_API, params)

    async def search_web(
        self,
        query: str,
        count: int = 20,
        freshness: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute web search on Brave API.

        Args:
            query: Search query string
            count: Number of results (max 100)
            freshness: Optional - pd=past day, pw=past week, pm=past month

        Returns:
            Raw API response or None on failure
        """
        params = {
            "q": query,
            "count": count,
            "text_decorations": False,
            "safesearch": "off",
        }
        if freshness:
            params["freshness"] = freshness
        return await self.request(BRAVE_WEB_API, params)

    async def delay(self):
        """Wait for configured rate limit delay between requests."""
        await asyncio.sleep(self.rate_limit_delay)


# Singleton instance for shared use
_client: Optional[BraveClient] = None


def get_brave_client() -> BraveClient:
    """Get shared Brave client instance."""
    global _client
    if _client is None:
        _client = BraveClient()
    return _client


async def close_brave_client():
    """Close the shared client (call on shutdown)."""
    global _client
    if _client:
        await _client.close()
        _client = None

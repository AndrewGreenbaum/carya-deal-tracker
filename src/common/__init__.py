"""
Common utilities and shared modules.
"""

from .brave_client import (
    BraveClient,
    BraveAPIError,
    get_brave_client,
    close_brave_client,
    BRAVE_NEWS_API,
    BRAVE_WEB_API,
)

__all__ = [
    "BraveClient",
    "BraveAPIError",
    "get_brave_client",
    "close_brave_client",
    "BRAVE_NEWS_API",
    "BRAVE_WEB_API",
]

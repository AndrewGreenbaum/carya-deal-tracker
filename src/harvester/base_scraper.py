"""
Base Scraper - Abstract interface for fund-specific scrapers.

Implements the PolymorphicScraper pattern with:
- fetch(): Retrieve HTML from source
- parse(): Extract article metadata
- normalize(): Standardize to common format
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from typing import List, Optional, AsyncIterator
import httpx
from bs4 import BeautifulSoup, Comment

from ..config.funds import FundConfig
from ..config.settings import settings


@dataclass
class RawArticle:
    """Raw article data as scraped from source."""
    url: str
    title: str
    html: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_date: Optional[date] = None
    author: Optional[str] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class NormalizedArticle:
    """Normalized article ready for processing."""
    url: str
    title: str
    text: str  # Cleaned text content
    published_date: Optional[date] = None
    author: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    fund_slug: str = ""  # Which fund's feed this came from
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BaseScraper(ABC):
    """
    Abstract base class for fund-specific scrapers.

    Subclasses must implement:
    - fetch(): Get HTML from the fund's news source
    - parse(): Extract article list from HTML
    - normalize(): Clean and standardize articles
    """

    def __init__(self, fund_config: FundConfig):
        self.fund = fund_config
        self.client = httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "BudTracker/1.0 (Investment Research Bot)"
            }
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    @abstractmethod
    async def fetch(self, url: Optional[str] = None) -> str:
        """
        Fetch HTML content from the fund's news source.

        Args:
            url: Optional specific URL. If None, use fund's default ingestion_url.

        Returns:
            Raw HTML string
        """
        pass

    @abstractmethod
    async def parse(self, html: str) -> List[RawArticle]:
        """
        Parse HTML to extract article metadata.

        Args:
            html: Raw HTML from fetch()

        Returns:
            List of RawArticle objects
        """
        pass

    @abstractmethod
    async def normalize(self, raw: RawArticle) -> NormalizedArticle:
        """
        Normalize a raw article to standard format.

        Args:
            raw: RawArticle from parse()

        Returns:
            NormalizedArticle ready for processing
        """
        pass

    async def scrape(self) -> AsyncIterator[NormalizedArticle]:
        """
        Full scraping pipeline: fetch -> parse -> normalize.

        Yields:
            NormalizedArticle objects one at a time
        """
        html = await self.fetch()
        articles = await self.parse(html)

        for raw in articles:
            # Apply negative keyword filter
            if self._should_filter(raw):
                continue

            normalized = await self.normalize(raw)
            normalized.fund_slug = self.fund.slug
            yield normalized

    def _should_filter(self, article: RawArticle) -> bool:
        """Check if article should be filtered based on fund's negative keywords."""
        text = f"{article.title} {article.html}".lower()

        for keyword in self.fund.negative_keywords:
            if keyword.lower() in text:
                return True

        return False

    def _extract_text(self, html: str) -> str:
        """Extract clean text from HTML."""
        soup = BeautifulSoup(html, "lxml")

        # Remove script and style elements
        for element in soup(["script", "style", "nav", "header", "footer"]):
            element.decompose()

        # FIX #51: Remove HTML comments before extracting text
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        # Get text
        text = soup.get_text(separator="\n", strip=True)

        # Clean up whitespace
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    def _parse_date(self, date_str: str) -> Optional[date]:
        """Attempt to parse various date formats."""
        from dateutil import parser as date_parser

        try:
            return date_parser.parse(date_str).date()
        except (ValueError, TypeError):
            return None


class SimpleHTMLScraper(BaseScraper):
    """
    Simple HTML scraper for funds with straightforward news pages.

    Override article_selector and content_selector for fund-specific parsing.
    """

    article_selector: str = "article"
    title_selector: str = "h1, h2, .title"
    link_selector: str = "a"
    date_selector: str = "time, .date, .published"
    content_selector: str = ".content, .body, article"

    async def fetch(self, url: Optional[str] = None) -> str:
        target_url = url or self.fund.ingestion_url
        response = await self.client.get(target_url)
        response.raise_for_status()
        return response.text

    async def parse(self, html: str) -> List[RawArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        for article_el in soup.select(self.article_selector):
            # Extract title
            title_el = article_el.select_one(self.title_selector)
            title = title_el.get_text(strip=True) if title_el else ""

            # Extract link
            link_el = article_el.select_one(self.link_selector)
            url = link_el.get("href", "") if link_el else ""

            # Make URL absolute
            if url and not url.startswith("http"):
                base = self.fund.ingestion_url.rstrip("/")
                url = f"{base}/{url.lstrip('/')}"

            # Extract date
            date_el = article_el.select_one(self.date_selector)
            pub_date = None
            if date_el:
                date_str = date_el.get("datetime") or date_el.get_text(strip=True)
                pub_date = self._parse_date(date_str)

            if title and url:
                articles.append(RawArticle(
                    url=url,
                    title=title,
                    html=str(article_el),
                    published_date=pub_date
                ))

        return articles

    async def normalize(self, raw: RawArticle) -> NormalizedArticle:
        # Fetch full article if we only have snippet
        if len(raw.html) < 500:
            try:
                full_html = await self.fetch(raw.url)
                text = self._extract_text(full_html)
            except Exception:
                text = self._extract_text(raw.html)
        else:
            text = self._extract_text(raw.html)

        return NormalizedArticle(
            url=raw.url,
            title=raw.title,
            text=text,
            published_date=raw.published_date,
            author=raw.author,
            tags=raw.tags
        )

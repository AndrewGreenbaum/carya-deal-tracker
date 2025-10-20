"""
Khosla Ventures Scraper.

Source: https://www.khoslaventures.com/posts/rss.xml
Format: RSS

Deep-tech focus; RSS feed of blog posts that may mention investments.
"""

from typing import List, Optional
from datetime import datetime
import feedparser
from bs4 import BeautifulSoup

from ..base_scraper import SimpleHTMLScraper, RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class KhoslaScraper(SimpleHTMLScraper):
    """
    Scraper for Khosla Ventures RSS feed.

    Deep-tech focused fund with RSS feed of blog posts.
    """

    def __init__(self):
        super().__init__(FUND_REGISTRY["khosla"])
        self.base_url = "https://www.khoslaventures.com"
        self.rss_url = "https://www.khoslaventures.com/posts/rss.xml"

    async def fetch(self, url: Optional[str] = None) -> str:
        target_url = url or self.rss_url
        response = await self.client.get(target_url)
        response.raise_for_status()
        return response.text

    async def parse(self, content: str) -> List[RawArticle]:
        """Parse RSS feed content."""
        articles = []

        # Parse RSS feed
        feed = feedparser.parse(content)

        for entry in feed.entries[:20]:  # Limit to 20 most recent
            title = entry.get("title", "")
            url = entry.get("link", "")

            if not title or not url:
                continue

            # Parse publication date
            pub_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    pub_date = datetime(*entry.published_parsed[:6])
                except (TypeError, ValueError):
                    pass

            # Get description/summary
            description = entry.get("description", "") or entry.get("summary", "")

            articles.append(RawArticle(
                url=url,
                title=title,
                html=description,
                published_date=pub_date,
            ))

        return articles

    async def normalize(self, raw: RawArticle) -> NormalizedArticle:
        """Fetch full article content and normalize."""
        try:
            full_html = await self.fetch(raw.url)
            text = self._extract_text(full_html)
        except Exception:
            # Fall back to RSS description
            text = BeautifulSoup(raw.html, "lxml").get_text(strip=True) if raw.html else ""

        return NormalizedArticle(
            url=raw.url,
            title=raw.title,
            text=text,
            published_date=raw.published_date,
        )


def create_scraper() -> KhoslaScraper:
    return KhoslaScraper()

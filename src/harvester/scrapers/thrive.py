"""
Thrive Capital Scraper.

Source: external:prn,businesswire,techcrunch
Format: EXTERNAL

CRITICAL: Exclude 'Thrive IT Services' or 'Thrive Global'.
"""

from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import BaseScraper, RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class ThriveScraper(BaseScraper):
    """
    Scraper for Thrive Capital via news aggregators.

    Critical: Must exclude "Thrive IT Services", "Thrive Global", "Thrive Market".
    """

    def __init__(self):
        super().__init__(FUND_REGISTRY["thrive"])
        self.search_url = "https://news.google.com/search"

    async def fetch(self, url: Optional[str] = None) -> str:
        if url:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.text

        # Search for Thrive Capital funding news
        params = {
            "q": '"Thrive Capital" funding OR investment OR raises -"Thrive IT" -"Thrive Global" -"Thrive Market"',
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
        response = await self.client.get(self.search_url, params=params)
        response.raise_for_status()
        return response.text

    async def parse(self, html: str) -> List[RawArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        for card in soup.select("article, [class*='article']"):
            title_el = card.select_one("h3, h4, .title, a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            # Apply critical exclusions
            if self._should_exclude(title):
                continue

            link_el = card.select_one("a[href]")
            if not link_el:
                continue
            url = link_el.get("href", "")

            if not url or "google.com" in url:
                continue

            date_el = card.select_one("time, .date")
            pub_date = None
            if date_el:
                date_str = date_el.get("datetime") or date_el.get_text(strip=True)
                pub_date = self._parse_date(date_str)

            articles.append(RawArticle(
                url=url,
                title=title,
                html=str(card),
                published_date=pub_date,
            ))

        return articles

    async def normalize(self, raw: RawArticle) -> NormalizedArticle:
        try:
            full_html = await self.fetch(raw.url)
            text = self._extract_text(full_html)

            # Double-check exclusions in full text
            if self._should_exclude(text):
                text = ""

        except Exception:
            text = self._extract_text(raw.html)

        return NormalizedArticle(
            url=raw.url,
            title=raw.title,
            text=text,
            published_date=raw.published_date,
        )

    def _should_exclude(self, text: str) -> bool:
        """Critical: Exclude non-Thrive Capital content."""
        text_lower = text.lower()
        exclude_keywords = [
            "thrive it services",
            "thrive global",
            "thrive market",
            "thrive pet healthcare",
            "thrive fitness",
            "thrive causemetics",
        ]
        return any(kw in text_lower for kw in exclude_keywords)


def create_scraper() -> ThriveScraper:
    return ThriveScraper()

"""
Benchmark Capital Scraper.

Source: external:prn,businesswire (News aggregators)
Format: EXTERNAL

Benchmark doesn't have a public news feed - we search news aggregators.
Must include Partner names in article. Filter NOT 'International', NOT 'Electronics'.
"""

from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import BaseScraper, RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class BenchmarkScraper(BaseScraper):
    """
    Scraper for Benchmark Capital via news aggregators.

    Searches PRNewswire and BusinessWire for Benchmark mentions.
    Filters out "Benchmark International" and "Benchmark Electronics".
    """

    def __init__(self):
        super().__init__(FUND_REGISTRY["benchmark"])
        # Use Google News as aggregator
        self.search_url = "https://news.google.com/search"
        self.partner_names = self.fund.partner_names

    async def fetch(self, url: Optional[str] = None) -> str:
        if url:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.text

        # Search for Benchmark Capital funding news
        params = {
            "q": '"Benchmark Capital" OR "Benchmark Partners" funding OR investment OR raises',
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

        # Google News article cards
        for card in soup.select("article, [class*='article'], [data-article]"):
            title_el = card.select_one("h3, h4, .title, a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            # Apply negative keyword filter
            if self._should_exclude(title):
                continue

            # Extract link
            link_el = card.select_one("a[href]")
            if not link_el:
                continue
            url = link_el.get("href", "")

            # Skip if URL doesn't look like a news article
            if not url or "google.com" in url:
                # Try to extract actual URL from Google redirect
                if "./articles/" in url:
                    continue

            # Extract date
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

            # Verify this is actually about Benchmark Capital
            if not self._verify_benchmark_mention(text):
                text = ""  # Will be filtered out

        except Exception:
            text = self._extract_text(raw.html)

        return NormalizedArticle(
            url=raw.url,
            title=raw.title,
            text=text,
            published_date=raw.published_date,
        )

    def _should_exclude(self, text: str) -> bool:
        """Check if text contains excluded keywords."""
        text_lower = text.lower()
        exclude_keywords = [
            "benchmark international",
            "benchmark electronics",
            "benchmark capital management",  # Different fund
        ]
        return any(kw in text_lower for kw in exclude_keywords)

    def _verify_benchmark_mention(self, text: str) -> bool:
        """Verify the article mentions Benchmark Capital properly."""
        text_lower = text.lower()

        # Must mention Benchmark Capital or a partner name
        if "benchmark capital" in text_lower or "benchmark partners" in text_lower:
            return True

        # Check for partner names
        for partner in self.partner_names:
            if partner.lower() in text_lower:
                return True

        return False


def create_scraper() -> BenchmarkScraper:
    return BenchmarkScraper()

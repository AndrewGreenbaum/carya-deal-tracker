"""
Redpoint Ventures Scraper.

Source: https://redpoint.com/content-hub/
Format: HTML

Filter by tag 'Funding News'.
"""

from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import SimpleHTMLScraper, RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class RedpointScraper(SimpleHTMLScraper):
    """
    Scraper for Redpoint Ventures content hub.

    Key: Filter by 'Funding News' tag.
    """

    def __init__(self):
        super().__init__(FUND_REGISTRY["redpoint"])
        self.base_url = "https://redpoint.com"

    async def fetch(self, url: Optional[str] = None) -> str:
        target_url = url or f"{self.base_url}/content-hub/"
        response = await self.client.get(target_url)
        response.raise_for_status()
        return response.text

    async def parse(self, html: str) -> List[RawArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles = []
        seen_urls = set()

        selectors = [
            "article",
            ".content-item",
            ".post-card",
            ".news-item",
            "[class*='content']",
            "[class*='article']",
            ".card",
        ]

        for selector in selectors:
            for card in soup.select(selector):
                title_el = card.select_one("h2, h3, h4, .title, [class*='title'], a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                if not title or len(title) < 10:
                    continue

                link_el = card.select_one("a[href]")
                if not link_el:
                    link_el = card.find_parent("a")
                if not link_el:
                    continue

                url = link_el.get("href", "")

                if url and not url.startswith("http"):
                    url = f"{self.base_url}{url}"

                if url in seen_urls or not url:
                    continue
                seen_urls.add(url)

                date_el = card.select_one("time, .date, [class*='date']")
                pub_date = None
                if date_el:
                    date_str = date_el.get("datetime") or date_el.get_text(strip=True)
                    pub_date = self._parse_date(date_str)

                # Extract tags for filtering
                tags = []
                for tag_el in card.select(".tag, .category, [class*='tag'], [class*='category']"):
                    tag_text = tag_el.get_text(strip=True)
                    if tag_text:
                        tags.append(tag_text.lower())

                # Check if this is funding news (required keyword)
                card_text = card.get_text(strip=True).lower()
                is_funding_news = (
                    "funding news" in card_text or
                    "funding" in tags or
                    "funding news" in tags or
                    any("funding" in t for t in tags)
                )

                # Include article, noting if it's funding news
                if is_funding_news:
                    tags.append("funding_news")

                articles.append(RawArticle(
                    url=url,
                    title=title,
                    html=str(card),
                    published_date=pub_date,
                    tags=tags,
                ))

        return articles

    async def normalize(self, raw: RawArticle) -> NormalizedArticle:
        try:
            full_html = await self.fetch(raw.url)
            text = self._extract_text(full_html)

            # Flag funding news articles
            if "funding_news" in raw.tags:
                text = f"[FUNDING NEWS]\n{text}"

        except Exception:
            text = self._extract_text(raw.html)

        return NormalizedArticle(
            url=raw.url,
            title=raw.title,
            text=text,
            published_date=raw.published_date,
            tags=raw.tags,
        )


def create_scraper() -> RedpointScraper:
    return RedpointScraper()

"""
Greylock Partners Scraper.

Source: https://greylock.com/news
Format: HTML

Monitor Medium/Blog; prioritize Partner-authored posts.
"""

from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import SimpleHTMLScraper, RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class GreylockScraper(SimpleHTMLScraper):
    """
    Scraper for Greylock news page.

    Key: Prioritize Partner-authored posts.
    """

    # Known Greylock partners
    PARTNERS = [
        "reid hoffman",
        "david sze",
        "josh elman",
        "sarah guo",
        "saam motamedi",
        "seth rosenberg",
        "jerry chen",
        "asheem chandna",
        "mike duboe",
        "corinne riley",
    ]

    def __init__(self):
        super().__init__(FUND_REGISTRY["greylock"])
        self.base_url = "https://greylock.com"

    async def fetch(self, url: Optional[str] = None) -> str:
        target_url = url or f"{self.base_url}/news"
        response = await self.client.get(target_url)
        response.raise_for_status()
        return response.text

    async def parse(self, html: str) -> List[RawArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles = []
        seen_urls = set()

        selectors = [
            "article",
            ".news-item",
            ".post-card",
            ".blog-post",
            "[class*='news']",
            "[class*='post']",
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

                # Handle Medium links
                if url and not url.startswith("http"):
                    url = f"{self.base_url}{url}"

                if url in seen_urls or not url:
                    continue
                seen_urls.add(url)

                # Extract author
                author = None
                author_el = card.select_one(".author, [class*='author'], .byline")
                if author_el:
                    author = author_el.get_text(strip=True)

                date_el = card.select_one("time, .date, [class*='date']")
                pub_date = None
                if date_el:
                    date_str = date_el.get("datetime") or date_el.get_text(strip=True)
                    pub_date = self._parse_date(date_str)

                tags = []
                for tag_el in card.select(".tag, .category, [class*='tag']"):
                    tag_text = tag_el.get_text(strip=True).lower()
                    if tag_text:
                        tags.append(tag_text)

                articles.append(RawArticle(
                    url=url,
                    title=title,
                    html=str(card),
                    published_date=pub_date,
                    author=author,
                    tags=tags,
                ))

        return articles

    async def normalize(self, raw: RawArticle) -> NormalizedArticle:
        try:
            full_html = await self.fetch(raw.url)
            soup = BeautifulSoup(full_html, "lxml")
            text = self._extract_text(full_html)

            # Try to find author in full article
            author = raw.author
            if not author:
                author_el = soup.select_one(".author, [class*='author'], .byline, [rel='author']")
                if author_el:
                    author = author_el.get_text(strip=True)

            # Flag if partner-authored
            if author and self._is_partner_authored(author):
                text = f"[PARTNER POST: {author}]\n{text}"

        except Exception:
            text = self._extract_text(raw.html)
            author = raw.author

        return NormalizedArticle(
            url=raw.url,
            title=raw.title,
            text=text,
            published_date=raw.published_date,
            author=author,
            tags=raw.tags,
        )

    def _is_partner_authored(self, author: str) -> bool:
        """Check if author is a Greylock partner."""
        author_lower = author.lower()
        return any(partner in author_lower for partner in self.PARTNERS)


def create_scraper() -> GreylockScraper:
    return GreylockScraper()

"""
Sequoia Capital Scraper.

Source: https://sequoiacap.com/stories
Format: PLAYWRIGHT (JS hydration required)

Uses Playwright for full JavaScript rendering.
"""

from typing import List, Optional
from bs4 import BeautifulSoup

from ..playwright_scraper import PlaywrightScraper
from ..base_scraper import RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class SequoiaScraper(PlaywrightScraper):
    """
    Scraper for Sequoia Capital news and portfolio announcements.

    Uses Playwright to render JavaScript-heavy pages.
    Distinguishes from HongShan/Peak XV (formerly Sequoia China/India).
    """

    def __init__(self):
        super().__init__(FUND_REGISTRY["sequoia"])
        self.base_url = "https://sequoiacap.com"

    async def fetch(self, url: Optional[str] = None) -> str:
        target_url = url or f"{self.base_url}/stories"

        html = await self.fetch_rendered(
            target_url,
            wait_selector="article, [class*='story'], [class*='post'], [class*='card']",
            wait_ms=5000
        )
        return html

    async def parse(self, html: str) -> List[RawArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles = []
        seen_urls = set()

        selectors = [
            "article",
            ".story-card",
            "[class*='story']",
            "[class*='post']",
            "[class*='Card']",
            "[data-testid*='story']",
        ]

        for selector in selectors:
            for card in soup.select(selector):
                title_el = card.select_one("h2, h3, h4, .title, [class*='title'], [class*='Title']")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                if not title or len(title) < 10:
                    continue

                # Filter out HongShan/Peak XV content
                if self._is_non_us(title):
                    continue

                # Extract link
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

                # Extract date
                date_el = card.select_one("time, .date, [class*='date'], [class*='Date']")
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
            full_html = await self.fetch_rendered(
                raw.url,
                wait_selector="article, .story-content, .post-content, main",
                wait_ms=3000
            )
            soup = BeautifulSoup(full_html, "lxml")

            content_el = soup.select_one(
                "article, .story-content, .post-content, [class*='Content'], main"
            )

            if content_el:
                for el in content_el.select("nav, header, footer, .share, script, style"):
                    el.decompose()
                text = content_el.get_text(separator="\n", strip=True)
            else:
                text = self._extract_text(full_html)

            author = None
            author_el = soup.select_one(".author, [class*='author'], [class*='Author']")
            if author_el:
                author = author_el.get_text(strip=True)

        except Exception:
            text = self._extract_text(raw.html)
            author = None

        return NormalizedArticle(
            url=raw.url,
            title=raw.title,
            text=text,
            published_date=raw.published_date,
            author=author,
        )

    def _is_non_us(self, title: str) -> bool:
        """Check if content is from HongShan or Peak XV (non-US)."""
        non_us_keywords = [
            "hongshan", "peak xv", "sequoia india", "sequoia china",
            "sequoia southeast asia", "sequoia sea"
        ]
        title_lower = title.lower()
        return any(kw in title_lower for kw in non_us_keywords)


def create_scraper() -> SequoiaScraper:
    return SequoiaScraper()

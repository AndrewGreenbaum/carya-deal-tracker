"""
Founders Fund Scraper.

Source: https://foundersfund.com/news/
Format: PLAYWRIGHT (JS hydration required)

Uses Playwright for full JavaScript rendering.
Also monitors /portfolio/ for stealth additions via DOM-diffing.
"""

from typing import List, Optional
from bs4 import BeautifulSoup

from ..playwright_scraper import PlaywrightScraper
from ..base_scraper import RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class FoundersFundScraper(PlaywrightScraper):
    """
    Scraper for Founders Fund news and portfolio announcements.

    Uses Playwright to render JavaScript-heavy pages.
    Supports stealth detection via portfolio page monitoring.
    """

    def __init__(self):
        super().__init__(FUND_REGISTRY["founders_fund"])
        self.base_url = "https://foundersfund.com"

    async def fetch(self, url: Optional[str] = None) -> str:
        target_url = url or f"{self.base_url}/news/"

        html = await self.fetch_rendered(
            target_url,
            wait_selector="article, .news-item, [class*='post'], [class*='article'], [class*='card']",
            wait_ms=5000
        )
        return html

    async def parse(self, html: str) -> List[RawArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles = []
        seen_urls = set()

        selectors = [
            "article",
            ".news-item",
            "[class*='post']",
            "[class*='article']",
            "[class*='Card']",
            "[class*='news']",
        ]

        for selector in selectors:
            for card in soup.select(selector):
                title_el = card.select_one("h2, h3, h4, .title, [class*='title'], [class*='Title']")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                if not title or len(title) < 10:
                    continue

                # Skip non-funding content
                if not self._is_funding_related(title):
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
                wait_selector="article, .post-content, .entry-content, main",
                wait_ms=3000
            )
            soup = BeautifulSoup(full_html, "lxml")

            content_el = soup.select_one(
                "article, .post-content, .entry-content, [class*='Content'], main"
            )

            if content_el:
                for el in content_el.select("nav, header, footer, .share, script, style"):
                    el.decompose()
                text = content_el.get_text(separator="\n", strip=True)
            else:
                text = self._extract_text(full_html)

        except Exception:
            text = self._extract_text(raw.html)

        return NormalizedArticle(
            url=raw.url,
            title=raw.title,
            text=text,
            published_date=raw.published_date,
        )

    def _is_funding_related(self, title: str) -> bool:
        """Check if title suggests funding news."""
        keywords = [
            "raises", "raised", "funding", "investment", "series",
            "seed", "round", "portfolio", "backs", "backed", "led",
            "million", "billion", "$", "announces", "investment"
        ]
        title_lower = title.lower()
        return any(kw in title_lower for kw in keywords)

    async def fetch_portfolio(self) -> List[str]:
        """
        Fetch current portfolio companies for stealth detection.

        Returns list of company names currently on portfolio page.
        """
        try:
            html = await self.fetch_rendered(
                f"{self.base_url}/portfolio/",
                wait_selector="[class*='company'], [class*='portfolio'], [class*='grid']",
                wait_ms=5000
            )
            soup = BeautifulSoup(html, "lxml")

            companies = []
            for el in soup.select("[class*='company'], [class*='portfolio'], [class*='Company']"):
                name_el = el.select_one("h3, h4, .name, [class*='name'], [class*='Name']")
                if name_el:
                    companies.append(name_el.get_text(strip=True))

            return companies
        except Exception:
            return []


def create_scraper() -> FoundersFundScraper:
    return FoundersFundScraper()

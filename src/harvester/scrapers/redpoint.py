"""
Redpoint Ventures Scraper.

Source: https://www.redpoint.com/content-hub/
Format: HTML (React/Gatsby - requires www. prefix)

The content hub has funding news articles with card-module classes.
Filter for articles linking to /content-hub/category/funding-news/.
"""

from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import SimpleHTMLScraper, RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class RedpointScraper(SimpleHTMLScraper):
    """
    Scraper for Redpoint Ventures content hub.

    Key: Uses www.redpoint.com (redirects from non-www).
    Looks for card-module elements with funding news.
    """

    def __init__(self):
        super().__init__(FUND_REGISTRY["redpoint"])
        # IMPORTANT: Must use www. prefix - site redirects without it
        self.base_url = "https://www.redpoint.com"

    async def fetch(self, url: Optional[str] = None) -> str:
        target_url = url or f"{self.base_url}/content-hub/"
        response = await self.client.get(target_url, follow_redirects=True)
        response.raise_for_status()
        return response.text

    async def parse(self, html: str) -> List[RawArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles = []
        seen_urls = set()

        # Updated selectors for Redpoint's Gatsby-based site
        selectors = [
            "[class*='card-module']",  # Main card container
            "[class*='Card']",
            "article",
            ".content-item",
        ]

        for selector in selectors:
            for card in soup.select(selector):
                # Find title - Redpoint uses card-module--title class
                title_el = card.select_one(
                    "[class*='title'], h2, h3, h4, "
                    "[class*='Title'], [class*='headline']"
                )
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                if not title or len(title) < 10:
                    continue

                # Find link - prioritize content-hub links
                link_el = card.select_one(
                    "a[href*='/content-hub/'], "
                    "a[href*='/blog/'], "
                    "[class*='cardLink'], "
                    "a[href]"
                )
                if not link_el:
                    link_el = card.find_parent("a")
                if not link_el:
                    continue

                url = link_el.get("href", "")

                # Make absolute URL
                if url and not url.startswith("http"):
                    url = f"{self.base_url}{url}"

                # Skip category pages and non-content URLs
                if "/category/" in url and "funding-news" not in url:
                    continue
                if url in seen_urls or not url:
                    continue

                # Must be a Redpoint content URL
                if "redpoint.com" not in url:
                    continue

                seen_urls.add(url)

                # Extract date if available
                date_el = card.select_one("time, .date, [class*='date']")
                pub_date = None
                if date_el:
                    date_str = date_el.get("datetime") or date_el.get_text(strip=True)
                    pub_date = self._parse_date(date_str)

                # Extract category/overline for tagging
                tags = []
                overline_el = card.select_one(
                    "[class*='overline'], [class*='category'], "
                    "[class*='tag'], .category"
                )
                if overline_el:
                    tag_text = overline_el.get_text(strip=True).lower()
                    if tag_text:
                        tags.append(tag_text)

                # Check if this is funding news
                card_text = card.get_text(strip=True).lower()
                is_funding_news = (
                    "funding" in card_text or
                    "series" in card_text or
                    "investment" in card_text or
                    "leading" in title.lower() or
                    any("funding" in t for t in tags)
                )

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
            if "funding_news" in (raw.tags or []):
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

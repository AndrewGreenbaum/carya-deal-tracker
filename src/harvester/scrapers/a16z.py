"""
a16z (Andreessen Horowitz) Scraper.

Source: https://a16z.com/news-content/
Format: PLAYWRIGHT (JavaScript-rendered content)

a16z uses heavy JS rendering - requires Playwright for scraping.
"""

from typing import List, Optional
from datetime import date
from bs4 import BeautifulSoup

from ..playwright_scraper import PlaywrightScraper
from ..base_scraper import RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class A16ZScraper(PlaywrightScraper):
    """
    Scraper for a16z news and announcements.

    Uses Playwright to render JavaScript-heavy pages.
    """

    def __init__(self):
        super().__init__(FUND_REGISTRY["a16z"])
        self.base_url = "https://a16z.com"

    async def fetch(self, url: Optional[str] = None) -> str:
        target_url = url or f"{self.base_url}/news-content/"

        # Use Playwright to render the JavaScript content
        html = await self.fetch_rendered(
            target_url,
            wait_selector="article, [class*='card'], [class*='post']",
            wait_ms=5000  # Extra wait for dynamic content
        )
        return html

    async def parse(self, html: str) -> List[RawArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # a16z uses various card/article patterns
        selectors = [
            "article",
            ".post-card",
            ".news-item",
            "[class*='PostCard']",
            "[class*='NewsCard']",
            "[class*='article-card']",
            "[data-testid*='post']",
            "[data-testid*='article']",
        ]

        seen_urls = set()

        for selector in selectors:
            for card in soup.select(selector):
                # Extract title
                title_el = card.select_one("h2, h3, h4, .title, [class*='title'], [class*='Title']")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                if not title or len(title) < 10:
                    continue

                # Extract link
                link_el = card.select_one("a[href]")
                if not link_el:
                    # Try parent
                    link_el = card.find_parent("a")
                if not link_el:
                    continue

                url = link_el.get("href", "")

                # Make URL absolute
                if url and not url.startswith("http"):
                    url = f"{self.base_url}{url}"

                # Skip duplicates and non-article URLs
                if url in seen_urls or not url or "/news" not in url.lower() and "/portfolio" not in url.lower():
                    if "/announcement" not in url.lower() and "a16z.com" not in url:
                        continue

                seen_urls.add(url)

                # Extract date
                date_el = card.select_one("time, .date, [class*='date'], [class*='Date']")
                pub_date = None
                if date_el:
                    date_str = date_el.get("datetime") or date_el.get_text(strip=True)
                    pub_date = self._parse_date(date_str)

                # Extract tags
                tags = []
                for tag_el in card.select(".tag, .category, [class*='tag'], [class*='category']"):
                    tag_text = tag_el.get_text(strip=True).lower()
                    if tag_text:
                        tags.append(tag_text)

                articles.append(RawArticle(
                    url=url,
                    title=title,
                    html=str(card),
                    published_date=pub_date,
                    tags=tags
                ))

        return articles

    async def normalize(self, raw: RawArticle) -> NormalizedArticle:
        try:
            # Fetch full article with Playwright
            full_html = await self.fetch_rendered(
                raw.url,
                wait_selector="article, .post-content, [class*='content']",
                wait_ms=3000
            )
            soup = BeautifulSoup(full_html, "lxml")

            # Find main content
            content_el = soup.select_one(
                "article, .post-content, .entry-content, [class*='ArticleContent'], [class*='PostContent'], main"
            )

            if content_el:
                for el in content_el.select("nav, header, footer, .share, .related, script, style"):
                    el.decompose()
                text = content_el.get_text(separator="\n", strip=True)
            else:
                text = self._extract_text(full_html)

            # Extract author
            author = None
            author_el = soup.select_one(".author, [class*='author'], [class*='Author'], [rel='author']")
            if author_el:
                author = author_el.get_text(strip=True)

        except Exception as e:
            text = self._extract_text(raw.html)
            author = None

        return NormalizedArticle(
            url=raw.url,
            title=raw.title,
            text=text,
            published_date=raw.published_date,
            author=author,
            tags=raw.tags
        )


def create_scraper() -> A16ZScraper:
    return A16ZScraper()

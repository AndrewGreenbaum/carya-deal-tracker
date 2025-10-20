"""
Greylock Partners Scraper.

Source: https://greylock.com/blog/portfolio-news/
Format: PLAYWRIGHT (JavaScript-rendered content)

Portfolio news page; requires Playwright for JS rendering.
"""

from typing import List, Optional
from bs4 import BeautifulSoup

from ..playwright_scraper import PlaywrightScraper
from ..base_scraper import RawArticle, NormalizedArticle
from ...config.funds import FUND_REGISTRY


class GreylockScraper(PlaywrightScraper):
    """
    Scraper for Greylock portfolio news page.

    Uses Playwright to render JavaScript-heavy pages.
    Prioritizes Partner-authored posts.
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
        target_url = url or f"{self.base_url}/blog/portfolio-news/"

        # Use Playwright to render the JavaScript content
        html = await self.fetch_rendered(
            target_url,
            wait_selector="article, [class*='post'], [class*='card'], [class*='blog']",
            wait_ms=5000
        )
        return html

    async def parse(self, html: str) -> List[RawArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles = []
        seen_urls = set()

        # Try multiple selectors for Greylock's layout
        selectors = [
            "article",
            ".post-card",
            ".blog-post",
            "[class*='post']",
            "[class*='Post']",
            "[class*='card']",
            "[class*='Card']",
            "[class*='blog']",
            "[class*='Blog']",
            "a[href*='/blog/']",
        ]

        for selector in selectors:
            for card in soup.select(selector):
                # Extract title
                title_el = card.select_one("h2, h3, h4, .title, [class*='title'], [class*='Title'], a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)

                if not title or len(title) < 10:
                    continue

                # Extract link
                link_el = card.select_one("a[href]")
                if not link_el:
                    if card.name == "a":
                        link_el = card
                    else:
                        link_el = card.find_parent("a")
                if not link_el:
                    continue

                url = link_el.get("href", "")

                # Make URL absolute
                if url and not url.startswith("http"):
                    url = f"{self.base_url}{url}"

                # Skip non-blog URLs
                if "/blog/" not in url and "/portfolio-news" not in url:
                    continue

                if url in seen_urls or not url:
                    continue
                seen_urls.add(url)

                # Extract author
                author = None
                author_el = card.select_one(".author, [class*='author'], [class*='Author'], .byline")
                if author_el:
                    author = author_el.get_text(strip=True)

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
                    author=author,
                    tags=tags,
                ))

        return articles

    async def normalize(self, raw: RawArticle) -> NormalizedArticle:
        try:
            # Fetch full article with Playwright
            full_html = await self.fetch_rendered(
                raw.url,
                wait_selector="article, .post-content, [class*='content'], main",
                wait_ms=3000
            )
            soup = BeautifulSoup(full_html, "lxml")

            # Find main content
            content_el = soup.select_one(
                "article, .post-content, .entry-content, [class*='Content'], main"
            )

            if content_el:
                # Remove navigation and non-content elements
                for el in content_el.select("nav, header, footer, .share, .related, script, style"):
                    el.decompose()
                text = content_el.get_text(separator="\n", strip=True)
            else:
                text = self._extract_text(full_html)

            # Try to find author in full article
            author = raw.author
            if not author:
                author_el = soup.select_one(".author, [class*='author'], [class*='Author'], .byline, [rel='author']")
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

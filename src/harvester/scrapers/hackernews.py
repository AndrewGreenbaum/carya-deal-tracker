"""
Hacker News Scraper - Monitor HN for "Launch HN" posts and funding news.

Hacker News is the tech community's front page. Companies often post "Launch HN"
or "Show HN" before or alongside funding announcements.

Key signals:
- "Launch HN" posts = company launching, often precedes/coincides with funding
- "Show HN" posts = new product, potential funding signal
- Funding news discussions in comments

Data Sources:
1. HN API: https://hacker-news.firebaseio.com/v0/
2. HN Search (Algolia): https://hn.algolia.com/api/
"""

import asyncio
import httpx
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from urllib.parse import quote

from ..base_scraper import NormalizedArticle
from ...config.settings import settings


# HN API endpoints
HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
HN_ALGOLIA_API = "https://hn.algolia.com/api/v1"

# Search queries for funding news
HN_FUNDING_QUERIES = [
    "funding",
    "raises",
    "series a",
    "series b",
    "seed round",
    "YC",
]

# Fund patterns for matching
HN_FUND_PATTERNS = {
    "founders_fund": ["founders fund", "peter thiel"],
    "benchmark": ["benchmark capital", "benchmark"],
    "sequoia": ["sequoia capital", "sequoia"],
    "khosla": ["khosla ventures", "khosla"],
    "index": ["index ventures"],
    "a16z": ["andreessen horowitz", "a16z"],
    "insight": ["insight partners"],
    "bessemer": ["bessemer venture", "bessemer", "bvp"],
    "redpoint": ["redpoint ventures", "redpoint"],
    "greylock": ["greylock partners", "greylock"],
    "gv": ["google ventures"],
    "menlo": ["menlo ventures"],
    "usv": ["union square ventures", "usv"],
    "thrive": ["thrive capital"],
    "accel": ["accel partners", "accel"],
    "felicis": ["felicis ventures", "felicis"],
    "general_catalyst": ["general catalyst"],
    "first_round": ["first round capital", "first round"],
}


@dataclass
class HNStory:
    """Hacker News story/post."""
    id: int
    title: str
    url: Optional[str]
    text: Optional[str]  # For self-posts (Ask HN, Show HN, Launch HN)
    author: str
    score: int
    comments: int
    created_at: datetime
    story_type: str  # "story", "show_hn", "ask_hn", "launch_hn"


class HackerNewsScraper:
    """
    Scraper for Hacker News posts and discussions.

    Monitors:
    - "Launch HN" posts (startup launches)
    - "Show HN" posts (new products)
    - Funding announcement discussions
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "BudTracker/1.0 (Investment Research)"}
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def search_algolia(
        self,
        query: str,
        tags: Optional[str] = None,
        num_results: int = 50,
        hours_back: int = 168,  # 7 days
    ) -> List[Dict[str, Any]]:
        """
        Search HN using Algolia API.

        Args:
            query: Search query
            tags: Filter tags (e.g., "story", "show_hn", "ask_hn")
            num_results: Max results to return
            hours_back: Look back this many hours

        Returns:
            List of search result dicts
        """
        try:
            cutoff = int((datetime.utcnow() - timedelta(hours=hours_back)).timestamp())

            params = {
                "query": query,
                "hitsPerPage": num_results,
                "numericFilters": f"created_at_i>{cutoff}",
            }
            if tags:
                params["tags"] = tags

            response = await self.client.get(
                f"{HN_ALGOLIA_API}/search",
                params=params,
            )
            response.raise_for_status()

            data = response.json()
            return data.get("hits", [])

        except Exception as e:
            print(f"Error searching HN Algolia: {e}")
            return []

    async def fetch_story(self, story_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single story from HN API."""
        try:
            response = await self.client.get(
                f"{HN_API_BASE}/item/{story_id}.json"
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching HN story {story_id}: {e}")
            return None

    async def fetch_top_stories(self, limit: int = 100) -> List[int]:
        """Fetch current top story IDs."""
        try:
            response = await self.client.get(f"{HN_API_BASE}/topstories.json")
            response.raise_for_status()
            return response.json()[:limit]
        except Exception as e:
            print(f"Error fetching HN top stories: {e}")
            return []

    async def fetch_new_stories(self, limit: int = 100) -> List[int]:
        """Fetch newest story IDs."""
        try:
            response = await self.client.get(f"{HN_API_BASE}/newstories.json")
            response.raise_for_status()
            return response.json()[:limit]
        except Exception as e:
            print(f"Error fetching HN new stories: {e}")
            return []

    def parse_story(self, hit: Dict[str, Any]) -> HNStory:
        """Parse Algolia search hit into HNStory."""
        title = hit.get("title", "")

        # Determine story type
        story_type = "story"
        if title.lower().startswith("launch hn"):
            story_type = "launch_hn"
        elif title.lower().startswith("show hn"):
            story_type = "show_hn"
        elif title.lower().startswith("ask hn"):
            story_type = "ask_hn"

        created = datetime.fromtimestamp(hit.get("created_at_i", 0))

        return HNStory(
            id=hit.get("objectID", 0),
            title=title,
            url=hit.get("url"),
            text=hit.get("story_text"),
            author=hit.get("author", ""),
            score=hit.get("points", 0),
            comments=hit.get("num_comments", 0),
            created_at=created,
            story_type=story_type,
        )

    async def search_launch_hn(self, hours_back: int = 168) -> List[HNStory]:
        """Search for Launch HN posts."""
        hits = await self.search_algolia(
            query="Launch HN",
            tags="story",
            hours_back=hours_back,
        )
        return [self.parse_story(h) for h in hits]

    async def search_show_hn(self, hours_back: int = 168) -> List[HNStory]:
        """Search for Show HN posts."""
        hits = await self.search_algolia(
            query="Show HN",
            tags="show_hn",
            hours_back=hours_back,
        )
        return [self.parse_story(h) for h in hits]

    async def search_funding_news(self, hours_back: int = 168) -> List[HNStory]:
        """Search for funding-related posts."""
        all_stories = []
        seen_ids = set()

        for query in HN_FUNDING_QUERIES:
            hits = await self.search_algolia(
                query=query,
                tags="story",
                hours_back=hours_back,
                num_results=30,
            )
            for hit in hits:
                story_id = hit.get("objectID")
                if story_id not in seen_ids:
                    seen_ids.add(story_id)
                    all_stories.append(self.parse_story(hit))

            await asyncio.sleep(0.3)

        return all_stories

    def match_tracked_fund(self, story: HNStory) -> Optional[str]:
        """Check if story mentions any tracked fund."""
        search_text = f"{story.title} {story.text or ''}".lower()

        for slug, patterns in HN_FUND_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in search_text:
                    return slug

        return None

    def is_funding_related(self, story: HNStory) -> bool:
        """Check if story is funding-related."""
        funding_keywords = [
            "raises", "raised", "funding", "series", "seed", "million", "billion",
            "led by", "investment", "round", "valuation", "venture", "capital",
            "yc", "y combinator"
        ]
        text = f"{story.title} {story.text or ''}".lower()
        return any(kw in text for kw in funding_keywords)

    def story_to_article(self, story: HNStory) -> NormalizedArticle:
        """Convert HN story to NormalizedArticle."""
        # Build text content
        text_parts = [
            f"Hacker News: {story.title}",
            f"Type: {story.story_type}",
            f"Score: {story.score} points",
            f"Comments: {story.comments}",
            f"Author: {story.author}",
        ]

        if story.text:
            text_parts.append(f"\n{story.text}")

        if story.url:
            text_parts.append(f"\nLink: {story.url}")

        hn_url = f"https://news.ycombinator.com/item?id={story.id}"
        text_parts.append(f"HN Discussion: {hn_url}")

        # Check for fund matches
        fund_slug = self.match_tracked_fund(story)

        return NormalizedArticle(
            url=story.url or hn_url,
            title=story.title,
            text="\n".join(text_parts),
            published_date=story.created_at.date(),
            author=story.author,
            tags=['hackernews', story.story_type],
            fund_slug=fund_slug or "",
            fetched_at=datetime.utcnow(),
        )

    async def scrape_all(self, hours_back: int = 168) -> List[NormalizedArticle]:
        """
        Full scraping pipeline for Hacker News.

        Args:
            hours_back: Look back this many hours (default 7 days)

        Returns:
            List of NormalizedArticle objects.
        """
        articles = []
        seen_ids = set()

        # Get Launch HN posts (high signal for new startups)
        launch_stories = await self.search_launch_hn(hours_back=hours_back)
        for story in launch_stories:
            if story.id not in seen_ids:
                seen_ids.add(story.id)
                articles.append(self.story_to_article(story))

        await asyncio.sleep(0.5)

        # Get Show HN posts (product launches)
        show_stories = await self.search_show_hn(hours_back=hours_back)
        for story in show_stories:
            if story.id not in seen_ids:
                seen_ids.add(story.id)
                # Only include if potentially funding-related or high score
                if self.is_funding_related(story) or story.score > 100:
                    articles.append(self.story_to_article(story))

        await asyncio.sleep(0.5)

        # Get funding news
        funding_stories = await self.search_funding_news(hours_back=hours_back)
        for story in funding_stories:
            if story.id not in seen_ids:
                seen_ids.add(story.id)
                articles.append(self.story_to_article(story))

        return articles


# Convenience function
async def run_hackernews_scraper(hours_back: int = 168) -> List[NormalizedArticle]:
    """Run Hacker News scraper and return articles."""
    async with HackerNewsScraper() as scraper:
        return await scraper.scrape_all(hours_back=hours_back)

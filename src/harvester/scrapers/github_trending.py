"""
GitHub Trending Scraper - Monitor trending repositories for dev tools before funding.

GitHub Trending tracks the most popular repos by stars gained. Many dev tools
appear on trending BEFORE they announce funding - it's an early signal.

Key insight: Dev tools that trend often have VC interest. Track them early.

Source: https://github.com/trending
"""

import asyncio
import re
import httpx
from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional
from bs4 import BeautifulSoup

from ..base_scraper import NormalizedArticle
from ...config.settings import settings


# GitHub Trending URLs by time range
GITHUB_TRENDING_URLS = {
    "daily": "https://github.com/trending?since=daily",
    "weekly": "https://github.com/trending?since=weekly",
    "monthly": "https://github.com/trending?since=monthly",
}

# Languages relevant for enterprise dev tools
ENTERPRISE_LANGUAGES = [
    "python", "typescript", "go", "rust", "java", "javascript"
]

# Keywords that indicate enterprise/B2B dev tools
ENTERPRISE_KEYWORDS = [
    # Infrastructure
    "api", "sdk", "cli", "framework", "platform", "infrastructure",
    "database", "queue", "cache", "storage", "cloud",
    # AI/ML
    "ai", "ml", "llm", "gpt", "agent", "embedding", "vector",
    "langchain", "llamaindex", "rag", "transformer",
    # DevOps
    "deploy", "ci", "cd", "kubernetes", "docker", "terraform",
    "monitoring", "observability", "logging", "tracing",
    # Security
    "security", "auth", "identity", "encryption", "compliance",
    # Data
    "analytics", "etl", "pipeline", "warehouse", "lakehouse",
]


@dataclass
class TrendingRepo:
    """A trending GitHub repository."""
    name: str  # org/repo
    url: str
    description: str
    language: Optional[str]
    stars_today: int
    total_stars: int
    forks: int
    built_by: List[str]  # Contributor avatars/names


class GitHubTrendingScraper:
    """
    Scraper for GitHub Trending repositories.

    Monitors trending repos to find dev tools before they announce funding.
    """

    def __init__(self, languages: Optional[List[str]] = None):
        """
        Initialize scraper.

        Args:
            languages: Filter by programming languages. If None, uses all.
        """
        self.languages = languages or ENTERPRISE_LANGUAGES
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            }
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def fetch_trending_page(self, language: Optional[str] = None, since: str = "daily") -> str:
        """Fetch GitHub trending page."""
        try:
            base_url = f"https://github.com/trending"
            if language:
                base_url = f"https://github.com/trending/{language}"

            url = f"{base_url}?since={since}"
            response = await self.client.get(url)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"Error fetching GitHub trending: {e}")
            return ""

    def parse_trending_repos(self, html: str) -> List[TrendingRepo]:
        """Parse trending repositories from HTML."""
        repos = []
        soup = BeautifulSoup(html, 'lxml')

        for article in soup.select('article.Box-row'):
            try:
                # Repository name (org/repo)
                name_el = article.select_one('h2 a')
                if not name_el:
                    continue

                name = name_el.get_text(strip=True).replace('\n', '').replace(' ', '')
                url = f"https://github.com{name_el.get('href', '')}"

                # Description
                desc_el = article.select_one('p')
                description = desc_el.get_text(strip=True) if desc_el else ''

                # Language
                lang_el = article.select_one('[itemprop="programmingLanguage"]')
                language = lang_el.get_text(strip=True) if lang_el else None

                # Stars today
                stars_today_el = article.select_one('.float-sm-right')
                stars_today = 0
                if stars_today_el:
                    match = re.search(r'(\d+(?:,\d+)*)\s*stars?\s*today', stars_today_el.get_text())
                    if match:
                        stars_today = int(match.group(1).replace(',', ''))

                # Total stars and forks
                star_fork_els = article.select('a.Link--muted')
                total_stars = 0
                forks = 0
                for el in star_fork_els:
                    href = el.get('href', '')
                    text = el.get_text(strip=True).replace(',', '')
                    if '/stargazers' in href:
                        total_stars = int(text) if text.isdigit() else 0
                    elif '/forks' in href:
                        forks = int(text) if text.isdigit() else 0

                # Built by contributors
                built_by = []
                contributor_els = article.select('img.avatar')
                for img in contributor_els[:5]:
                    alt = img.get('alt', '')
                    if alt and alt.startswith('@'):
                        built_by.append(alt[1:])

                repos.append(TrendingRepo(
                    name=name,
                    url=url,
                    description=description,
                    language=language,
                    stars_today=stars_today,
                    total_stars=total_stars,
                    forks=forks,
                    built_by=built_by,
                ))

            except Exception as e:
                print(f"Error parsing trending repo: {e}")
                continue

        return repos

    async def fetch_all_trending(self, since: str = "daily") -> List[TrendingRepo]:
        """Fetch trending repos across multiple languages."""
        all_repos = []
        seen_names = set()

        # First get all languages trending
        html = await self.fetch_trending_page(since=since)
        repos = self.parse_trending_repos(html)
        for repo in repos:
            if repo.name not in seen_names:
                seen_names.add(repo.name)
                all_repos.append(repo)

        await asyncio.sleep(0.5)

        # Then get language-specific trending
        for lang in self.languages:
            html = await self.fetch_trending_page(language=lang, since=since)
            repos = self.parse_trending_repos(html)
            for repo in repos:
                if repo.name not in seen_names:
                    seen_names.add(repo.name)
                    all_repos.append(repo)
            await asyncio.sleep(0.5)

        return all_repos

    def is_enterprise_devtool(self, repo: TrendingRepo) -> bool:
        """Check if repo is an enterprise dev tool."""
        text = f"{repo.name} {repo.description}".lower()
        return any(kw in text for kw in ENTERPRISE_KEYWORDS)

    async def fetch_repo_readme(self, repo: TrendingRepo) -> Optional[str]:
        """Fetch README content for a repository."""
        try:
            # Try common README paths
            for readme_path in ['README.md', 'readme.md', 'README.rst', 'README']:
                raw_url = f"https://raw.githubusercontent.com/{repo.name}/main/{readme_path}"
                response = await self.client.get(raw_url)
                if response.status_code == 200:
                    return response.text[:5000]  # Truncate

                # Try master branch
                raw_url = f"https://raw.githubusercontent.com/{repo.name}/master/{readme_path}"
                response = await self.client.get(raw_url)
                if response.status_code == 200:
                    return response.text[:5000]

            return None
        except Exception as e:
            print(f"Error fetching README for {repo.name}: {e}")
            return None

    async def repo_to_article(self, repo: TrendingRepo) -> NormalizedArticle:
        """Convert GitHub repo to NormalizedArticle."""
        # Try to fetch README for more context
        readme = await self.fetch_repo_readme(repo)

        text_parts = [
            f"GitHub Trending: {repo.name}",
            f"Description: {repo.description}",
            f"Language: {repo.language or 'Unknown'}",
            f"Stars Today: {repo.stars_today:,}",
            f"Total Stars: {repo.total_stars:,}",
            f"Forks: {repo.forks:,}",
        ]

        if repo.built_by:
            text_parts.append(f"Top Contributors: {', '.join(repo.built_by)}")

        text_parts.append(f"\nGitHub URL: {repo.url}")

        if readme:
            text_parts.append(f"\n--- README ---\n{readme[:2000]}")

        return NormalizedArticle(
            url=repo.url,
            title=f"GitHub Trending: {repo.name} (+{repo.stars_today} stars) - {repo.description[:80]}",
            text="\n".join(text_parts),
            published_date=date.today(),
            author=repo.built_by[0] if repo.built_by else None,
            tags=['github', 'trending', repo.language.lower() if repo.language else 'unknown'],
            fund_slug="",  # No specific fund
            fetched_at=datetime.utcnow(),
        )

    async def scrape_all(self, since: str = "daily", filter_enterprise: bool = True) -> List[NormalizedArticle]:
        """
        Full scraping pipeline for GitHub Trending.

        Args:
            since: Time range - "daily", "weekly", or "monthly"
            filter_enterprise: Only include enterprise dev tools

        Returns:
            List of NormalizedArticle objects.
        """
        repos = await self.fetch_all_trending(since=since)

        articles = []
        for repo in repos:
            # Optionally filter for enterprise tools
            if filter_enterprise and not self.is_enterprise_devtool(repo):
                continue

            article = await self.repo_to_article(repo)
            articles.append(article)
            await asyncio.sleep(0.2)

        return articles


# Convenience function
async def run_github_trending_scraper(since: str = "daily") -> List[NormalizedArticle]:
    """Run GitHub Trending scraper and return articles."""
    async with GitHubTrendingScraper() as scraper:
        return await scraper.scrape_all(since=since)

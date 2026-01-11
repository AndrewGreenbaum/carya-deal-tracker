"""
VC Portfolio Page Diff - Catch Stealth Additions Before Announcements.

Monitors VC fund portfolio pages for NEW company additions.
VCs often add companies to their portfolio pages BEFORE public announcements.

How it works:
1. Scrape portfolio pages for all listed companies
2. Compare against previous snapshot
3. New companies = stealth additions (pre-announcement)
4. Store snapshot for next comparison

This is one of the BEST free sources for true stealth detection.
"""

import asyncio
import json
import logging
import re
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass
from datetime import datetime, date, timezone
from typing import List, Optional, Dict, Set

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..base_scraper import NormalizedArticle
from ...config.settings import settings
from ...archivist.database import get_session
from ...archivist.models import PortfolioSnapshot

logger = logging.getLogger(__name__)


# Portfolio page URLs for tracked funds
PORTFOLIO_URLS = {
    "a16z": {
        "url": "https://a16z.com/portfolio/",
        "selector": ".portfolio-company, .company-card, [class*='portfolio'], a[href*='/companies/']",
        "name_selector": "h3, h4, .company-name, .title",
    },
    "sequoia": {
        "url": "https://www.sequoiacap.com/our-companies/",
        "selector": ".company-card, .portfolio-item, [class*='company']",
        "name_selector": "h3, h4, .name, .company-name",
    },
    "founders_fund": {
        "url": "https://foundersfund.com/portfolio/",
        "selector": ".portfolio-company, .company, [class*='portfolio']",
        "name_selector": "h3, h4, .name, .title",
    },
    "benchmark": {
        "url": "https://www.benchmark.com/portfolio/",
        "selector": ".company, .portfolio-item, [class*='company']",
        "name_selector": "h3, h4, .name",
    },
    "greylock": {
        "url": "https://greylock.com/portfolio/",
        "selector": ".company-card, .portfolio-company, [class*='portfolio']",
        "name_selector": "h3, h4, .company-name",
    },
    "khosla": {
        "url": "https://www.khoslaventures.com/portfolio",
        "selector": ".portfolio-company, .company, [class*='portfolio']",
        "name_selector": "h3, h4, .name",
    },
    "index": {
        "url": "https://www.indexventures.com/companies/",
        "selector": ".company, .portfolio-item",
        "name_selector": "h3, h4, .name",
    },
    "insight": {
        "url": "https://www.insightpartners.com/portfolio/",
        "selector": ".portfolio-company, .company",
        "name_selector": "h3, h4, .name",
    },
    "bessemer": {
        "url": "https://www.bvp.com/portfolio",
        "selector": ".portfolio-company, .company",
        "name_selector": "h3, h4, .name",
    },
    "accel": {
        "url": "https://www.accel.com/companies",
        "selector": ".company, .portfolio-item",
        "name_selector": "h3, h4, .name",
    },
    "felicis": {
        "url": "https://www.felicis.com/portfolio",
        "selector": ".portfolio-company, .company",
        "name_selector": "h3, h4, .name",
    },
    "general_catalyst": {
        "url": "https://www.generalcatalyst.com/portfolio",
        "selector": ".portfolio-company, .company",
        "name_selector": "h3, h4, .name",
    },
    "first_round": {
        "url": "https://firstround.com/companies/",
        "selector": ".company, .portfolio-item",
        "name_selector": "h3, h4, .name",
    },
    "menlo": {
        "url": "https://www.menlovc.com/portfolio/",
        "selector": ".portfolio-company, .company",
        "name_selector": "h3, h4, .name",
    },
    "redpoint": {
        "url": "https://www.redpoint.com/companies/",
        "selector": ".company, .portfolio-item",
        "name_selector": "h3, h4, .name",
    },
    "usv": {
        "url": "https://www.usv.com/companies/",
        "selector": ".company, .portfolio-item",
        "name_selector": "h3, h4, .name",
    },
    "thrive": {
        "url": "https://www.thrivecap.com/portfolio/",
        "selector": ".portfolio-company, .company",
        "name_selector": "h3, h4, .name",
    },
    "gv": {
        "url": "https://www.gv.com/portfolio/",
        "selector": ".portfolio-company, .company",
        "name_selector": "h3, h4, .name",
    },
}

# Snapshots are stored in database (portfolio_snapshots table)
# This survives Railway redeploys and ensures accurate diff detection.


@dataclass
class PortfolioCompany:
    """Company found on a VC portfolio page."""
    name: str
    fund_slug: str
    url: Optional[str] = None
    description: Optional[str] = None
    first_seen: Optional[date] = None


@dataclass
class PortfolioDiff:
    """Diff between two portfolio snapshots."""
    fund_slug: str
    new_companies: List[PortfolioCompany]
    removed_companies: List[str]
    snapshot_date: date


class PortfolioDiffScraper:
    """
    Monitor VC portfolio pages for new company additions.

    Detects stealth additions by comparing current portfolio
    against stored snapshot from previous run.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
            follow_redirects=True,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def _load_snapshot(self, fund_slug: str) -> Set[str]:
        """Load previous snapshot of company names from database."""
        async with get_session() as session:
            stmt = select(PortfolioSnapshot).where(PortfolioSnapshot.fund_slug == fund_slug)
            result = await session.execute(stmt)
            snapshot = result.scalar_one_or_none()

            if snapshot:
                try:
                    companies = json.loads(snapshot.companies_json)
                    return set(companies)
                except json.JSONDecodeError:
                    return set()
            return set()

    async def _save_snapshot(self, fund_slug: str, companies: Set[str]) -> None:
        """Save current snapshot of company names to database."""
        async with get_session() as session:
            companies_json = json.dumps(list(companies))

            # Use ON CONFLICT to upsert
            stmt = pg_insert(PortfolioSnapshot).values(
                fund_slug=fund_slug,
                companies_json=companies_json,
                updated_at=datetime.now(timezone.utc),
            ).on_conflict_do_update(
                index_elements=['fund_slug'],
                set_={
                    'companies_json': companies_json,
                    'updated_at': datetime.now(timezone.utc),
                }
            )
            await session.execute(stmt)
            await session.commit()

    async def scrape_portfolio_page(self, fund_slug: str) -> List[PortfolioCompany]:
        """
        Scrape a single fund's portfolio page for company names.
        """
        config = PORTFOLIO_URLS.get(fund_slug)
        if not config:
            return []

        try:
            response = await self.client.get(config["url"])
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'lxml')
            companies = []
            seen_names = set()

            # Try multiple selector strategies
            selectors = [
                config["selector"],
                "a[href*='portfolio']",
                "a[href*='company']",
                ".company",
                "[class*='portfolio']",
                "article",
            ]

            for selector in selectors:
                try:
                    elements = soup.select(selector)
                    for elem in elements:
                        # Try to extract company name
                        name = None

                        # Try name selector first
                        name_elem = elem.select_one(config.get("name_selector", "h3, h4"))
                        if name_elem:
                            name = name_elem.get_text(strip=True)

                        # Fallback to element text
                        if not name:
                            name = elem.get_text(strip=True)

                        # Clean up name
                        if name:
                            # Remove common suffixes
                            name = re.sub(r'\s*(Inc\.?|LLC|Ltd\.?|Corp\.?)$', '', name, flags=re.IGNORECASE)
                            name = name.strip()

                            # Skip if too short, too long, or already seen
                            if len(name) < 2 or len(name) > 100:
                                continue
                            if name.lower() in seen_names:
                                continue

                            # Skip common non-company text
                            skip_words = ['portfolio', 'companies', 'view all', 'load more', 'filter', 'sort']
                            if any(skip in name.lower() for skip in skip_words):
                                continue

                            seen_names.add(name.lower())

                            # Extract URL if available
                            url = None
                            link = elem.find('a') if elem.name != 'a' else elem
                            if link and link.get('href'):
                                href = link.get('href')
                                if href.startswith('/'):
                                    href = f"https://{config['url'].split('/')[2]}{href}"
                                url = href

                            companies.append(PortfolioCompany(
                                name=name,
                                fund_slug=fund_slug,
                                url=url,
                                first_seen=date.today(),
                            ))
                except Exception:
                    continue

                # If we found companies, stop trying other selectors
                # FIX: Lower threshold - some funds show fewer than 5 companies
                if len(companies) >= 1:
                    break

            return companies

        except Exception as e:
            logger.error(f"Error scraping {fund_slug} portfolio: {e}")
            return []

    async def get_portfolio_diff(self, fund_slug: str) -> PortfolioDiff:
        """
        Get diff between current portfolio and stored snapshot.
        """
        # Load previous snapshot from database
        previous = await self._load_snapshot(fund_slug)

        # Scrape current portfolio
        current_companies = await self.scrape_portfolio_page(fund_slug)
        current_names = {c.name.lower() for c in current_companies}

        # Find new companies (in current but not in previous)
        new_names = current_names - previous
        new_companies = [c for c in current_companies if c.name.lower() in new_names]

        # Find removed companies (in previous but not in current)
        removed_names = previous - current_names

        # Save current snapshot to database for next run
        await self._save_snapshot(fund_slug, current_names)

        return PortfolioDiff(
            fund_slug=fund_slug,
            new_companies=new_companies,
            removed_companies=list(removed_names),
            snapshot_date=date.today(),
        )

    def diff_to_article(self, company: PortfolioCompany) -> NormalizedArticle:
        """Convert new portfolio company to NormalizedArticle."""
        text_parts = [
            f"[STEALTH PORTFOLIO ADDITION DETECTED]",
            f"",
            f"Fund: {company.fund_slug}",
            f"Company: {company.name}",
            f"First Seen: {company.first_seen}",
            f"",
            f"This company was just added to {company.fund_slug}'s portfolio page.",
            f"This often happens BEFORE any public funding announcement.",
        ]

        if company.url:
            text_parts.append(f"\nPortfolio URL: {company.url}")

        # Generate fund-specific URL if company URL not available
        fund_config = PORTFOLIO_URLS.get(company.fund_slug, {})
        fund_url = fund_config.get("url", f"https://{company.fund_slug}.com/portfolio/")
        fallback_url = f"{fund_url.rstrip('/')}#{company.name.lower().replace(' ', '-')}"

        return NormalizedArticle(
            url=company.url or fallback_url,
            title=f"Stealth Addition: {company.name} added to {company.fund_slug} portfolio",
            text="\n".join(text_parts),
            published_date=company.first_seen or date.today(),
            author=f"{company.fund_slug} Portfolio",
            tags=["portfolio_diff", "stealth", "pre_announcement", company.fund_slug],
            fund_slug=company.fund_slug,
            fetched_at=datetime.now(timezone.utc),
        )

    async def scrape_all(
        self,
        fund_slugs: Optional[List[str]] = None,
    ) -> List[NormalizedArticle]:
        """
        Scrape all portfolio pages and return new additions.

        Args:
            fund_slugs: Optional list of specific funds to check.
                       If None, checks all configured funds.

        Returns:
            List of NormalizedArticle for each new company detected.
        """
        if fund_slugs is None:
            fund_slugs = list(PORTFOLIO_URLS.keys())

        all_articles = []

        for fund_slug in fund_slugs:
            if fund_slug not in PORTFOLIO_URLS:
                continue

            diff = await self.get_portfolio_diff(fund_slug)

            # Convert new companies to articles
            for company in diff.new_companies:
                article = self.diff_to_article(company)
                all_articles.append(article)

            # Rate limit between funds (0.3s matches other scrapers)
            await asyncio.sleep(0.3)

        return all_articles

    async def get_full_snapshot(self) -> Dict[str, List[str]]:
        """
        Get current snapshot of all portfolio pages without diffing.
        Useful for initial population.
        """
        snapshots = {}

        for fund_slug in PORTFOLIO_URLS.keys():
            companies = await self.scrape_portfolio_page(fund_slug)
            snapshots[fund_slug] = [c.name for c in companies]
            await self._save_snapshot(fund_slug, {c.name.lower() for c in companies})
            await asyncio.sleep(0.3)

        return snapshots


# Convenience function
async def run_portfolio_diff_scraper(
    fund_slugs: Optional[List[str]] = None,
) -> List[NormalizedArticle]:
    """Run portfolio diff scraper and return new company articles."""
    async with PortfolioDiffScraper() as scraper:
        return await scraper.scrape_all(fund_slugs)

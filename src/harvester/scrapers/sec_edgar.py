"""
SEC EDGAR Form D Monitor - The First Signal.

Monitors SEC EDGAR for Form D filings (private placements).
Form D filings are the ultimate proof of funding rounds and
often appear BEFORE news/PR announcements.

Key insight: When a startup raises money, they must file Form D
with the SEC within 15 days. This is public data we can monitor.
"""

import asyncio
import re
import feedparser
import httpx
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from xml.etree import ElementTree as ET

from ..base_scraper import NormalizedArticle
from ...config.settings import settings
from ...config.funds import FUND_REGISTRY


# SEC EDGAR Atom feed for recent Form D filings
SEC_FORM_D_FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=D&output=atom"

# Alternative: RSS feed for all recent filings
SEC_RECENT_FILINGS = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=D&company=&dateb=&owner=include&count=100&output=atom"

# Fund name variations for matching in SEC filings
SEC_FUND_PATTERNS = {
    "founders_fund": ["founders fund", "ff management"],
    "benchmark": ["benchmark capital", "benchmark partners"],
    "sequoia": ["sequoia capital", "sequoia operations"],
    "khosla": ["khosla ventures"],
    "index": ["index ventures"],
    "a16z": ["andreessen horowitz", "a16z", "ah capital"],
    "insight": ["insight partners", "insight venture"],
    "bessemer": ["bessemer venture", "bvp"],
    "redpoint": ["redpoint ventures"],
    "greylock": ["greylock partners", "greylock management"],
    "gv": ["google ventures", "gv management"],
    "menlo": ["menlo ventures"],
    "usv": ["union square ventures", "usv management"],
    "thrive": ["thrive capital"],
    "accel": ["accel partners", "accel management"],
    "felicis": ["felicis ventures"],
    "general_catalyst": ["general catalyst"],
    "first_round": ["first round capital"],
}


@dataclass
class FormDFiling:
    """Parsed Form D filing from SEC EDGAR."""
    company_name: str
    cik: str  # SEC Central Index Key
    filing_date: date
    form_type: str  # D, D/A (amendment)
    filing_url: str
    accession_number: str

    # Extracted from filing details (if available)
    amount_raised: Optional[str] = None
    total_offering: Optional[str] = None
    investors: List[str] = field(default_factory=list)
    is_first_sale: bool = False
    industry: Optional[str] = None


class SECEdgarScraper:
    """
    Monitor SEC EDGAR for Form D filings.

    Form D = Private placement notification (Reg D exemption)
    This is PROOF of a funding round, filed within 15 days of first sale.

    Workflow:
    1. Fetch recent Form D filings from Atom feed
    2. Parse filing details for amount raised
    3. Cross-reference with tracked fund names
    4. Return as NormalizedArticle for extraction pipeline
    """

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "BudTracker/1.0 (Investment Research Bot)",
                "Accept": "application/atom+xml, application/xml, text/xml",
            }
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def fetch_recent_filings(
        self,
        hours: int = 24,
        max_filings: int = 100,
    ) -> List[FormDFiling]:
        """
        Fetch recent Form D filings from SEC EDGAR.

        Args:
            hours: Look back this many hours
            max_filings: Maximum filings to return

        Returns:
            List of FormDFiling objects
        """
        try:
            response = await self.client.get(SEC_FORM_D_FEED)
            response.raise_for_status()

            # Parse Atom feed
            feed = feedparser.parse(response.text)

            filings = []
            cutoff = datetime.utcnow() - timedelta(hours=hours)

            for entry in feed.entries[:max_filings]:
                # Parse entry
                filing = self._parse_feed_entry(entry)
                if filing and filing.filing_date >= cutoff.date():
                    filings.append(filing)

            return filings

        except Exception as e:
            print(f"SEC EDGAR fetch error: {e}")
            return []

    def _parse_feed_entry(self, entry: Dict[str, Any]) -> Optional[FormDFiling]:
        """Parse a single Atom feed entry into FormDFiling."""
        try:
            # Extract title (company name and form type)
            title = entry.get("title", "")

            # Title format: "D - Company Name (CIK)"
            match = re.match(r"(D(?:/A)?)\s*-\s*(.+?)\s*\((\d+)\)", title)
            if not match:
                return None

            form_type, company_name, cik = match.groups()

            # Extract filing date
            updated = entry.get("updated", "")
            try:
                filing_date = datetime.fromisoformat(updated.replace("Z", "+00:00")).date()
            except (ValueError, TypeError):
                filing_date = date.today()

            # Extract filing URL
            filing_url = entry.get("link", "")

            # Extract accession number from URL
            accession_match = re.search(r"/(\d+-\d+-\d+)", filing_url)
            accession_number = accession_match.group(1) if accession_match else ""

            return FormDFiling(
                company_name=company_name.strip(),
                cik=cik,
                filing_date=filing_date,
                form_type=form_type,
                filing_url=filing_url,
                accession_number=accession_number,
            )

        except Exception as e:
            print(f"Error parsing SEC entry: {e}")
            return None

    async def fetch_filing_details(self, filing: FormDFiling) -> FormDFiling:
        """
        Fetch detailed Form D XML to extract amount raised and investors.

        Form D XML contains:
        - Total Offering Amount
        - Amount Sold
        - Names of Related Persons (investors)
        """
        try:
            # Build URL to Form D XML
            # Format: /Archives/edgar/data/{cik}/{accession}/primary_doc.xml
            accession_clean = filing.accession_number.replace("-", "")
            xml_url = f"https://www.sec.gov/Archives/edgar/data/{filing.cik}/{accession_clean}/primary_doc.xml"

            response = await self.client.get(xml_url)
            if response.status_code != 200:
                return filing

            # Parse XML
            root = ET.fromstring(response.text)
            ns = {"": "http://www.sec.gov/edgar/document/formd"}

            # Extract offering amount
            offering_elem = root.find(".//totalOfferingAmount", ns)
            if offering_elem is not None and offering_elem.text:
                filing.total_offering = f"${int(offering_elem.text):,}"

            amount_sold_elem = root.find(".//totalAmountSold", ns)
            if amount_sold_elem is not None and amount_sold_elem.text:
                filing.amount_raised = f"${int(amount_sold_elem.text):,}"

            # Extract related persons (investors)
            for person in root.findall(".//relatedPersonInfo", ns):
                name_elem = person.find(".//relatedPersonName", ns)
                if name_elem is not None:
                    first = name_elem.find(".//firstName", ns)
                    last = name_elem.find(".//lastName", ns)
                    name = f"{first.text if first is not None else ''} {last.text if last is not None else ''}".strip()
                    if name:
                        filing.investors.append(name)

            # Check if first sale
            first_sale_elem = root.find(".//isFirstSale", ns)
            if first_sale_elem is not None:
                filing.is_first_sale = first_sale_elem.text == "true"

            # Extract industry
            industry_elem = root.find(".//industryGroup/industryGroupType", ns)
            if industry_elem is not None:
                filing.industry = industry_elem.text

            return filing

        except Exception as e:
            print(f"Error fetching filing details: {e}")
            return filing

    def match_tracked_fund(self, filing: FormDFiling) -> Optional[str]:
        """
        Check if filing mentions any tracked fund.

        Returns fund slug if match found, None otherwise.
        """
        # Search in company name and investors
        search_text = f"{filing.company_name} {' '.join(filing.investors)}".lower()

        for slug, patterns in SEC_FUND_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in search_text:
                    return slug

        return None

    async def to_normalized_article(
        self,
        filing: FormDFiling,
        fund_slug: Optional[str] = None,
    ) -> NormalizedArticle:
        """
        Convert Form D filing to NormalizedArticle for extraction pipeline.
        """
        # Build text content for extraction
        text_parts = [
            f"SEC Form D Filing: {filing.company_name}",
            f"Filing Date: {filing.filing_date.isoformat()}",
            f"Form Type: {filing.form_type}",
        ]

        if filing.amount_raised:
            text_parts.append(f"Amount Raised: {filing.amount_raised}")
        if filing.total_offering:
            text_parts.append(f"Total Offering: {filing.total_offering}")
        if filing.investors:
            text_parts.append(f"Related Persons: {', '.join(filing.investors)}")
        if filing.industry:
            text_parts.append(f"Industry: {filing.industry}")
        if filing.is_first_sale:
            text_parts.append("Note: This is the FIRST sale under this offering.")

        text_parts.append(f"\nSEC Filing URL: {filing.filing_url}")

        return NormalizedArticle(
            url=filing.filing_url,
            title=f"SEC Form D: {filing.company_name} - {filing.amount_raised or 'Amount TBD'}",
            text="\n".join(text_parts),
            published_date=filing.filing_date,
            author="SEC EDGAR",
            tags=["sec", "form_d", filing.form_type],
            fund_slug=fund_slug or "",
            fetched_at=datetime.utcnow(),
        )

    async def scrape_all(
        self,
        hours: int = 24,
        fetch_details: bool = True,
    ) -> List[NormalizedArticle]:
        """
        Full scraping pipeline for SEC EDGAR Form D filings.

        Args:
            hours: Look back this many hours
            fetch_details: Whether to fetch full filing XML for details

        Returns:
            List of NormalizedArticle objects
        """
        articles = []
        seen_ciks = set()

        # Fetch recent filings
        filings = await self.fetch_recent_filings(hours=hours)

        for filing in filings:
            # Skip duplicates
            if filing.cik in seen_ciks:
                continue
            seen_ciks.add(filing.cik)

            # Optionally fetch full details
            if fetch_details:
                filing = await self.fetch_filing_details(filing)
                await asyncio.sleep(0.2)  # Rate limit

            # Check for tracked fund match
            fund_slug = self.match_tracked_fund(filing)

            # Convert to article
            article = await self.to_normalized_article(filing, fund_slug)
            articles.append(article)

        return articles


# Convenience function
async def run_sec_edgar_scraper(hours: int = 24) -> List[NormalizedArticle]:
    """Run SEC EDGAR scraper and return normalized articles."""
    async with SECEdgarScraper() as scraper:
        return await scraper.scrape_all(hours=hours)

"""
SEC EDGAR Form D Monitor - The First Signal.

Monitors SEC EDGAR for Form D filings (private placements).
Form D filings are the ultimate proof of funding rounds and
often appear BEFORE news/PR announcements.

Key insight: When a startup raises money, they must file Form D
with the SEC within 15 days. This is public data we can monitor.
"""

import asyncio
import logging
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

logger = logging.getLogger(__name__)


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

# Industries likely to be VC-backed (for stealth detection)
VC_LIKELY_INDUSTRIES = [
    "Technology",
    "Computers",
    "Software",
    "Telecommunications",
    "Biotechnology",
    "Health Care",
    "Energy",
    "Manufacturing",
]

# Minimum amount to consider as likely VC-backed (filters out small raises)
MIN_VC_AMOUNT = 500_000  # $500K+

# State name to 2-letter code mapping for normalization
STATE_CODES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
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
    state_of_incorporation: Optional[str] = None  # For Delaware filtering


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
        # SEC requires a User-Agent with contact info for automated tools
        # See: https://www.sec.gov/os/accessing-edgar-data
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "User-Agent": "BudTracker/1.0 (contact@budtracker.io; Investment Research)",
                "Accept": "application/atom+xml, application/xml, text/xml",
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,  # SEC redirects to new URL structure
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
            logger.error(f"SEC EDGAR fetch error: {e}")
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
            logger.error(f"Error parsing SEC entry: {e}")
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
            # Validate accession_number before constructing URL
            if not filing.accession_number:
                logger.warning(f"Missing accession number for {filing.company_name} (CIK: {filing.cik})")
                return filing

            # Build URL to Form D XML
            # Format: /Archives/edgar/data/{cik}/{accession}/primary_doc.xml
            accession_clean = filing.accession_number.replace("-", "")
            xml_url = f"https://www.sec.gov/Archives/edgar/data/{filing.cik}/{accession_clean}/primary_doc.xml"

            response = await self.client.get(xml_url)
            if response.status_code != 200:
                return filing

            # Parse XML - SEC Form D may use namespace prefixes
            root = ET.fromstring(response.text)

            # Extract offering amount (namespace-agnostic search)
            offering_elem = root.find(".//{*}totalOfferingAmount")
            if offering_elem is None:
                offering_elem = root.find(".//totalOfferingAmount")
            if offering_elem is not None and offering_elem.text and offering_elem.text.strip():
                try:
                    filing.total_offering = f"${int(float(offering_elem.text)):,}"
                except (ValueError, TypeError):
                    pass

            amount_sold_elem = root.find(".//{*}totalAmountSold")
            if amount_sold_elem is None:
                amount_sold_elem = root.find(".//totalAmountSold")
            if amount_sold_elem is not None and amount_sold_elem.text and amount_sold_elem.text.strip():
                try:
                    filing.amount_raised = f"${int(float(amount_sold_elem.text)):,}"
                except (ValueError, TypeError):
                    pass

            # Extract related persons (investors) - look for names in relatedPersonsList
            for person in root.findall(".//relatedPersonInfo"):
                first_elem = person.find(".//firstName")
                last_elem = person.find(".//lastName")
                first = first_elem.text if first_elem is not None and first_elem.text else ""
                last = last_elem.text if last_elem is not None and last_elem.text else ""
                name = f"{first} {last}".strip()
                if name and name not in filing.investors:
                    filing.investors.append(name)

            # Check if first sale - look in typeOfFiling
            # For Form D, we check if this is the first filing (not an amendment)
            amendment_elem = root.find(".//isAmendment")
            if amendment_elem is not None and amendment_elem.text:
                filing.is_first_sale = amendment_elem.text.lower() != "true"
            else:
                # If no amendment flag, check if form type is D (not D/A)
                filing.is_first_sale = filing.form_type == "D"

            # Extract industry from industryGroup
            industry_elem = root.find(".//industryGroupType")
            if industry_elem is not None and industry_elem.text:
                filing.industry = industry_elem.text

            # Extract state of incorporation (jurisdictionOfInc in SEC XML)
            state_elem = root.find(".//jurisdictionOfInc")
            if state_elem is None:
                state_elem = root.find(".//issuerStateOrCountry")
            if state_elem is not None and state_elem.text:
                # Convert full name to 2-letter state code using STATE_CODES mapping
                state_text = state_elem.text.strip().upper()
                if len(state_text) == 2:
                    # Already a 2-letter code
                    filing.state_of_incorporation = state_text
                elif state_text in STATE_CODES:
                    # Use mapping to convert full name to 2-letter code
                    filing.state_of_incorporation = STATE_CODES[state_text]
                else:
                    # Unknown format, store as-is but log warning
                    logger.warning(f"Unknown state format: {state_text}")
                    filing.state_of_incorporation = state_text

            return filing

        except Exception as e:
            logger.error(f"Error fetching filing details for {filing.company_name}: {e}")
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

    def is_likely_vc_backed(self, filing: FormDFiling) -> bool:
        """
        Heuristic check if filing is likely a VC-backed startup.

        Criteria:
        - Industry is in VC_LIKELY_INDUSTRIES (tech/software/biotech)
        - Amount raised >= $500K (filters small raises)
        - First sale indicator (new round, not amendment)

        Returns True if likely VC-backed, False otherwise.
        """
        # Check industry
        industry_match = False
        if filing.industry:
            for vc_industry in VC_LIKELY_INDUSTRIES:
                if vc_industry.lower() in filing.industry.lower():
                    industry_match = True
                    break

        # Check amount (parse from string like "$1,000,000")
        amount_ok = False
        if filing.amount_raised:
            try:
                amount_str = filing.amount_raised.replace("$", "").replace(",", "")
                amount = int(amount_str)
                amount_ok = amount >= MIN_VC_AMOUNT
            except (ValueError, TypeError):
                amount_ok = False
        elif filing.total_offering:
            try:
                amount_str = filing.total_offering.replace("$", "").replace(",", "")
                amount = int(amount_str)
                amount_ok = amount >= MIN_VC_AMOUNT
            except (ValueError, TypeError):
                amount_ok = False

        # Return True if industry matches AND amount is significant
        # OR if it's a first sale (new funding round)
        return (industry_match and amount_ok) or filing.is_first_sale

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
        if filing.state_of_incorporation:
            text_parts.append(f"State of Incorporation: {filing.state_of_incorporation}")
        if filing.is_first_sale:
            text_parts.append("Note: This is the FIRST sale under this offering.")

        text_parts.append(f"\nSEC Filing URL: {filing.filing_url}")

        # Build tags
        tags = ["sec", "form_d", filing.form_type]
        if filing.state_of_incorporation == "DE":
            tags.append("delaware")

        return NormalizedArticle(
            url=filing.filing_url,
            title=f"SEC Form D: {filing.company_name} - {filing.amount_raised or 'Amount TBD'}",
            text="\n".join(text_parts),
            published_date=filing.filing_date,
            author="SEC EDGAR",
            tags=tags,
            fund_slug=fund_slug or "",
            fetched_at=datetime.utcnow(),
        )

    async def scrape_all(
        self,
        hours: int = 24,
        fetch_details: bool = True,
        include_likely_vc: bool = True,
    ) -> List[NormalizedArticle]:
        """
        Full scraping pipeline for SEC EDGAR Form D filings.

        Args:
            hours: Look back this many hours
            fetch_details: Whether to fetch full filing XML for details
            include_likely_vc: Include filings that are likely VC-backed (tech + $500K+)
                              This catches stealth companies before news announcements

        Returns:
            List of NormalizedArticle objects (fund-matched + likely VC-backed)
        """
        articles = []
        articles_fund_matched = []
        articles_likely_vc = []
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

            if fund_slug:
                # Fund-matched filing (high confidence)
                article = await self.to_normalized_article(filing, fund_slug)
                article.tags.append("fund_matched")
                articles_fund_matched.append(article)
            elif include_likely_vc and self.is_likely_vc_backed(filing):
                # Likely VC-backed (stealth detection)
                article = await self.to_normalized_article(filing, None)
                article.tags.append("likely_vc")
                article.tags.append("stealth_candidate")
                articles_likely_vc.append(article)

        # Combine: fund-matched first, then likely VC
        articles = articles_fund_matched + articles_likely_vc
        return articles


# Convenience function
async def run_sec_edgar_scraper(hours: int = 24) -> List[NormalizedArticle]:
    """Run SEC EDGAR scraper and return normalized articles."""
    async with SECEdgarScraper() as scraper:
        return await scraper.scrape_all(hours=hours)

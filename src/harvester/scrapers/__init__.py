"""Fund-specific scraper implementations."""

# Original 5 scrapers
from .a16z import A16ZScraper
from .sequoia import SequoiaScraper
from .benchmark import BenchmarkScraper
from .founders_fund import FoundersFundScraper
from .thrive import ThriveScraper

# Additional 13 scrapers
from .khosla import KhoslaScraper
from .index_ventures import IndexVenturesScraper
from .insight import InsightScraper
from .bessemer import BessemerScraper
from .redpoint import RedpointScraper
from .greylock import GreylockScraper
from .gv import GVScraper
from .menlo import MenloScraper
from .usv import USVScraper
from .accel import AccelScraper
from .felicis import FelicisScraper
from .general_catalyst import GeneralCatalystScraper
from .first_round import FirstRoundScraper

# News aggregator (multi-source)
from .news_aggregator import NewsAggregatorScraper, run_news_aggregator

# New data sources
from .sec_edgar import SECEdgarScraper, run_sec_edgar_scraper
from .brave_search import BraveSearchScraper, run_brave_search_scraper
from .firecrawl_scraper import FirecrawlScraper, scrape_with_firecrawl, run_firecrawl_scraper

__all__ = [
    # Original 5
    "A16ZScraper",
    "SequoiaScraper",
    "BenchmarkScraper",
    "FoundersFundScraper",
    "ThriveScraper",
    # Additional 13
    "KhoslaScraper",
    "IndexVenturesScraper",
    "InsightScraper",
    "BessemerScraper",
    "RedpointScraper",
    "GreylockScraper",
    "GVScraper",
    "MenloScraper",
    "USVScraper",
    "AccelScraper",
    "FelicisScraper",
    "GeneralCatalystScraper",
    "FirstRoundScraper",
    # News aggregator
    "NewsAggregatorScraper",
    "run_news_aggregator",
    # New data sources
    "SECEdgarScraper",
    "run_sec_edgar_scraper",
    "BraveSearchScraper",
    "run_brave_search_scraper",
    "FirecrawlScraper",
    "scrape_with_firecrawl",
    "run_firecrawl_scraper",
]

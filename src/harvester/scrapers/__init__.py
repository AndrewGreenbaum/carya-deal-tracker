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
]

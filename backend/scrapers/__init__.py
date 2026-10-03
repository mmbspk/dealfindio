"""
Scraper module for dealfindio.

Catalog scrapers (catalog_*.py) use the CatalogProduct dataclass and are
invoked via catalog_tasks.run_catalog_crawl. This package exports only the
shared base types used by app code.
"""

from .base import ScrapedProduct, Scraper, ScraperError
from .catalog_base import CatalogProduct

__all__ = [
    "Scraper",
    "ScrapedProduct",
    "ScraperError",
    "CatalogProduct",
]

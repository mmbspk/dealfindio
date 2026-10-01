"""
Scraper module for dealfindio.

Catalog scrapers (catalog_*.py) use the CatalogProduct dataclass and are
invoked via catalog_tasks.run_catalog_crawl. This package exports only the
shared base types used by app code.
"""

from .catalog_base import CatalogProduct
from .base import ScrapedProduct, Scraper, ScraperError

__all__ = [
    "Scraper",
    "ScrapedProduct",
    "ScraperError",
    "CatalogProduct",
]

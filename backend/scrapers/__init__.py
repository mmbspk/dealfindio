"""
Scraper module for dealfindio.

Provides scrapers for major Australian grocery chains:
- Coles specials: Next.js /_next/data API (replaces HTML scraper)
- Woolworths: JSON API with pagination
- ALDI: PDF extraction with OCR fallback
- IGA: HTML parsing with Playwright

Example usage:
    from scrapers import ColesSpecialsScraper, WoolworthsScraper, ALDIScraper, IGAScraper

    scraper = ColesSpecialsScraper(db_session, redis_client)
    result = await scraper.run()
    print(f"Found {result['products_found']} products")
    for product in result['products']:
        print(f"{product.product_name}: ${product.price_cents/100:.2f}")
"""

from .base import Scraper, ScrapedProduct, ScraperError
from .coles import ColesScraper          # kept for backwards-compat / tests
from .specials_coles import ColesSpecialsScraper
from .aldi import ALDIScraper
from .iga import IGAScraper
from .woolworths import WoolworthsScraper
from .drakes import DrakesScraper

__all__ = [
    "Scraper",
    "ScrapedProduct",
    "ScraperError",
    "ColesScraper",
    "ColesSpecialsScraper",
    "WoolworthsScraper",
    "ALDIScraper",
    "IGAScraper",
    "DrakesScraper",
]

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

from .aldi import ALDIScraper
from .base import ScrapedProduct, Scraper, ScraperError
from .coles import ColesScraper  # kept for backwards-compat / tests
from .drakes import DrakesScraper
from .iga import IGAScraper
from .specials_coles import ColesSpecialsScraper
from .woolworths import WoolworthsScraper

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

"""
Base scraper class with abstract methods for grocery chain flyer scrapers.

Provides the core interface that all scraper implementations must follow.
Defines the scraping workflow: discover source -> fetch flyer -> parse flyer.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import redis
from sqlalchemy.orm import Session


class ScraperError(Exception):
    """Base exception for scraper errors."""

    pass


@dataclass
class ScrapedProduct:
    """
    Represents a single product extracted from a flyer.

    Attributes:
        product_name: Human-readable product name/description
        price_cents: Current sale price in cents (e.g., 599 = $5.99)
        was_price_cents: Original price in cents before discount (optional)
        gtin: Global Trade Item Number / barcode (optional)
        store_product_id: Store's own internal product ID (Stockcode, productId, sku).
            Used to match against products already stored by the catalog scraper via
            external_id lookup — avoids creating duplicates when both pipelines run.
        valid_from: Date this price is valid from
        valid_to: Date this price expires
        image_url: Product image URL, when the source provides one (optional)
        category: Source category/department name (optional)
        brand: Brand/manufacturer name (optional)
    """

    product_name: str
    price_cents: int
    was_price_cents: Optional[int] = None
    gtin: Optional[str] = None
    store_product_id: Optional[str] = None
    valid_from: date = None
    valid_to: date = None
    image_url: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None


class Scraper(ABC):
    """
    Abstract base class for grocery chain flyer scrapers.

    Defines the standard workflow for scraping flyers:
    1. discover_source() - Find the current flyer source (URL, API, etc.)
    2. fetch_flyer() - Download/retrieve the flyer data
    3. parse_flyer() - Extract products from the flyer

    All scrapers must implement these three methods.
    """

    def __init__(
        self,
        db_session: Optional[Session] = None,
        redis_client: Optional[redis.Redis] = None,
    ):
        """
        Initialize scraper with optional database and cache connections.

        Args:
            db_session: SQLAlchemy database session for storing results
            redis_client: Redis client for caching intermediate data
        """
        self.db_session = db_session
        self.redis_client = redis_client
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def discover_source(self) -> Any:
        """
        Discover the current flyer source.

        This method is responsible for finding where the latest flyer is located.
        It might:
        - Return a static URL (if the flyer URL never changes)
        - Fetch a listing page and extract the latest flyer link
        - Query an API for the current flyer

        Returns:
            Source identifier (URL, path, API endpoint, etc.) used in fetch_flyer()

        Raises:
            ScraperError: If source cannot be discovered
        """
        pass

    @abstractmethod
    async def fetch_flyer(self, source: str) -> Any:
        """
        Fetch/download the flyer from the source.

        This method retrieves the actual flyer data:
        - HTML page (for Playwright DOM scraping)
        - JSON response (for API-based scrapers)
        - PDF bytes (for PDF scrapers)
        - Or any other format

        Args:
            source: Source identifier from discover_source()

        Returns:
            Flyer data in whatever format the scraper uses (HTML, JSON, bytes, etc.)

        Raises:
            ScraperError: On network errors, timeouts, etc.
        """
        pass

    @abstractmethod
    async def parse_flyer(self, flyer_data: Any) -> list[ScrapedProduct]:
        """
        Parse the flyer data and extract products.

        This method is responsible for extracting structured product information
        from the raw flyer data. Each scraper must implement its own parsing logic
        specific to the target store's format.

        Args:
            flyer_data: Raw flyer data from fetch_flyer()
                       (HTML string, JSON dict, PDF bytes, etc.)

        Returns:
            List of ScrapedProduct objects extracted from the flyer

        Raises:
            ScraperError: On parsing errors
        """
        pass

    async def run(self) -> dict[str, Any]:
        """
        Execute the complete scraping workflow.

        Orchestrates the three-step process:
        1. Discover where the latest flyer is
        2. Fetch the flyer data
        3. Parse products from the flyer

        Returns:
            Result dictionary with:
            - status: 'success' or 'error'
            - products_found: Number of products extracted
            - products: List of ScrapedProduct objects
            - error: Error message (if status='error')

        Example:
            >>> scraper = ColesScraper(db_session, redis_client)
            >>> result = await scraper.run()
            >>> print(f"Found {result['products_found']} products")
        """
        try:
            self.logger.info(f"Starting scrape run for {self.__class__.__name__}")

            # Step 1: Discover the source
            source = await self.discover_source()
            self.logger.info(f"Discovered source: {source}")

            # Step 2: Fetch the flyer
            flyer_data = await self.fetch_flyer(source)
            self.logger.info(f"Fetched flyer from {source}")

            # Step 3: Parse the flyer
            products = await self.parse_flyer(flyer_data)
            self.logger.info(f"Parsed {len(products)} products from flyer")

            return {
                "status": "success",
                "products_found": len(products),
                "products": products,
                "error": None,
            }

        except Exception as e:
            self.logger.error(f"Scrape failed: {e}", exc_info=True)
            return {
                "status": "error",
                "products_found": 0,
                "products": [],
                "error": str(e),
            }

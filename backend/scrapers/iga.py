"""
IGA Australia flyer scraper using the igashop.com.au storefront API.

Fetches all on-special products from the IGA promotions endpoint, which returns
products with a wasPriceNumeric field indicating they are on special.

Source: https://www.igashop.com.au/api/storefront/stores/{storeId}/promotions/products
"""

import logging
from datetime import date, timedelta
from typing import Optional

import httpx
import redis
from sqlalchemy.orm import Session

from .base import ScrapedProduct, Scraper, ScraperError

logger = logging.getLogger(__name__)

_BASE = "https://www.igashop.com.au/api/storefront"
_DEFAULT_STORE_ID = "32600"
_SHOPPING_MODE_ID = "11111111-1111-1111-1111-111111111111"

_HEADERS = {
    "x-shopping-mode": _SHOPPING_MODE_ID,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.igashop.com.au/",
}


def _to_cents(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(float(val) * 100)
    except (TypeError, ValueError):
        return None


class IGAScraper(Scraper):
    """
    Scraper for IGA Australia specials via igashop.com.au storefront API.

    Uses the /promotions/products endpoint which returns all on-special products
    directly, paginated via take/skip parameters.
    """

    PAGE_SIZE = 48
    TIMEOUT_S = 30.0
    MAX_PAGES = 200
    store_id: str = _DEFAULT_STORE_ID

    def __init__(
        self,
        db_session: Optional[Session] = None,
        redis_client: Optional[redis.Redis] = None,
    ):
        super().__init__(db_session, redis_client)
        self.logger = logging.getLogger(__name__)

    async def discover_source(self) -> str:
        return f"{_BASE}/stores/{self.store_id}/promotions/products"

    async def fetch_flyer(self, source: str) -> list[dict]:
        """Fetch all IGA specials from the promotions API, paginated."""
        self.logger.info(f"Fetching IGA specials from {source}")
        all_items: list[dict] = []

        async with httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
        ) as client:
            skip = 0
            total_reported: Optional[int] = None

            for _ in range(self.MAX_PAGES):
                params = {"take": self.PAGE_SIZE, "skip": skip}
                try:
                    resp = await client.get(source, params=params)
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise ScraperError(
                        f"HTTP {e.response.status_code} from IGA promotions API"
                    ) from e
                except httpx.RequestError as e:
                    raise ScraperError(f"Request error fetching IGA specials: {e}") from e

                try:
                    data = resp.json()
                except Exception as e:
                    raise ScraperError(f"IGA promotions API returned invalid JSON: {e}") from e

                if total_reported is None:
                    total_reported = data.get("total") or data.get("count") or 0
                    self.logger.info(f"IGA reports {total_reported} specials total")

                items = data.get("items") or []
                if not items:
                    break

                all_items.extend(items)
                self.logger.debug(
                    f"skip={skip}: {len(items)} items (total so far: {len(all_items)})"
                )
                skip += self.PAGE_SIZE

                if len(items) < self.PAGE_SIZE:
                    break
                if total_reported and skip >= total_reported:
                    break

        self.logger.info(f"Fetched {len(all_items)} IGA specials")
        return all_items

    async def parse_flyer(self, flyer_data: list[dict]) -> list[ScrapedProduct]:
        """Parse IGA promotions API items into ScrapedProduct instances."""
        try:
            products: list[ScrapedProduct] = []

            for idx, item in enumerate(flyer_data):
                try:
                    name = (item.get("name") or "").strip()
                    if not name:
                        self.logger.debug(f"Item {idx}: no name — skipping")
                        continue

                    price_cents = _to_cents(
                        item.get("priceNumeric") or item.get("wholePrice")
                    )
                    if price_cents is None or price_cents <= 0:
                        self.logger.debug(f"Item {idx}: no valid price — skipping")
                        continue

                    was_cents = _to_cents(item.get("wasPriceNumeric"))
                    if was_cents is not None and was_cents <= price_cents:
                        was_cents = None

                    sku = str(item.get("productId") or item.get("sku") or "").strip() or None

                    products.append(ScrapedProduct(
                        product_name=name,
                        price_cents=price_cents,
                        was_price_cents=was_cents,
                        store_product_id=sku,
                        valid_from=date.today(),
                        valid_to=date.today() + timedelta(days=7),
                    ))

                except Exception as e:
                    self.logger.warning(f"Item {idx}: parse error — {e}")
                    continue

            self.logger.info(f"Parsed {len(products)} products from IGA")
            return products

        except Exception as e:
            self.logger.error(f"Parse failed: {e}", exc_info=True)
            raise ScraperError(f"IGA parsing failed: {e}") from e

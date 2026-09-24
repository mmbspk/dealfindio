"""
Woolworths Australia specials scraper using the internal search API.

Fetches all on-special products by querying the Woolworths search API with
IsSpecial=true. Uses httpx with a real browser session (homepage cookie warm-up)
to avoid bot-detection. Paginates until all specials are retrieved.

Source: https://www.woolworths.com.au/apis/ui/Search/products?IsSpecial=true
"""

import logging
from datetime import date, timedelta
from typing import Optional

import httpx
import redis
from sqlalchemy.orm import Session

from scrapers.catalog_base import get_scraper_proxy as _get_proxy

from .base import ScrapedProduct, Scraper, ScraperError

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-AU,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.woolworths.com.au/shop/specials/all",
    "Origin": "https://www.woolworths.com.au",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Cache-Control": "no-cache",
}

_HOMEPAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AU,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

_SEARCH_URL = "https://www.woolworths.com.au/apis/ui/Search/products"
_IMAGE_BASE = "https://cdn0.woolworths.media/content/wowproductimages/medium"


class WoolworthsScraper(Scraper):
    """
    Scraper for Woolworths Australia specials via the internal search API.

    Warms up a browser session on the homepage to get valid cookies, then
    queries the search API with IsSpecial=true to paginate all on-sale products.
    The search term 'a' is used as a broad filter — it reliably returns the full
    specials catalogue (typically 1400+ products).
    """

    PAGE_SIZE = 36
    TIMEOUT_S = 30
    MAX_RETRIES = 3
    MAX_PAGES = 100

    def __init__(
        self,
        db_session: Optional[Session] = None,
        redis_client: Optional[redis.Redis] = None,
    ):
        super().__init__(db_session, redis_client)
        self.logger = logging.getLogger(__name__)

    async def discover_source(self) -> str:
        return _SEARCH_URL

    async def fetch_flyer(self, source: str) -> list[dict]:
        """
        Fetch all Woolworths specials via paginated JSON API.

        Warms up a session with a homepage hit first to get cookies, then
        queries the search API. Returns raw product dicts from all pages.
        """
        self.logger.info("Fetching Woolworths specials via search API")
        last_err = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                all_products: list[dict] = []

                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=self.TIMEOUT_S,
                    proxies=_get_proxy(),
                    http2=False,
                ) as client:
                    # Warm up session with homepage to get cookies
                    await client.get(
                        "https://www.woolworths.com.au/",
                        headers=_HOMEPAGE_HEADERS,
                        timeout=15,
                    )
                    self.logger.debug("Homepage warm-up done, cookies set")

                    page = 1
                    total_reported = None

                    while page <= self.MAX_PAGES:
                        params = {
                            "searchTerm": "a",
                            "pageNumber": page,
                            "pageSize": self.PAGE_SIZE,
                            "sortType": "TraderRelevance",
                            "IsSpecial": "true",
                            "inStockProductsOnly": "false",
                        }
                        resp = await client.get(
                            _SEARCH_URL,
                            params=params,
                            headers=_HEADERS,
                        )
                        resp.raise_for_status()
                        data = resp.json()

                        if total_reported is None:
                            total_reported = data.get("SearchResultsCount", 0)
                            self.logger.info(
                                f"Woolworths reports {total_reported} specials total"
                            )

                        # Products are double-nested: outer[].Products[0]
                        outer = data.get("Products", [])
                        page_products: list[dict] = []
                        for bundle in outer:
                            page_products.extend(bundle.get("Products", []))

                        all_products.extend(page_products)
                        self.logger.debug(
                            f"Page {page}: {len(page_products)} products "
                            f"(total so far: {len(all_products)})"
                        )

                        if len(page_products) < self.PAGE_SIZE:
                            break
                        page += 1

                self.logger.info(
                    f"Fetched {len(all_products)} Woolworths specials "
                    f"across {page} pages"
                )
                return all_products

            except httpx.HTTPStatusError as e:
                last_err = ScraperError(f"HTTP {e.response.status_code} from Woolworths API")
                self.logger.warning(f"Attempt {attempt}: {last_err}")
            except httpx.RequestError as e:
                last_err = ScraperError(f"Request error fetching Woolworths: {e}")
                self.logger.warning(f"Attempt {attempt}: {last_err}")

        raise last_err or ScraperError("All fetch attempts failed")

    async def parse_flyer(self, flyer_data: list[dict]) -> list[ScrapedProduct]:
        """
        Parse Woolworths product dicts into ScrapedProduct instances.

        Each dict has keys: Name, Price, WasPrice, Stockcode, IsOnSpecial,
        SmallImageFile/MediumImageFile, SavingsAmount, etc.
        """
        try:
            products: list[ScrapedProduct] = []

            for idx, item in enumerate(flyer_data):
                try:
                    name = (item.get("Name") or item.get("DisplayName") or "").strip()
                    if not name:
                        self.logger.debug(f"Item {idx}: no name — skipping")
                        continue

                    price_raw = item.get("Price")
                    if price_raw is None:
                        self.logger.debug(f"Item {idx}: no price — skipping")
                        continue

                    try:
                        price_cents = int(float(price_raw) * 100)
                    except (ValueError, TypeError):
                        self.logger.warning(f"Item {idx}: unparseable price {price_raw!r}")
                        continue

                    was_price_cents: Optional[int] = None
                    was_raw = item.get("WasPrice") or item.get("InstoreWasPrice")
                    if was_raw is not None:
                        try:
                            was_price_cents = int(float(was_raw) * 100)
                        except (ValueError, TypeError):
                            pass

                    stockcode = item.get("Stockcode")
                    store_product_id = str(stockcode) if stockcode else None

                    image_url = item.get("MediumImageFile") or item.get("SmallImageFile")
                    if not image_url and stockcode:
                        image_url = f"{_IMAGE_BASE}/{stockcode}.jpg"

                    products.append(ScrapedProduct(
                        product_name=name,
                        price_cents=price_cents,
                        was_price_cents=was_price_cents,
                        store_product_id=store_product_id,
                        image_url=image_url,
                        valid_from=date.today(),
                        valid_to=date.today() + timedelta(days=7),
                    ))

                except Exception as e:
                    self.logger.warning(f"Item {idx}: parse error — {e}")
                    continue

            self.logger.info(f"Parsed {len(products)} products from Woolworths")
            return products

        except Exception as e:
            self.logger.error(f"Parse failed: {e}", exc_info=True)
            raise ScraperError(f"Parsing failed: {e}") from e

"""
Coles Australia flyer scraper using HTTP fetch + BeautifulSoup.

Fetches the Coles specials page with httpx (no browser needed — the page is
fully server-side rendered and accessible with a standard browser UA). Parses
product tiles with BeautifulSoup.

Source: https://www.coles.com.au/on-special
"""

import logging
import re
from datetime import date, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup
import redis
from sqlalchemy.orm import Session

from .base import Scraper, ScrapedProduct, ScraperError
from scrapers.catalog_base import get_scraper_proxy as _get_proxy

logger = logging.getLogger(__name__)

_HEADERS = {
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


class ColesScraper(Scraper):
    """
    Scraper for Coles Australia specials catalogue.

    Fetches the Coles /on-special page via httpx and parses product tiles
    with BeautifulSoup. The page is SSR'd by Next.js so no headless browser
    is required.
    """

    COLES_SPECIALS_URL = "https://www.coles.com.au/on-special"
    TIMEOUT_S = 30
    MAX_RETRIES = 3

    def __init__(
        self,
        db_session: Optional[Session] = None,
        redis_client: Optional[redis.Redis] = None,
    ):
        super().__init__(db_session, redis_client)
        self.logger = logging.getLogger(__name__)

    async def discover_source(self) -> str:
        return self.COLES_SPECIALS_URL

    async def fetch_flyer(self, source: str) -> str:
        """Fetch Coles specials page HTML via httpx with retry on bot-block."""
        self.logger.info(f"Fetching {source}")
        last_err = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(
                    headers=_HEADERS,
                    follow_redirects=True,
                    timeout=self.TIMEOUT_S,
                    http2=False,
                    proxies=_get_proxy(),
                ) as client:
                    # Hit the homepage first to get cookies, then the specials page
                    await client.get("https://www.coles.com.au/", timeout=15)
                    resp = await client.get(source)
                    resp.raise_for_status()
                    html = resp.text

                if "Pardon Our Interruption" in html or "imperva" in html.lower():
                    self.logger.warning(f"Attempt {attempt}: Imperva bot-protection page — retrying")
                    last_err = ScraperError("Coles bot-protection page returned — retry later")
                    continue

                product_count = html.count('data-testid="product-tile"')
                self.logger.info(
                    f"Fetched {len(html)} bytes, {product_count} product-tile markers"
                )
                if product_count == 0:
                    self.logger.warning(f"Attempt {attempt}: no product tiles found — retrying")
                    last_err = ScraperError("No product tiles found in Coles response")
                    continue

                return html

            except httpx.HTTPStatusError as e:
                last_err = ScraperError(f"HTTP error fetching Coles: {e}")
                self.logger.warning(f"Attempt {attempt}: {last_err}")
            except httpx.RequestError as e:
                last_err = ScraperError(f"Request error fetching Coles: {e}")
                self.logger.warning(f"Attempt {attempt}: {last_err}")

        raise last_err or ScraperError("All fetch attempts failed")

    async def parse_flyer(self, flyer_data: str) -> list[ScrapedProduct]:
        """
        Parse Coles HTML and extract product tiles.

        Each tile is a <section data-testid="product-tile"> containing:
          - h2.product__title — product name
          - span[data-testid="product-pricing"] — current price ($X.XX)
          - .price__was — was-price text (e.g. "| Was $6.50")
        """
        try:
            soup = BeautifulSoup(flyer_data, "html.parser")
            tiles = soup.find_all("section", attrs={"data-testid": "product-tile"})
            self.logger.info(f"Found {len(tiles)} product tiles")

            products: list[ScrapedProduct] = []
            for idx, tile in enumerate(tiles):
                try:
                    # Product name
                    name_el = tile.find("h2", class_="product__title")
                    if not name_el:
                        name_el = tile.find("a", attrs={"aria-label": True})
                        product_name = name_el["aria-label"] if name_el else None
                    else:
                        product_name = name_el.get_text(strip=True)

                    if not product_name:
                        self.logger.debug(f"Tile {idx}: no name — skipping")
                        continue

                    # Current price
                    price_el = tile.find("span", attrs={"data-testid": "product-pricing"})
                    if not price_el:
                        self.logger.debug(f"Tile {idx}: no price element — skipping")
                        continue

                    price_text = price_el.get_text(strip=True)
                    price_match = re.search(r"\$?([\d,]+\.?\d*)", price_text)
                    if not price_match:
                        self.logger.debug(f"Tile {idx}: can't parse price '{price_text}'")
                        continue
                    price_cents = int(float(price_match.group(1).replace(",", "")) * 100)

                    # Was-price (optional)
                    was_price_cents: Optional[int] = None
                    was_el = tile.find(class_="price__was")
                    if was_el:
                        was_text = was_el.get_text(strip=True)
                        was_match = re.search(r"\$?([\d,]+\.?\d*)", was_text)
                        if was_match:
                            was_price_cents = int(
                                float(was_match.group(1).replace(",", "")) * 100
                            )

                    products.append(ScrapedProduct(
                        product_name=product_name,
                        price_cents=price_cents,
                        was_price_cents=was_price_cents,
                        valid_from=date.today(),
                        valid_to=date.today() + timedelta(days=7),
                    ))
                    self.logger.debug(
                        f"Tile {idx}: {product_name!r} ${price_cents/100:.2f}"
                        + (f" (was ${was_price_cents/100:.2f})" if was_price_cents else "")
                    )

                except Exception as e:
                    self.logger.warning(f"Tile {idx}: parse error — {e}")
                    continue

            self.logger.info(f"Parsed {len(products)} products from Coles")
            return products

        except Exception as e:
            self.logger.error(f"Parse failed: {e}", exc_info=True)
            raise ScraperError(f"HTML parsing failed: {e}") from e

"""
Drakes Supermarkets weekly specials scraper.

Strategy:
  Drakes Online (Myfoodlink platform) serves products via pjax — the HTML
  page is a Rails app that returns full HTML fragments in response to XHR
  requests with ``X-PJAX: true``.

  Specials endpoint:
    GET /search?page={n}&q[]=special:1&sort_by=top_products
    Headers: X-PJAX=true, X-PJAX-Container=#search-results-products

  Product data is embedded in the HTML card:
    - Name: <span class="talker__product-name">
    - Price: <strong class="price__sell">
    - Was price: <span class="talker__prices__was">
    - Image: <img src="..."> inside <div class="talker__section--image">
    - Slug: <a href="/lines/{slug}">
    - Internal ID: data-talker id="line_{id}"

  No auth required.  Rate limit: polite 0.5s between pages.

  Note: 5,000+ specials per week is normal — Drakes runs broad catalogue-wide
  promotions.  Products without a "was" price are excluded (not real specials).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from scrapers.base import Scraper, ScrapedProduct, ScraperError

logger = logging.getLogger(__name__)

_BASE_URL = "https://015.drakes.com.au"
_SPECIALS_PATH = "/search"
_PAGE_SIZE = 48

_PJAX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "en-AU,en-GB;q=0.9,en;q=0.8",
    "X-PJAX": "true",
    "X-PJAX-Container": "#search-results-products",
    "X-Requested-With": "XMLHttpRequest",
}


def _parse_price(text: str) -> Optional[int]:
    """Parse a price string like '$4.45' or '$4.45 per 500g' into cents."""
    m = re.search(r"\$([\d]+\.[\d]{1,2})", text)
    if not m:
        return None
    try:
        return int(round(float(m.group(1)) * 100))
    except ValueError:
        return None


def _parse_card(card: BeautifulSoup, today: date) -> Optional[ScrapedProduct]:
    """Extract a ScrapedProduct from a single talker card element."""
    # Name
    name_el = card.find(class_="talker__product-name")
    if not name_el:
        return None
    name = name_el.get_text(strip=True)
    if not name:
        return None

    # Current price
    price_el = card.find(class_="price__sell")
    if not price_el:
        return None
    price_cents = _parse_price(price_el.get_text(strip=True))
    if not price_cents:
        return None

    # Was price (only keep real specials — those with a "was" price)
    was_el = card.find(class_="talker__prices__was")
    was_text = was_el.get_text(strip=True) if was_el else ""
    was_cents = _parse_price(was_text) if was_text else None

    # Image
    img_el = card.select_one(".talker__section--image img")
    image_url = img_el.get("src") or img_el.get("data-src") if img_el else None

    # Product slug → used as external_id proxy
    link_el = card.select_one("a[href^='/lines/']")
    slug = link_el["href"].replace("/lines/", "") if link_el else None

    # Category: extract from sticker classes e.g. talker--Special
    # (not available in the card — will be None; catalog crawler handles this)

    return ScrapedProduct(
        product_name=name,
        price_cents=price_cents,
        was_price_cents=was_cents,
        gtin=None,
        valid_from=today,
        valid_to=today + timedelta(days=7),
        image_url=image_url,
        category=None,
        brand=None,
    )


class DrakesScraper(Scraper):
    """
    Drakes weekly specials scraper (Myfoodlink / pjax).

    Fetches the /search?q[]=special:1 endpoint page by page, parsing each
    product card from the HTML fragment returned by the pjax request.
    Only products that have a 'was' price are treated as real specials.
    """

    TIMEOUT_S = 30.0
    DELAY_S = 0.5

    # ── discover_source ──────────────────────────────────────────────────────

    async def discover_source(self) -> str:
        """Bootstrap a session and return the base URL."""
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.TIMEOUT_S) as client:
            resp = await client.get(
                _BASE_URL,
                headers={"User-Agent": _PJAX_HEADERS["User-Agent"]},
            )
            if resp.status_code != 200:
                raise ScraperError(f"Drakes bootstrap failed: HTTP {resp.status_code}")
        return _BASE_URL

    # ── fetch_flyer ──────────────────────────────────────────────────────────

    async def fetch_flyer(self, source: str) -> list[str]:
        """
        Fetch all specials pages via pjax and return a list of HTML fragments.

        Args:
            source: base URL (unused, kept for interface compatibility)

        Returns:
            List of raw HTML strings, one per page.
        """
        pages: list[str] = []
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=self.TIMEOUT_S
        ) as client:
            # Bootstrap session / cookies
            await client.get(
                _BASE_URL,
                headers={"User-Agent": _PJAX_HEADERS["User-Agent"]},
            )
            page = 1
            while True:
                html = await self._fetch_page(client, page)
                if html is None:
                    break
                pages.append(html)
                # Check if there are more pages
                if f"page={page + 1}" not in html:
                    break
                page += 1
                await asyncio.sleep(self.DELAY_S)
        return pages

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    async def _fetch_page(
        self, client: httpx.AsyncClient, page: int
    ) -> Optional[str]:
        params = {
            "page": page,
            "q[]": "special:1",
            "sort_by": "top_products",
        }
        resp = await client.get(_BASE_URL + _SPECIALS_PATH, params=params, headers=_PJAX_HEADERS)
        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", "30"))
            logger.warning("Drakes rate limited, waiting %.0fs", wait)
            await asyncio.sleep(wait)
            raise httpx.TransportError("rate limited")
        if resp.status_code != 200:
            logger.warning("Drakes specials page %d returned HTTP %d", page, resp.status_code)
            return None
        if "search-results-products" not in resp.text:
            logger.warning("Drakes page %d: no product results in response", page)
            return None
        return resp.text

    # ── parse_flyer ──────────────────────────────────────────────────────────

    async def parse_flyer(self, raw: list[str]) -> list[ScrapedProduct]:
        """Parse all HTML pages and return a deduplicated list of ScrapedProducts."""
        products: list[ScrapedProduct] = []
        seen_names: set[str] = set()
        today = date.today()

        for html in raw:
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.find_all(attrs={"data-talker": True})
            for card in cards:
                product = _parse_card(card, today)
                if product is None:
                    continue
                key = product.product_name.lower()
                if key in seen_names:
                    continue
                seen_names.add(key)
                products.append(product)

        logger.info("Drakes: parsed %d specials from %d pages", len(products), len(raw))
        return products

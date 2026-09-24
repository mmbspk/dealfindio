"""
Drakes Supermarkets full-catalog scraper (Myfoodlink platform).

Strategy:
  Drakes Online runs on Myfoodlink, a Rails-based grocery e-commerce platform.
  Products are served as server-rendered HTML via pjax (jQuery pjax) — requests
  with ``X-PJAX: true`` return HTML fragments for the ``#search-results-products``
  container.

  No authentication required.

Store selection:
  All Drakes stores share the same product catalogue (same Myfoodlink tenant).
  We use store 015 (Aldinga, SA) as the canonical representative.
  Base URL: https://015.drakes.com.au

Category discovery:
  GET /search/sidebar_facets.json?cb={cb}
  Returns JSON with ``more_sections[].facets`` listing departments.
  The ``cb`` token is scraped from any pjax page load.

Products per category (pjax):
  GET /search?page={n}&q[]=category:{slug}&sort_by=top_products
  Headers: X-PJAX=true, X-PJAX-Container=#search-results-products

  Pagination: when more pages exist the response HTML contains a
  ``page={n+1}`` link.  We stop when no next-page link appears.
  48 products per page.

Product fields extracted from the card HTML:
  - external_id:  ``id="line_{mongoId}"`` (24-char hex, Myfoodlink internal ID)
  - name:         ``.talker__product-name``
  - current price: ``.price__sell``
  - was price:    ``.talker__prices__was`` (present when on promotion)
  - image_url:    ``<img src>`` inside ``.talker__section--image``
  - unit_price:   parsed from unit-pricing text (e.g. "$1.26 per 100g")
  - in_stock:     always True for listed products (Myfoodlink hides OOS)
  - category:     determined by which category search URL was used
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.category_normaliser import normalise_category as _normalise_category
from scrapers.base import ScraperError
from scrapers.catalog_base import (
    SCRAPER_UA,
    CatalogProduct,
    CatalogScraper,
    CategoryNode,
    HumanLikeRateLimiter,
)

logger = logging.getLogger(__name__)

_BASE = "https://015.drakes.com.au"
_SEARCH_PATH = "/search"
_TIMEOUT_S = 30.0

_PJAX_HEADERS = {
    "User-Agent": SCRAPER_UA,
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "en-AU,en-GB;q=0.9,en;q=0.8",
    "X-PJAX": "true",
    "X-PJAX-Container": "#search-results-products",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": _BASE + "/browse",
}

_HTML_HEADERS = {
    "User-Agent": SCRAPER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en-GB;q=0.9,en;q=0.8",
}

# Myfoodlink category slug → canonical name.
# Kept in sync with category_normaliser.py — these are the Drakes raw strings.
_SLUG_TO_RAW = {
    "fruit-vegetables": "Fruit & Vegetables",
    "bread-bakery": "Bakery",
    "deli-seafood": "Deli & Fresh",
    "meat": "Meat & Seafood",
    "dairy": "Dairy, Eggs & Fridge",
    "freezer": "Frozen",
    "pantry": "Pantry",
    "confectionery-snacks": "Snacks & Confectionery",
    "drinks-1": "Drinks",
    "baby": "Baby",
    "health-beauty": "Health & Beauty",
    "household-cleaning-needs": "Cleaning & Laundry",
    "petcare": "Pet",
    "general-merchandise": "Other",
}

_UNIT_PRICE_RE = re.compile(r"\$([\d.]+)\s+(?:per\s+(.+)|each)", re.IGNORECASE)
_PRICE_RE = re.compile(r"\$([\d]+\.[\d]{1,2})")


def _parse_cents(text: str) -> Optional[int]:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    try:
        return int(round(float(m.group(1)) * 100))
    except ValueError:
        return None


def _parse_unit_price(text: str) -> tuple[Optional[float], Optional[str]]:
    """Return (unit_price_float, unit_label) from e.g. '$1.26 per 100g' or '$0.92 each'."""
    m = _UNIT_PRICE_RE.search(text)
    if not m:
        return None, None
    try:
        label = m.group(2).strip() if m.group(2) else "each"
        return float(m.group(1)), label
    except (ValueError, AttributeError):
        return None, None


def _parse_card(card: BeautifulSoup, cat_slug: str, today: date) -> Optional[CatalogProduct]:
    """Parse one talker card ``<div id="line_...">`` into a CatalogProduct."""
    card_id = card.get("id", "")
    if not card_id.startswith("line_"):
        return None
    external_id = card_id[5:]  # strip "line_" prefix (24-char hex Mongo ID)
    if not external_id:
        return None

    name_el = card.find(class_="talker__product-name")
    if not name_el:
        return None
    name = name_el.get_text(strip=True)
    if not name:
        return None

    price_el = card.find(class_="price__sell")
    if not price_el:
        return None
    price_cents = _parse_cents(price_el.get_text(strip=True))
    if not price_cents:
        return None

    was_el = card.find(class_="talker__prices__was")
    was_cents = _parse_cents(was_el.get_text(strip=True)) if was_el else None

    img_el = card.select_one(".talker__section--image img")
    image_url: Optional[str] = None
    if img_el:
        image_url = img_el.get("src") or img_el.get("data-src") or None

    # Package size from the dedicated size span e.g. "700g", "2L"
    size_el = card.find(class_="talker__name__size")
    package_size: Optional[str] = size_el.get_text(strip=True) if size_el else None

    # Unit price from the comparison span e.g. "$0.96 per 100g" → unit_price=0.96, label="100g"
    unit_el = card.find(class_="talker__prices__comparison")
    unit_price: Optional[float] = None
    unit_price_label: Optional[str] = None
    if unit_el:
        unit_price, unit_price_label = _parse_unit_price(unit_el.get_text(strip=True))

    raw_cat = _SLUG_TO_RAW.get(cat_slug, cat_slug)
    normalised_cat = _normalise_category(raw_cat) or raw_cat

    return CatalogProduct(
        product_name=name,
        price_cents=price_cents,
        was_price_cents=was_cents,
        gtin=None,
        valid_from=today,
        valid_to=None,
        image_url=image_url,
        category=normalised_cat,
        brand=None,
        external_id=external_id,
        in_stock=True,
        package_size=package_size,
        unit_price=unit_price,
        unit_price_label=unit_price_label,
    )


class DrakesCatalogScraper(CatalogScraper):
    """
    Drakes full-catalog scraper (Myfoodlink / pjax).

    No login required. A single httpx client is created in ``authenticate``
    and reused across ``discover_categories`` and all ``crawl_category`` calls
    so cookies are shared and we only bootstrap once.
    """

    TIMEOUT_S = _TIMEOUT_S

    # Drakes runs on Myfoodlink — no Imperva/bot-protection. Use tighter delays.
    _MIN_DELAY_S = 1.5
    _MAX_DELAY_S = 4.0
    _BURST_LIMIT = 8

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client: Optional[httpx.AsyncClient] = None
        # Override rate limiter with Drakes-specific pacing (faster than Coles defaults).
        self.rate_limiter = HumanLikeRateLimiter(
            min_delay_s=self._MIN_DELAY_S,
            max_delay_s=self._MAX_DELAY_S,
            burst_limit=self._BURST_LIMIT,
            think_every=30,  # less frequent think-pauses
            think_min_s=2.0,
            think_max_s=5.0,
        )

    # ── CatalogScraper abstract interface ────────────────────────────────────

    async def authenticate(self) -> dict:
        """Bootstrap session cookies and keep the client alive for reuse."""
        # Close any previously open client (e.g. on re-auth).
        if self._client:
            await self._client.aclose()
        self._client = httpx.AsyncClient(follow_redirects=True, timeout=self.TIMEOUT_S)
        # Bootstrap: establish session cookies. 403 is fine — cookies still set.
        try:
            await self._client.get(_BASE, headers=_HTML_HEADERS)
        except httpx.HTTPError:
            pass
        return {"ttl_s": 86400}

    async def discover_categories(self) -> list[CategoryNode]:
        """Fetch the Myfoodlink department list via the sidebar_facets JSON API."""
        client = self._client
        if client is None:
            return _hardcoded_categories()

        try:
            await self.rate_limiter.wait()
            r_browse = await client.get(
                _BASE + _SEARCH_PATH,
                params={"page": 1, "sort_by": "top_products"},
                headers=_PJAX_HEADERS,
            )
            cb_match = re.search(r"sidebar_facets\.json\?cb=([a-f0-9]+)", r_browse.text)
            if not cb_match:
                logger.warning("Drakes: cb token not found, using hardcoded slugs")
                return _hardcoded_categories()

            cb = cb_match.group(1)
            await self.rate_limiter.wait()
            r_facets = await client.get(
                f"{_BASE}/search/sidebar_facets.json?cb={cb}",
                headers={"User-Agent": SCRAPER_UA, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            logger.warning("Drakes: category discovery failed (%s), using hardcoded slugs", exc)
            return _hardcoded_categories()

        if r_facets.status_code != 200:
            logger.warning("Drakes: sidebar_facets %d, using hardcoded slugs", r_facets.status_code)
            return _hardcoded_categories()

        facets = r_facets.json()
        nodes: list[CategoryNode] = []
        for section in facets.get("more_sections", []):
            if section.get("group") != "categories":
                continue
            for cat in section.get("facets", []):
                url_val = cat.get("url", "")
                slug_match = re.search(r"category(?:%3A|:)([^&\"']+)", url_val)
                if not slug_match:
                    continue
                slug = slug_match.group(1)
                name = cat.get("name", slug)
                nodes.append(CategoryNode(id=slug, name=name, url=slug))

        logger.info("Drakes: discovered %d categories: %s", len(nodes), [n.name for n in nodes])
        return nodes or _hardcoded_categories()

    async def crawl_category(self, category: CategoryNode, auth: dict) -> Any:
        """Fetch all pages for one category, parse products inline, discard HTML immediately."""
        client = self._client
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=self.TIMEOUT_S)

        today = date.today()
        products: list[CatalogProduct] = []
        seen: set[str] = set()
        page = 1
        while page <= self.config.max_pages_per_category:
            await self.rate_limiter.wait()
            html = await self._fetch_page(client, category.id, page)
            if html is None:
                break
            has_next = f"page={page + 1}" in html
            # Parse and discard the HTML immediately — don't buffer large strings.
            soup = BeautifulSoup(html, "html.parser")
            del html  # release before next iteration
            for card in soup.find_all(id=re.compile(r"^line_")):
                product = _parse_card(card, category.id, today)
                if product is None or product.external_id in seen:
                    continue
                seen.add(product.external_id)
                products.append(product)
            del soup
            if not has_next:
                break
            page += 1

        # Return products directly; parse_products just passes them through.
        return {"products": products}

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    async def _fetch_page(self, client: httpx.AsyncClient, slug: str, page: int) -> Optional[str]:
        r = await client.get(
            _BASE + _SEARCH_PATH,
            params={"page": page, "q[]": f"category:{slug}", "sort_by": "top_products"},
            headers=_PJAX_HEADERS,
        )
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", "30"))
            self.rate_limiter.on_rate_limited(wait)
            raise ScraperError(f"Drakes rate limited (slug={slug})")
        if r.status_code != 200:
            logger.warning(
                "Drakes: category %s page %d returned HTTP %d", slug, page, r.status_code
            )
            return None
        if "search-results-products" not in r.text and "talker" not in r.text:
            return None
        return r.text

    async def parse_products(self, raw: Any, category: CategoryNode) -> list[CatalogProduct]:
        """Return products already parsed inline during crawl_category."""
        return raw.get("products", [])

    async def crawl_specials(self, auth: dict) -> Any:
        """Fetch all Drakes on-sale products using the pjax sale filter.

        Uses ``q[]=sale:true`` on the same search endpoint as category crawls.
        Products are parsed inline (same as crawl_category) to avoid buffering
        large HTML strings. Returns ``{"products": [CatalogProduct, ...]}``.
        """
        client = self._client
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=self.TIMEOUT_S)

        today = date.today()
        products: list[CatalogProduct] = []
        seen: set[str] = set()
        page = 1

        while page <= self.config.max_pages_per_category:
            await self.rate_limiter.wait()
            try:
                r = await client.get(
                    _BASE + _SEARCH_PATH,
                    params={"page": page, "q[]": "sale:true", "sort_by": "top_products"},
                    headers=_PJAX_HEADERS,
                )
            except httpx.HTTPError as exc:
                raise ScraperError(f"Drakes specials fetch failed: {exc}") from exc

            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", "30"))
                self.rate_limiter.on_rate_limited(wait)
                raise ScraperError("Drakes specials rate-limited (429)")
            if r.status_code != 200:
                self.logger.warning("Drakes specials page %d returned HTTP %d", page, r.status_code)
                break
            if "search-results-products" not in r.text and "talker" not in r.text:
                break

            html = r.text
            has_next = f"page={page + 1}" in html
            soup = BeautifulSoup(html, "html.parser")
            del html

            for card in soup.find_all(id=re.compile(r"^line_")):
                product = _parse_card(card, "sale", today)
                if product is None or product.external_id in seen:
                    continue
                seen.add(product.external_id)
                # Override category to "Specials" for sale items
                product.category = "Specials"
                products.append(product)
            del soup

            if not has_next:
                break
            page += 1

        self.logger.info("Fetched %d Drakes specials across %d pages", len(products), page)
        return {"products": products}

    async def parse_specials(self, raw: Any) -> list[CatalogProduct]:
        """Products were already parsed inline in crawl_specials."""
        return raw.get("products", [])


def _hardcoded_categories() -> list[CategoryNode]:
    """Fallback list when the facets API is unavailable."""
    return [
        CategoryNode(id=slug, name=_SLUG_TO_RAW.get(slug, slug), url=slug) for slug in _SLUG_TO_RAW
    ]

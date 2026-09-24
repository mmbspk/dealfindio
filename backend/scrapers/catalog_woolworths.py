"""
Woolworths full-catalog scraper (public storefront JSON, no login required).

Woolworths' catalog is public — no account needed. The Angular SPA reads
products via two JSON APIs we can call directly:

  Bootstrap (HTML headers):
    GET https://www.woolworths.com.au/  ->  session cookies (Akamai bot-mgmt)
    GET /api/ui/v2/bootstrap            ->  category tree + FulfilmentStoreId

  Per-category products (JSON headers), paged:
    GET /apis/ui/browse/category
        ?categoryId={nodeId}
        &pageNumber={n}
        &pageSize=36
        &sortType=TraderRelevance
        &url=/shop/browse/{urlFriendlyName}
        &location=/shop/browse/{urlFriendlyName}
        &formatObject={"name":"{name}"}
        &isSpecial=false
    ->  { TotalRecordCount, Bundles: [{ Products: [...] }] }

Key details:
  1. Session cookies come from the homepage HTML request (Akamai bm_s/bm_sz).
     Fetching the API with bare JSON headers before warming up the homepage
     still returns 200 but with TotalRecordCount=0 and empty Bundles.
  2. Woolworths caps TotalRecordCount at 10 000 per category, but pagination
     works beyond that — iterate until an empty Bundles page is returned.
  3. Categories flagged IsSpecial (specialsgroup) and non-grocery sections
     (Everyday Market, Healthylife, etc.) are skipped.
  4. Products carry Barcode (EAN-13), Stockcode (internal SKU), Brand,
     PackageSize, and full CDN image URLs — richer than Coles.
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import date
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.services.category_normaliser import normalise_category as _normalise_category
from scrapers.base import ScraperError
from scrapers.catalog_base import (
    HTML_HEADERS as _HTML_HEADERS,
)
from scrapers.catalog_base import (
    JSON_HEADERS as _JSON_HEADERS_BASE,
)
from scrapers.catalog_base import (
    AuthExpiredError,
    CatalogProduct,
    CatalogScraper,
    CategoryNode,
)
from scrapers.catalog_base import (
    get_scraper_proxy as _get_proxy,
)
from scrapers.catalog_base import (
    parse_retry_after as _parse_retry_after,
)
from scrapers.catalog_base import (
    to_cents as _to_cents,
)

# ── Headers ──────────────────────────────────────────────────────────────────
# Woolworths JSON calls need a same-origin Referer and Origin.
_HTML_HEADERS = _HTML_HEADERS  # re-export alias for readability at call sites
_JSON_HEADERS = {
    **_JSON_HEADERS_BASE,
    "Referer": "https://www.woolworths.com.au/shop/browse",
    "Origin": "https://www.woolworths.com.au",
}

# ── URL constants ─────────────────────────────────────────────────────────────

_HOME_URL = "https://www.woolworths.com.au/"
_BOOTSTRAP_URL = "https://www.woolworths.com.au/api/ui/v2/bootstrap"
_BROWSE_URL = "https://www.woolworths.com.au/apis/ui/browse/category"

# NodeIds for sections we don't want to crawl (non-grocery / specials overlay).
_SKIP_NODE_IDS = {
    "specialsgroup",   # handled by the specials scraper (woolworths.py)
    "1_A363395",       # Everyday Market (third-party marketplace)
    "1_F89E4BB",       # Healthylife (health supplement store)
    "1_BC1E589",       # Gift Ideas
    "1_9E60D27",       # Footy Finals (seasonal)
    "1_B63CF9E",       # Front of Store (internal/restricted)
}


class WoolworthsCatalogScraper(CatalogScraper):
    """Full-catalog crawler for Woolworths via the public browse/category API."""

    TIMEOUT_S = 30.0

    # ── CatalogScraper ABC ────────────────────────────────────────────────────

    async def authenticate(self) -> dict:
        """Bootstrap: warm up homepage to get Akamai session cookies.

        No login needed — the catalog is public. Returns session cookies that
        must be replayed on every subsequent API call to avoid empty responses.
        """
        try:
            return await self._bootstrap()
        except ScraperError as exc:
            raise ScraperError(f"Woolworths bootstrap failed after retries: {exc}") from exc

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=5, min=5, max=60),
        reraise=True,
    )
    async def _bootstrap(self) -> dict:
        """Fetch the homepage (HTML headers) to seed session cookies."""
        async with httpx.AsyncClient(
            headers=_HTML_HEADERS, follow_redirects=True, timeout=self.TIMEOUT_S,
            proxies=_get_proxy(),
        ) as client:
            try:
                resp = await client.get(_HOME_URL)
                resp.raise_for_status()
            except httpx.TransportError:
                raise  # retried by tenacity
            except httpx.HTTPError as exc:
                raise ScraperError(f"Woolworths homepage fetch failed: {exc}") from exc

            cookies = {c.name: c.value for c in client.cookies.jar}

        return {"cookies": cookies}

    async def discover_categories(self) -> list[CategoryNode]:
        """Fetch the category tree from the bootstrap API.

        Returns all top-level grocery departments, skipping non-grocery sections
        and the specials overlay (those are handled by woolworths.py).
        """
        auth = await self._get_or_refresh_auth()
        async with httpx.AsyncClient(
            headers=_JSON_HEADERS,
            cookies=auth.get("cookies", {}),
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
            proxies=_get_proxy(),        ) as client:
            await self.rate_limiter.wait()
            try:
                resp = await client.get(_BOOTSTRAP_URL)
                if resp.status_code in (401, 403):
                    raise AuthExpiredError("Woolworths bootstrap auth expired")
                resp.raise_for_status()
                cats_raw = (
                    resp.json()
                    .get("ListTopLevelPiesCategories", {})
                    .get("Categories", [])
                )
            except (httpx.HTTPError, ValueError) as exc:
                raise ScraperError(f"Woolworths category discovery failed: {exc}") from exc

        nodes: list[CategoryNode] = []
        for cat in cats_raw:
            node_id = cat.get("NodeId")
            if not node_id or node_id in _SKIP_NODE_IDS:
                continue
            if cat.get("IsSpecial"):
                continue
            nodes.append(CategoryNode(
                id=node_id,
                name=cat.get("Description") or node_id,
                url=cat.get("UrlFriendlyName") or node_id,
            ))
        return nodes

    async def crawl_category(self, category: CategoryNode, auth: dict) -> Any:
        """Paginate one top-level department. Returns all raw product dicts."""
        url_name = category.url or category.id
        all_products: list[dict] = []

        async with httpx.AsyncClient(
            headers=_JSON_HEADERS,
            cookies=auth.get("cookies", {}),
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
            proxies=_get_proxy(),        ) as client:
            for page in range(1, self.config.max_pages_per_category + 1):
                await self.rate_limiter.wait()
                data = await self._fetch_page(client, url_name, category, page)
                page_products = _extract_products(data)
                if not page_products:
                    break
                all_products.extend(page_products)

                # Stop when we have consumed the reported total (capped at 10000).
                total = data.get("TotalRecordCount") or 0
                if total > 0 and len(all_products) >= total:
                    break
                # Short-page heuristic: Woolworths always returns 36 per page.
                if len(page_products) < 36:
                    break

        return {"category_id": category.id, "category_name": category.name, "products": all_products}

    async def parse_products(
        self, raw: Any, category: CategoryNode
    ) -> list[CatalogProduct]:
        """Convert raw product dicts into CatalogProduct instances."""
        products: list[CatalogProduct] = []
        today = date.today()
        for item in raw.get("products", []):
            product = _parse_item(item, category, today)
            if product is not None:
                products.append(product)
        return products

    async def crawl_specials(self, auth: dict) -> Any:
        """Fetch all Woolworths specials via the search API (IsSpecial=true).

        Warms up a session on the homepage for cookies, then paginates the
        search API with ``searchTerm=a&IsSpecial=true``.
        Returns a dict ``{"products": [raw_item, ...]}``.
        """
        _SPECIALS_URL = "https://www.woolworths.com.au/apis/ui/Search/products"
        PAGE_SIZE = 36
        MAX_PAGES = 200

        all_products: list[dict] = []

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
            proxies=_get_proxy(),
            http2=False,
        ) as client:
            # Warm up homepage to get Akamai session cookies.
            try:
                await client.get("https://www.woolworths.com.au/", headers=_HTML_HEADERS)
            except Exception as exc:
                self.logger.warning("Woolworths homepage warm-up failed: %s", exc)

            page = 1
            total_reported: Optional[int] = None

            while page <= MAX_PAGES:
                await self.rate_limiter.wait()
                params = {
                    "searchTerm": "a",
                    "pageNumber": page,
                    "pageSize": PAGE_SIZE,
                    "sortType": "TraderRelevance",
                    "IsSpecial": "true",
                    "inStockProductsOnly": "false",
                }
                try:
                    resp = await client.get(_SPECIALS_URL, params=params, headers=_JSON_HEADERS)
                    if resp.status_code in (401, 403):
                        raise AuthExpiredError(f"Woolworths specials returned {resp.status_code}")
                    resp.raise_for_status()
                    data = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise ScraperError(f"Woolworths specials fetch failed: {exc}") from exc

                if total_reported is None:
                    total_reported = data.get("SearchResultsCount", 0)
                    self.logger.info("Woolworths reports %d specials total", total_reported)

                outer = data.get("Products") or []
                page_products: list[dict] = []
                for bundle in outer:
                    page_products.extend(bundle.get("Products") or [])

                all_products.extend(page_products)
                self.logger.debug(
                    "Specials page %d: %d products (total so far: %d)",
                    page, len(page_products), len(all_products),
                )

                if len(page_products) < PAGE_SIZE:
                    break
                page += 1

        self.logger.info("Fetched %d Woolworths specials across %d pages", len(all_products), page)
        return {"products": all_products}

    async def parse_specials(self, raw: Any) -> list[CatalogProduct]:
        """Parse specials response into CatalogProducts using the shared item parser."""
        specials_cat = CategoryNode(id="specials", name="Specials")
        products: list[CatalogProduct] = []
        today = date.today()
        for item in raw.get("products", []):
            product = _parse_item(item, specials_cat, today)
            if product is not None:
                products.append(product)
        return products

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        url_name: str,
        category: CategoryNode,
        page: int,
    ) -> dict:
        """Fetch one page from browse/category. Raises on auth failure / 429."""
        # Woolworths' browse/category API only accepts pageSize of 36.
        params = {
            "pageNumber": page,
            "pageSize": 36,
            "sortType": "TraderRelevance",
            "url": f"/shop/browse/{url_name}",
            "location": f"/shop/browse/{url_name}",
            "formatObject": json.dumps({"name": category.name}, separators=(",", ":")),
            "isSpecial": "false",
            "categoryId": category.id,
        }
        try:
            resp = await client.get(_BROWSE_URL, params=params)
        except httpx.HTTPError as exc:
            raise ScraperError(f"Woolworths page fetch failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise AuthExpiredError(f"Woolworths returned {resp.status_code}")
        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            self.rate_limiter.on_rate_limited(retry_after)
            raise ScraperError("Woolworths rate-limited (429)")
        try:
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ScraperError(f"Woolworths page parse failed: {exc}") from exc


# ── Module-level response helpers ─────────────────────────────────────────────

def _extract_products(data: dict) -> list[dict]:
    """Flatten Bundles[].Products[] from a browse/category response."""
    bundles = data.get("Bundles") or []
    products: list[dict] = []
    for bundle in bundles:
        products.extend(bundle.get("Products") or [])
    return products


_SKU_RE = re.compile(r"^[^a-zA-Z]+$")  # no letters at all → internal SKU code


def _parse_item(
    item: dict, category: CategoryNode, today: date
) -> Optional[CatalogProduct]:
    """Map one Woolworths product dict to a CatalogProduct. Returns None to skip."""
    if not isinstance(item, dict):
        return None

    name = _html.unescape((item.get("Name") or item.get("DisplayName") or "").strip())
    price_raw = item.get("Price")
    if not name or price_raw is None:
        return None

    # Skip Woolworths internal SKU codes that leak as product names (e.g. "== Vga-r")
    if name.startswith("==") or _SKU_RE.match(name):
        return None

    price_cents = _to_cents(price_raw)
    if price_cents is None or price_cents <= 0:
        return None

    was_raw = item.get("WasPrice") or item.get("InstoreWasPrice")
    was_cents = _to_cents(was_raw) if was_raw is not None else None
    if was_cents is not None and was_cents <= price_cents:
        was_cents = None

    # Woolworths provides EAN-13 via Barcode; Stockcode is their internal SKU (not a GTIN).
    barcode = str(item["Barcode"]) if item.get("Barcode") else None
    stockcode = item.get("Stockcode")
    gtin = barcode  # only store real barcodes, not internal Stockcodes

    image_url = item.get("MediumImageFile") or item.get("SmallImageFile")
    if not image_url and stockcode:
        image_url = (
            f"https://cdn0.woolworths.media/content/wowproductimages/medium/{stockcode}.jpg"
        )

    # Derive category label: use SAP department name when available, then normalise.
    additional = item.get("AdditionalAttributes") or {}

    # Skip third-party marketplace products — not sold/shipped by Woolworths.
    if additional.get("IsMarketProduct"):
        return None

    dept_name = additional.get("sapdepartmentname") or additional.get("sapsubcategoryname")
    raw_label = f"{category.name} > {dept_name}" if dept_name else category.name
    category_label = _normalise_category(raw_label) or category.name

    brand = item.get("Brand") or None
    in_stock = bool(item.get("IsInStock", True)) and bool(item.get("IsAvailable", True))

    package_size = item.get("PackageSize") or None
    cup_price = item.get("CupPrice")
    cup_measure = item.get("CupMeasure") or ""
    unit_price: Optional[float] = None
    if cup_price is not None:
        try:
            unit_price = float(cup_price)
        except (TypeError, ValueError):
            pass
    # Normalise CupMeasure into package_size when PackageSize absent
    if not package_size and cup_measure:
        package_size = cup_measure

    return CatalogProduct(
        product_name=name,
        price_cents=price_cents,
        was_price_cents=was_cents,
        gtin=gtin,
        valid_from=today,
        image_url=image_url,
        category=category_label,
        brand=brand,
        external_id=str(stockcode) if stockcode else None,
        url=None,
        in_stock=in_stock,
        package_size=package_size,
        unit_price=unit_price,
        unit_price_label=cup_measure.strip() or None,
    )



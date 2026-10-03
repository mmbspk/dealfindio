"""
IGA full-catalog scraper (igashop.com.au storefront BFF).

Strategy:
  IGA's website uses a Next.js BFF at https://www.igashop.com.au/api/storefront/
  that proxies the Metcash backend.  No authentication is required — only the
  x-shopping-mode header is needed.  The raw Metcash Lambda
  (z2zelumxs4.execute-api.ap-southeast-2.amazonaws.com/altprod) now rejects all
  anonymous tokens and has PASSWORD_LOGIN_DISABLED on Firebase, so it is
  intentionally not used.

Store selection:
  GET /api/storefront/stores?take=1 returns the default store (nearest/first).
  We pick a fixed Sydney metro store (retailerStoreId=32600) so results are
  representative and stable between runs.

Shopping modes:
  11111111-1111-1111-1111-111111111111 = Pickup (exposes widest product set)
  22222222-2222-2222-2222-222222222222 = Delivery
  33333333-3333-3333-3333-333333333333 = Planning (in-store)

Category discovery:
  GET /api/storefront/stores/{storeId}/categoryHierarchy
    x-shopping-mode: {modeId}

Products (per category):
  GET /api/storefront/stores/{storeId}/categories/{categoryId}/search
    ?take=48&skip=N
    x-shopping-mode: {modeId}
  Pagination: response contains {"total": N, "count": N, "items": [...]}

Specials (promotions endpoint — returns all on-special products directly):
  GET /api/storefront/stores/{storeId}/promotions/products
    ?take=48&skip=N
    x-shopping-mode: {modeId}
  Items include wasPriceNumeric when on special.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.category_normaliser import normalise_category as _normalise_category
from scrapers.base import ScraperError
from scrapers.catalog_base import (
    SCRAPER_UA,
    CatalogProduct,
    CatalogScraper,
    CategoryNode,
)
from scrapers.catalog_base import (
    to_cents as _to_cents,
)

_BASE = "https://www.igashop.com.au/api/storefront"
_DEFAULT_STORE_ID = "32600"
_SHOPPING_MODE_ID = "11111111-1111-1111-1111-111111111111"


def _headers() -> dict:
    return {
        "x-shopping-mode": _SHOPPING_MODE_ID,
        "Accept": "application/json",
        "User-Agent": SCRAPER_UA,
        "Referer": "https://www.igashop.com.au/",
    }


class IGACatalogScraper(CatalogScraper):
    """Full-catalog crawler for IGA via the igashop.com.au storefront API."""

    TIMEOUT_S = 30.0
    store_id: str = _DEFAULT_STORE_ID

    # ── Authentication ────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        reraise=True,
    )
    async def authenticate(self) -> dict:
        """No auth required — return a static context dict."""
        return {
            "store_id": self.store_id,
            "ttl_s": 86400,  # Re-auth daily (no-op, just renews the context)
        }

    # ── Category discovery ────────────────────────────────────────────────────

    async def discover_categories(self) -> list[CategoryNode]:
        url = f"{_BASE}/stores/{self.store_id}/categoryHierarchy"
        async with httpx.AsyncClient(
            headers=_headers(), follow_redirects=True, timeout=self.TIMEOUT_S
        ) as client:
            await self.rate_limiter.wait()
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise ScraperError(f"IGA categoryHierarchy failed: {exc}") from exc

            if resp.status_code != 200:
                raise ScraperError(
                    f"IGA categoryHierarchy returned {resp.status_code}: {resp.text[:200]}"
                )
            try:
                data = resp.json()
            except ValueError as exc:
                raise ScraperError(f"IGA categoryHierarchy JSON error: {exc}") from exc

        return _flatten_category_tree(data)

    # ── Category crawling ─────────────────────────────────────────────────────

    async def crawl_category(
        self, category: CategoryNode, auth: dict, *, page_sink=None, start_page: int = 1
    ) -> Any:
        store_id = auth.get("store_id", self.store_id)
        pages: list[dict] = []
        # Compute skip directly so resume doesn't waste requests re-fetching old pages.
        skip = (start_page - 1) * self.config.page_size
        page_num = start_page - 1
        today = date.today()
        # seen_ids spans all pages so cross-page duplicates are caught even in streaming mode.
        seen_ids: set[str] = set()

        async with httpx.AsyncClient(
            headers=_headers(), follow_redirects=True, timeout=self.TIMEOUT_S
        ) as client:
            while page_num < self.config.max_pages_per_category:
                page_num += 1
                await self.rate_limiter.wait()
                data = await self._fetch_page(client, store_id, category.id, skip)
                items = data.get("items") or []
                if not items:
                    break

                if page_sink is not None:
                    page_products = []
                    for item in items:
                        product = _parse_item(item, category.name, today)
                        if product is None or product.external_id in seen_ids:
                            continue
                        seen_ids.add(product.external_id or "")
                        page_products.append(product)
                    await page_sink(page_products, page_num)
                else:
                    pages.append(data)

                total = data.get("total") or 0
                skip += self.config.page_size
                if skip >= total or len(items) < self.config.page_size:
                    break

        if page_sink is not None:
            return {"_streamed": True}

        return {"category_name": category.name, "pages": pages}

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        store_id: str,
        category_id: str,
        skip: int,
    ) -> dict:
        url = f"{_BASE}/stores/{store_id}/categories/{category_id}/search"
        params = {"take": self.config.page_size, "skip": skip}
        try:
            resp = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ScraperError(f"IGA products fetch failed for cat {category_id}: {exc}") from exc

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "60"))
            self.rate_limiter.on_rate_limited(retry_after)
            raise ScraperError(f"IGA rate-limited on cat {category_id}")
        if resp.status_code != 200:
            raise ScraperError(f"IGA category search returned {resp.status_code} for {category_id}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ScraperError(f"IGA products JSON error for {category_id}: {exc}") from exc

    async def parse_products(self, raw: Any, category: CategoryNode) -> list[CatalogProduct]:
        products: list[CatalogProduct] = []
        today = date.today()
        cat_name = raw.get("category_name", category.name)
        seen_ids: set[str] = set()

        for page in raw.get("pages", []):
            for item in page.get("items") or []:
                product = _parse_item(item, cat_name, today)
                if product is None:
                    continue
                if product.external_id in seen_ids:
                    continue
                seen_ids.add(product.external_id or "")
                products.append(product)

        return products

    async def crawl_specials(self, auth: dict, product_sink=None, resume_page: int = 1) -> Any:
        """Fetch all IGA on-special products from the promotions endpoint.

        Uses /api/storefront/stores/{storeId}/promotions/products, paginated
        with take/skip. Returns {"items": [flat list of raw item dicts]}.
        """
        store_id = auth.get("store_id", self.store_id)
        url = f"{_BASE}/stores/{store_id}/promotions/products"
        all_items: list[dict] = []
        skip = 0
        PAGE_SIZE = 48
        MAX_PAGES = 200

        async with httpx.AsyncClient(
            headers=_headers(), follow_redirects=True, timeout=self.TIMEOUT_S
        ) as client:
            total_reported: Optional[int] = None

            for _ in range(MAX_PAGES):
                await self.rate_limiter.wait()
                params = {"take": PAGE_SIZE, "skip": skip}
                try:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise ScraperError(f"IGA promotions fetch failed: {exc}") from exc

                if total_reported is None:
                    total_reported = data.get("total") or data.get("count") or 0
                    self.logger.info("IGA reports %d specials total", total_reported)

                items = data.get("items") or []
                if not items:
                    break
                all_items.extend(items)
                skip += PAGE_SIZE

                if len(items) < PAGE_SIZE:
                    break
                if total_reported and skip >= total_reported:
                    break

        self.logger.info("Fetched %d IGA specials", len(all_items))
        return {"items": all_items}

    async def parse_specials(self, raw: Any) -> list[CatalogProduct]:
        """Parse promotions response into CatalogProducts using the shared item parser."""
        products: list[CatalogProduct] = []
        today = date.today()
        seen_ids: set[str] = set()

        for item in raw.get("items") or []:
            product = _parse_item(item, None, today)
            if product is None or product.external_id in seen_ids:
                continue
            seen_ids.add(product.external_id or "")
            products.append(product)

        return products


# ── Category tree helpers ─────────────────────────────────────────────────────


def _flatten_category_tree(data: Any) -> list[CategoryNode]:
    """Return the nav-level (top-level) IGA categories for crawling.

    The API hierarchy is: Grocery → NavCategory → SubCategory → Leaf.
    We crawl at NavCategory depth (e.g. "Fruit and Vegetable", "Pantry") so
    each request covers the full section, and we use the product's
    categoryBreadcrumb to assign the correct nav-level label.  Crawling at
    leaf depth would produce ~415 requests with duplicate products across
    parent/child nodes.
    """
    nodes: list[CategoryNode] = []

    if isinstance(data, dict):
        inner = data.get("children") or data.get("categories") or data.get("items") or []
        if isinstance(inner, list):
            data = inner

    if not isinstance(data, list):
        return nodes

    for top in data:
        if not isinstance(top, dict):
            continue
        # "Grocery" is the root — its children are the nav-level categories
        for nav in top.get("children") or []:
            if not isinstance(nav, dict):
                continue
            cat_id = str(
                nav.get("identifier")
                or nav.get("retailerCategoryId")
                or nav.get("id")
                or nav.get("categoryId")
                or ""
            )
            name = (
                nav.get("displayName")
                or nav.get("name")
                or nav.get("categoryName")
                or nav.get("title")
                or ""
            ).strip()
            if cat_id and name:
                nodes.append(
                    CategoryNode(id=cat_id, name=name, url=cat_id, parent_id=None, children=[])
                )

    return nodes


# ── Product helpers ───────────────────────────────────────────────────────────


def _parse_item(item: dict, cat_name: str | None, today: date) -> Optional[CatalogProduct]:
    product_id = str(item.get("productId") or item.get("sku") or "").strip()
    if not product_id:
        return None

    name = (item.get("name") or "").strip()
    if not name:
        return None

    # priceNumeric is already a float (dollars), e.g. 5.3
    price_cents = _to_cents(item.get("priceNumeric") or item.get("wholePrice"))
    if price_cents is None or price_cents <= 0:
        return None

    # wasPriceNumeric is set when on special
    was_raw = item.get("wasPriceNumeric")
    was_cents = _to_cents(was_raw) if was_raw else None
    if was_cents is not None and was_cents <= price_cents:
        was_cents = None

    # Unit price: parse from pricePerUnit string e.g. "$1.20/100g"
    unit_price_val: Optional[float] = None
    unit_price_label_val: Optional[str] = None
    ppu = item.get("pricePerUnit") or ""
    m = re.search(r"\$([\d.]+)/(.+)", str(ppu))
    if m:
        try:
            unit_price_val = float(m.group(1))
            unit_price_label_val = m.group(2).strip()
        except ValueError:
            pass

    # Image: storefront returns a dict {cell, default, details, template, zoom}
    image_raw = item.get("image") or {}
    image_url: Optional[str] = None
    if isinstance(image_raw, str) and image_raw.startswith("http"):
        image_url = image_raw
    elif isinstance(image_raw, dict):
        image_url = image_raw.get("default") or image_raw.get("cell") or image_raw.get("details")

    in_stock = bool(item.get("available", True))

    # Package size from unitOfSize
    uos = item.get("unitOfSize") or {}
    size_val = uos.get("size")
    size_type = uos.get("type") or uos.get("abbreviation") or ""
    package_size: Optional[str] = None
    # Map verbose IGA unit names to clean abbreviations
    _IGA_UNIT_ABBREV = {
        "millilitre": "mL",
        "milliliter": "mL",
        "ml": "mL",
        "litre": "L",
        "liter": "L",
        "gram": "g",
        "grams": "g",
        "kilogram": "kg",
        "kilograms": "kg",
        "ounce": "oz",
        "ounces": "oz",
    }
    if size_val is not None and size_type:
        type_lower = size_type.lower()
        if type_lower in ("each", "ea", "pk", "pack"):
            # Count-based packs: only meaningful when > 1 (e.g. "44 each" → "44 Pack")
            try:
                count = int(float(size_val))
                if count > 1:
                    package_size = f"{count} Pack"
            except (ValueError, TypeError):
                pass
        elif type_lower:
            abbrev = _IGA_UNIT_ABBREV.get(type_lower)
            package_size = f"{size_val} {abbrev}" if abbrev else f"{size_val}{size_type}"

    brand = (item.get("brand") or "").strip() or None
    canonical = _normalise_category(cat_name) or cat_name

    # Use nav-level category from breadcrumb (Grocery/NavCategory/Sub/Leaf → NavCategory)
    default_cats = item.get("defaultCategory") or []
    if default_cats and isinstance(default_cats, list):
        breadcrumb = default_cats[0].get("categoryBreadcrumb") or ""
        if breadcrumb:
            parts = [p.strip() for p in breadcrumb.split("/") if p.strip()]
            # parts[0]="Grocery", parts[1]=nav category (matches website nav bar)
            if len(parts) >= 2:
                canonical = _normalise_category(parts[1]) or parts[1]

    # Product URL
    sku = item.get("sku") or product_id
    product_url = f"https://www.igashop.com.au/product/{sku}"

    return CatalogProduct(
        product_name=name,
        price_cents=price_cents,
        was_price_cents=was_cents,
        gtin=None,
        valid_from=today,
        valid_to=None,
        image_url=image_url,
        category=canonical,
        brand=brand,
        external_id=product_id,
        url=product_url,
        in_stock=in_stock,
        package_size=package_size,
        unit_price=unit_price_val,
        unit_price_label=unit_price_label_val,
    )

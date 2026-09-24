"""
Dan Murphy's full-catalog scraper.

Strategy: Dan Murphy's is a Woolworths-owned liquor retailer running the
Olympic TRADER platform. The catalog API is public but Cloudflare-protected.

Bootstrap / auth:
  GET https://api.danmurphys.com.au/bootstrap/path
  → sets ARRAffinity + __cf_bm cookies required by all subsequent calls
  No login required.

Category discovery:
  Dan Murphy's has no public API for categories. We use a hardcoded slug
  list that covers every meaningful top-level product group, same approach as
  ALDI. Sub-categories (e.g. red-wine, white-wine) are included as siblings
  so we get more granular metadata.

Products:
  POST https://api.danmurphys.com.au/apis/ui/ProductGroup/Products/{slug}
  Body: {"pageSize":48,"pageNumber":N,"sortType":"Relevance",
         "Location":"ProductGroup","Filters":[],"ShowOnlyAvailable":false}
  Returns: {"Items":[{"Products":[...],"Heading":"..."}],"TotalRecordCount":N}

Headers:
  dmIV: 28DC62D8B2DA77F445E239BD0DB257FD   (static, from JS bundle)
  Content-Type: application/json

Product fields used:
  Stockcode, Description, Prices[0].{Price,WasPrice}, SmallImageFile /
  MediumImageFile / LargeImageFile, PackageSize, IsOnSpecial, UrlFriendlyName,
  IsAvailable, Variety, Type, Region
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from scrapers.base import ScraperError
from scrapers.catalog_base import (
    AuthExpiredError,
    CatalogProduct,
    CatalogScraper,
    CategoryNode,
    HTML_HEADERS as _HTML_HEADERS_BASE,
    SCRAPER_UA,
    parse_retry_after as _parse_retry_after,
    to_cents as _to_cents,
)
from app.services.category_normaliser import normalise_category as _normalise_category

_BOOTSTRAP_URL = "https://api.danmurphys.com.au/bootstrap/path"
_PRODUCTS_URL = "https://api.danmurphys.com.au/apis/ui/ProductGroup/Products/{slug}"
_PRODUCT_URL = "https://www.danmurphys.com.au/product/DM_{name}"
_IMAGE_BASE = "https://media.danmurphys.com.au/dmo/product/{code}-1.png"

# Static header value extracted from Dan Murphy's Angular bundle.
_DM_IV = "28DC62D8B2DA77F445E239BD0DB257FD"

_BASE_HEADERS = {
    **_HTML_HEADERS_BASE,
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.danmurphys.com.au",
    "Referer": "https://www.danmurphys.com.au/",
    "dmIV": _DM_IV,
}

# Hardcoded category slugs with parent/child relationships.
# These map to the /ProductGroup/Products/{slug} endpoint.
# Verified against live API 2026-09-16 — counts in comments.
_CATEGORIES: list[dict] = [
    # Top-level groups
    {"id": "wine",               "name": "Wine",                 "parent": None},        # 6700
    {"id": "beer",               "name": "Beer & Cider",         "parent": None},        # 735  (was beer-cider)
    {"id": "spirits",            "name": "Spirits",              "parent": None},        # 2091
    {"id": "champagne-sparkling","name": "Champagne & Sparkling","parent": None},        # 750
    {"id": "food-snacks",        "name": "Food & Snacks",        "parent": None},        # 77
    # Wine sub-categories
    {"id": "red-wine",           "name": "Red Wine",             "parent": "wine"},      # 6827
    {"id": "white-wine",         "name": "White Wine",           "parent": "wine"},      # 2372
    {"id": "rose",               "name": "Rosé Wine",            "parent": "wine"},      # 271  (was rose-wine)
    {"id": "dessert-wine",       "name": "Dessert & Fortified",  "parent": "wine"},      # 153  (was dessert-fortified)
    # Spirits sub-categories
    {"id": "whisky",             "name": "Whisky",               "parent": "spirits"},   # 658
    {"id": "bourbon",            "name": "Bourbon",              "parent": "spirits"},   # 70
    {"id": "gin",                "name": "Gin",                  "parent": "spirits"},   # 461
    {"id": "vodka",              "name": "Vodka",                "parent": "spirits"},   # 162
    {"id": "rum",                "name": "Rum",                  "parent": "spirits"},   # 136
    {"id": "tequila",            "name": "Tequila & Mezcal",     "parent": "spirits"},   # 188  (was tequila-mezcal)
    {"id": "brandy-cognac",      "name": "Brandy & Cognac",      "parent": "spirits"},   # 65
    {"id": "liqueurs",           "name": "Liqueur",              "parent": "spirits"},   # 237  (was liqueur)
    # Beer sub-categories
    {"id": "lager",              "name": "Lager & Pilsner",      "parent": "beer"},      # 240  (was lager-pilsner)
    {"id": "ipa-beer",           "name": "IPA",                  "parent": "beer"},      # 33   (was ipa)
    {"id": "cider",              "name": "Cider",                "parent": "beer"},      # 151
    {"id": "craft-beer",         "name": "Craft Beer",           "parent": "beer"},      # 372
]

# Slugs that the API confirms return 0 products — skip them entirely.
_SKIP_SLUGS = {"accessories", "other-drinks", "gifts-occassions", "other-drinks-accessories",
               "beer-cider", "rose-wine", "dessert-fortified", "tequila-mezcal", "liqueur",
               "lager-pilsner", "pale-ale", "stout-porter"}


def _build_category_nodes() -> list[CategoryNode]:
    nodes: list[CategoryNode] = []
    for c in _CATEGORIES:
        if c["id"] in _SKIP_SLUGS:
            continue
        nodes.append(
            CategoryNode(
                id=c["id"],
                name=c["name"],
                url=c["id"],
                parent_id=c["parent"],
            )
        )
    return nodes


class DanMurphysCatalogScraper(CatalogScraper):
    """Full-catalog crawler for Dan Murphy's via the Olympic TRADER API."""

    TIMEOUT_S = 30.0

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        reraise=True,
    )
    async def authenticate(self) -> dict:
        """Fetch the bootstrap page to get Cloudflare + ARRAffinity cookies.

        No credentials needed — the cookies are set automatically by Cloudflare
        on a regular HTTP GET. We cache them in Redis so subsequent runs reuse
        the same session rather than triggering another Cloudflare check.
        """
        async with httpx.AsyncClient(
            headers={**_BASE_HEADERS, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
        ) as client:
            try:
                resp = await client.get(_BOOTSTRAP_URL)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ScraperError(f"DM bootstrap failed: {exc}") from exc

            cookies = {c.name: c.value for c in client.cookies.jar}

        return {
            "cookies": cookies,
            "ttl_s": 3600,  # Cloudflare cookies are short-lived; re-bootstrap hourly
        }

    async def discover_categories(self) -> list[CategoryNode]:
        return _build_category_nodes()

    async def crawl_category(self, category: CategoryNode, auth: dict) -> Any:
        """POST paginated product requests for one category slug."""
        slug = category.url or category.id
        pages: list[dict] = []

        async with httpx.AsyncClient(
            headers=_BASE_HEADERS,
            cookies=auth.get("cookies", {}),
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
        ) as client:
            page_number = 1
            while page_number <= self.config.max_pages_per_category:
                await self.rate_limiter.wait()
                data = await self._fetch_page(client, slug, page_number, auth)
                pages.append(data)

                total = data.get("TotalRecordCount") or 0
                items = _extract_items(data)
                fetched_so_far = page_number * self.config.page_size
                if fetched_so_far >= total or not items:
                    break
                page_number += 1

        return {"slug": slug, "category_name": category.name, "pages": pages}

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        slug: str,
        page_number: int,
        auth: dict,
    ) -> dict:
        url = _PRODUCTS_URL.format(slug=slug)
        payload = {
            "pageSize": self.config.page_size,
            "pageNumber": page_number,
            "sortType": "Relevance",
            "Location": "ProductGroup",
            "Filters": [],
            "ShowOnlyAvailable": False,
        }
        try:
            resp = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise ScraperError(f"DM page fetch failed for {slug!r}: {exc}") from exc

        if resp.status_code in (401, 403):
            raise AuthExpiredError(f"DM returned {resp.status_code} (cookies expired?)")
        if resp.status_code == 404:
            # Unknown slug — treat as empty rather than a hard error.
            return {"TotalRecordCount": 0, "Items": []}
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "60"))
            self.rate_limiter.on_rate_limited(retry_after)
            raise ScraperError(f"DM rate-limited (429) for {slug!r}")
        try:
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ScraperError(f"DM page parse failed for {slug!r}: {exc}") from exc

    async def parse_products(self, raw: Any, category: CategoryNode) -> list[CatalogProduct]:
        products: list[CatalogProduct] = []
        today = date.today()
        seen_codes: set[str] = set()
        cat_name = raw.get("category_name", category.name)

        for page in raw.get("pages", []):
            for item in _extract_items(page):
                product = _parse_item(item, category, cat_name, today)
                if product is None:
                    continue
                if product.external_id in seen_codes:
                    continue
                seen_codes.add(product.external_id or "")
                products.append(product)

        return products


# ── Response helpers ─────────────────────────────────────────────────────────

def _extract_items(page: dict) -> list[dict]:
    """Flatten Items[*].Products into a single list of product dicts."""
    products: list[dict] = []
    for group in page.get("Items") or []:
        for p in group.get("Products") or []:
            products.append(p)
    return products


def _parse_item(
    item: dict,
    category: CategoryNode,
    cat_name: str,
    today: date,
) -> Optional[CatalogProduct]:
    stockcode = str(item.get("Stockcode", "") or "").strip()
    if not stockcode:
        return None

    # Clean HTML from Description, prefer AdditionalDetails productname if present
    details = {d["Name"]: d["Value"] for d in (item.get("AdditionalDetails") or []) if d.get("Name")}
    name = (
        details.get("webproductname")
        or details.get("producttitle")
        or (item.get("Description") or "")
    ).strip()
    # Strip residual HTML tags (Description sometimes contains <br>)
    import re as _re
    name = _re.sub(r"<[^>]+>", " ", name).strip()
    name = _re.sub(r"\s+", " ", name).strip()
    if not name:
        return None

    # Prices is a dict: {"singleprice": {"Value": 98.99, ...}, "caseprice": ..., "inanysixprice": ...}
    prices: dict = item.get("Prices") or {}
    single = prices.get("singleprice") or {}
    price_cents = _to_cents(single.get("Value"))
    if price_cents is None or price_cents <= 0:
        return None

    # WasPrice: BeforePromotion on singleprice when on special
    was_cents = None
    if item.get("IsOnSpecial"):
        before = single.get("BeforePromotion")
        was_cents = _to_cents(before) if before else None
        if was_cents is not None and was_cents <= price_cents:
            was_cents = None

    # Any-6 price as a proxy for unit_price (per-bottle when bought in any 6)
    # Value is already in dollars (same scale as singleprice.Value).
    any6 = prices.get("inanysixprice") or {}
    unit_price_val: Optional[float] = None
    if any6.get("Value"):
        try:
            unit_price_val = float(any6["Value"])
        except (TypeError, ValueError):
            pass

    # Azure blob URLs returned by the API (LargeImageFile etc.) are 404 on the
    # public CDN. Always derive the URL from the stockcode via the working CDN.
    image_url = _IMAGE_BASE.format(code=stockcode)

    url_name = (item.get("UrlFriendlyName") or "").strip()
    product_url = _PRODUCT_URL.format(name=url_name) if url_name else None

    # Stock: use Inventory block when available
    inventory = item.get("Inventory") or {}
    available_qty = inventory.get("availableinventoryqty", 1)
    in_stock = int(available_qty) > 0

    package_size = (details.get("webliquorsize") or item.get("PackageSize") or "").strip() or None

    # Brand and category enrichment from AdditionalDetails
    brand = (details.get("webbrandname") or "").strip() or None
    prod_type = (details.get("webproducttype") or details.get("varietal") or "").strip()
    canonical = _normalise_category(cat_name) or cat_name
    if prod_type and prod_type.lower() not in cat_name.lower():
        category_label = f"{canonical} > {prod_type}"
    else:
        category_label = canonical

    return CatalogProduct(
        product_name=name,
        price_cents=price_cents,
        was_price_cents=was_cents,
        gtin=None,
        valid_from=today,
        valid_to=None,
        image_url=image_url,
        category=category_label,
        brand=brand,
        external_id=stockcode,
        url=product_url,
        in_stock=in_stock,
        package_size=package_size,
        unit_price=unit_price_val,
        unit_price_label="any 6" if unit_price_val is not None else None,
    )


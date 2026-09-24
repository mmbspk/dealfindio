"""
ALDI Australia full-catalog scraper.

Strategy: ALDI's site is a Nuxt 3 app backed by a Spryker Glue API at
asl.api.aldi.com.au/commerce. The API is public (no auth required) and
returns clean JSON with full product metadata, categories, images, and pricing.

Endpoints used:
  GET /commerce/v2/product-category-tree   — full category hierarchy
  GET /commerce/v3/product-search          — paginated product list per category
    params: categoryTree, limit (must be one of 12/16/24/30/32/48/60), offset

Pagination: standard offset/limit. totalCount in meta.pagination.
Products: discontinued=true or price.amount=0 are skipped.
notForSale=true products are included — they're real in-store items that
just can't be purchased online; their prices are valid.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.services.category_normaliser import normalise_category as _normalise_category
from scrapers.base import ScraperError
from scrapers.catalog_base import (
    SCRAPER_UA,
    CatalogProduct,
    CatalogScraper,
    CategoryNode,
)

_GLUE_BASE = "https://asl.api.aldi.com.au/commerce"
_CATEGORY_TREE_URL = f"{_GLUE_BASE}/v2/product-category-tree"
_PRODUCT_SEARCH_URL = f"{_GLUE_BASE}/v3/product-search"
_IMAGE_URL = (
    "https://dm.apac.cms.aldi.cx/is/image/aldiprodapac/product/jpg/scaleWidth/600/{uuid}/{slug}"
)
_PAGE_SIZE = 48

# Promotional categories — products change weekly and are also in their
# parent grocery categories; including these causes duplicates.
_PROMO_CATEGORY_KEYS = {
    "1588161425841179",  # Lower Prices
    "1588161426952145",  # Super Savers
    "1588161420755352",  # Limited Time Only
}

_HEADERS = {
    "Accept": "application/vnd.api+json",
    "Accept-Language": "en-AU,en;q=0.9",
    "User-Agent": SCRAPER_UA,
    "Origin": "https://www.aldi.com.au",
    "Referer": "https://www.aldi.com.au/",
}


def _parse_comparison_display(display: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    """
    Parse price.comparisonDisplay like "$24.95 per 1 kg" into (24.95, "1 kg").
    Returns (None, None) if unparseable.
    """
    if not display:
        return None, None
    m = re.match(r"\$([\d.]+)\s+per\s+(.+)", display.strip())
    if not m:
        return None, None
    try:
        return float(m.group(1)), m.group(2).strip()
    except ValueError:
        return None, None


def _parse_was_price(display: Optional[str]) -> Optional[int]:
    """Parse wasPriceDisplay like '$2.49' into cents."""
    if not display:
        return None
    m = re.search(r"[\d.]+", display.replace(",", ""))
    if not m:
        return None
    try:
        return int(round(float(m.group()) * 100))
    except ValueError:
        return None


def _build_image_url(assets: list[dict], url_slug: str) -> Optional[str]:
    """Extract the first product image from the assets array."""
    for asset in assets:
        url_template = asset.get("url", "")
        if not url_template:
            continue
        if "{width}" in url_template and "{slug}" in url_template:
            m = re.search(r"scaleWidth/\{width\}/([^/\{]+)/\{slug\}", url_template)
            if m:
                uuid = m.group(1)
                return _IMAGE_URL.format(uuid=uuid, slug=url_slug)
        elif url_template.startswith("http"):
            return url_template
    return None


def _product_from_api(item: dict, category_name: str) -> Optional[CatalogProduct]:
    """Convert a Glue API product dict to a CatalogProduct. Returns None to skip."""
    sku = item.get("sku")
    if not sku:
        return None

    name = item.get("name", "").strip()
    if not name:
        return None

    if item.get("discontinued") is True:
        return None

    price_obj = item.get("price") or {}
    price_cents = price_obj.get("amount")
    if not price_cents:
        return None

    brand = item.get("brandName") or None
    url_slug = item.get("urlSlugText") or ""

    was_price_cents = _parse_was_price(price_obj.get("wasPriceDisplay"))

    unit_price, unit_price_label = _parse_comparison_display(price_obj.get("comparisonDisplay"))

    # sellingSize: "200 g", "2 L", "1.5 kg" → normalise to our package_size format
    selling_size = (item.get("sellingSize") or "").strip() or None

    # Category: use deepest available category name ("Dairy, Eggs & Fridge > Cheese")
    cats = item.get("categories") or []
    if cats:
        cat_label = " > ".join(c["name"] for c in cats[:2] if c.get("name"))
    else:
        cat_label = category_name

    image_url = _build_image_url(item.get("assets") or [], url_slug)

    today = date.today()
    normalised_cat = _normalise_category(cat_label) or cat_label

    return CatalogProduct(
        product_name=name,
        price_cents=price_cents,
        was_price_cents=was_price_cents,
        gtin=_sku_to_gtin(sku),
        valid_from=today,
        valid_to=None,
        image_url=image_url,
        category=normalised_cat,
        brand=brand,
        external_id=sku,
        url=f"https://www.aldi.com.au/product/{url_slug}-{sku}" if url_slug else None,
        in_stock=True,  # notForSale only restricts online ordering, not in-store availability
        package_size=selling_size,
        unit_price=unit_price,
        unit_price_label=unit_price_label,
    )


def _sku_to_gtin(sku: str) -> Optional[str]:
    """Return the EAN/GTIN if the SKU is a valid 13-digit barcode, else None."""
    digits = re.sub(r"\D", "", sku)
    return digits if len(digits) == 13 else None


class ALDICatalogScraper(CatalogScraper):
    """
    ALDI Australia catalog scraper using the Spryker Glue API.

    No authentication required. The API is publicly accessible.
    """

    TIMEOUT_S = 30.0

    async def authenticate(self) -> dict:
        return {}  # no auth needed

    async def discover_categories(self) -> list[CategoryNode]:
        """Fetch the live category tree from the Glue API."""
        await self.rate_limiter.wait()
        async with httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
        ) as client:
            resp = await client.get(_CATEGORY_TREE_URL)

        if resp.status_code != 200:
            raise ScraperError(f"ALDI category tree returned {resp.status_code}: {resp.text[:200]}")

        nodes: list[CategoryNode] = []
        for cat in resp.json().get("data", []):
            key = str(cat.get("key", ""))
            name = cat.get("name", "")
            if not key or not name:
                continue
            if key in _PROMO_CATEGORY_KEYS:
                continue

            parent_node = CategoryNode(
                id=key,
                name=name,
                url=f"{_PRODUCT_SEARCH_URL}?categoryTree={key}",
                parent_id=None,
            )
            nodes.append(parent_node)

            for child in cat.get("children", []):
                child_key = str(child.get("key", ""))
                child_name = child.get("name", "")
                if not child_key or not child_name:
                    continue
                if child_key in _PROMO_CATEGORY_KEYS:
                    continue
                nodes.append(
                    CategoryNode(
                        id=child_key,
                        name=f"{name} > {child_name}",
                        url=f"{_PRODUCT_SEARCH_URL}?categoryTree={child_key}",
                        parent_id=key,
                    )
                )

        return nodes

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=3, max=20))
    async def _fetch_page(self, client: httpx.AsyncClient, category_id: str, offset: int) -> dict:
        """Fetch one page of products for a category. Retries on 5xx."""
        await self.rate_limiter.wait()
        resp = await client.get(
            _PRODUCT_SEARCH_URL,
            params={"categoryTree": category_id, "limit": str(_PAGE_SIZE), "offset": str(offset)},
            timeout=self.TIMEOUT_S,
        )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "30"))
            self.rate_limiter.on_rate_limited(retry_after)
            raise ScraperError(f"Rate limited: Retry-After={retry_after}s")
        if resp.status_code >= 500:
            raise ScraperError(f"ALDI server error {resp.status_code} on category {category_id}")
        if resp.status_code != 200:
            raise ScraperError(
                f"ALDI returned {resp.status_code} on category {category_id}: {resp.text[:200]}"
            )
        return resp.json()

    async def crawl_category(self, category: CategoryNode, auth: dict) -> Any:
        """Fetch all pages for a category and return raw API responses."""
        pages: list[dict] = []
        offset = 0

        async with httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
        ) as client:
            while True:
                data = await self._fetch_page(client, category.id, offset)
                pages.append(data)

                pagination = data.get("meta", {}).get("pagination", {})
                total = pagination.get("totalCount", 0)
                fetched_so_far = offset + _PAGE_SIZE

                if fetched_so_far >= total:
                    break
                if len(pages) >= self.config.max_pages_per_category:
                    break

                offset = fetched_so_far

        return {"category_name": category.name, "pages": pages}

    async def parse_products(self, raw: Any, category: CategoryNode) -> list[CatalogProduct]:
        """Convert raw API pages into CatalogProduct instances."""
        cat_name = raw.get("category_name", category.name)
        products: list[CatalogProduct] = []
        seen_skus: set[str] = set()

        for page in raw.get("pages", []):
            for item in page.get("data", []):
                product = _product_from_api(item, cat_name)
                if product is None:
                    continue
                if product.external_id in seen_skus:
                    continue
                seen_skus.add(product.external_id)
                products.append(product)

        return products

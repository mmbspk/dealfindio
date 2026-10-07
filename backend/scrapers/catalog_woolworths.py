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
import secrets
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
    "specialsgroup",  # handled by crawl_specials via _SPECIALS_NODE_IDS
    "1_A363395",  # Everyday Market (third-party marketplace root)
    "1_F89E4BB",  # Healthylife (health supplement store)
    "1_BC1E589",  # Gift Ideas
    "1_9E60D27",  # Footy Finals (seasonal)
    "1_B63CF9E",  # Front of Store (internal/restricted)
}

# Name-fragment fallback for marketplace/non-grocery sub-trees. Used in
# discover_categories() to skip nodes whose NodeId is unknown or rotated.
_SKIP_CATEGORY_NAME_FRAGMENTS = {
    "everyday market",
    "marketplace",
}

# Woolworths specials sub-category node IDs (from the /shop/specials/* pages).
# These are the 13 dedicated specials categories Woolworths shows on their specials
# landing page — crawling them directly is both faster and more accurate than
# applying isSpecial=true to the full grocery category tree.
# NodeId → (url_friendly_name, display_name)
_SPECIALS_NODE_IDS: dict[str, tuple[str, str]] = {
    "1-E5BEE36E": ("specials/fruit-veg", "Fruit & Veg"),
    "1_D5A2236": ("specials/poultry-meat-seafood", "Poultry, Meat & Seafood"),
    "1_DEB537E": ("specials/bakery", "Bakery"),
    "1_6E4F4E4": ("specials/dairy-eggs-fridge", "Dairy, Eggs & Fridge"),
    "1_39FD49C": ("specials/frozen", "Frozen"),
    "1_ACA2FC2": ("specials/pantry", "Pantry"),
    "1_5AF3A0A": ("specials/snacks-confectionery", "Snacks & Confectionery"),
    "1_8E4DA6F": ("specials/drinks", "Drinks"),
    "1_61D6FEB": ("specials/health-wellness", "Health & Wellness"),
    "1_717A94B": ("specials/cleaning-maintenance", "Cleaning & Maintenance"),
    "1_894D0A8": ("specials/baby", "Baby"),
    "1_2432B58": ("specials/pet", "Pet"),
}


class WoolworthsCatalogScraper(CatalogScraper):
    """Full-catalog crawler for Woolworths via the public browse/category API."""

    TIMEOUT_S = 30.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pinned DataImpulse session ID — set after each successful bootstrap so
        # every category request uses the same exit IP as the bootstrap.
        # Regenerated on each re-bootstrap to rotate to a fresh IP after a block.
        self._proxy_session_id: Optional[str] = None

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
        retry=retry_if_exception_type((httpx.TransportError, ScraperError)),
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=15, min=30, max=300),
        reraise=True,
    )
    async def _bootstrap(self) -> dict:
        """Fetch the homepage to seed Akamai session cookies.

        Uses a fresh DataImpulse session ID on every attempt so each retry
        gets a different exit IP — rotating away from any IP Akamai has blocked.
        Stores the winning session ID in self._proxy_session_id so all
        subsequent browse/category requests share the same exit IP.
        """
        attempt_session_id = secrets.token_hex(8)
        async with httpx.AsyncClient(
            headers=_HTML_HEADERS,
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
            proxies=_get_proxy(attempt_session_id),
        ) as client:
            try:
                resp = await client.get(_HOME_URL)
                if resp.status_code in (403, 429):
                    raise ScraperError(
                        f"Woolworths homepage fetch failed: {resp.status_code} — bot-blocked, rotating IP"
                    )
                resp.raise_for_status()
            except httpx.TransportError:
                raise  # retried by tenacity
            except ScraperError:
                raise  # already formatted, retried by tenacity
            except httpx.HTTPError as exc:
                raise ScraperError(f"Woolworths homepage fetch failed: {exc}") from exc

            cookies = {c.name: c.value for c in client.cookies.jar}

        # Bootstrap succeeded — pin this DataImpulse session for all data requests.
        self._proxy_session_id = attempt_session_id
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
            proxies=_get_proxy(self._proxy_session_id),
        ) as client:
            await self.rate_limiter.wait()
            try:
                resp = await client.get(_BOOTSTRAP_URL)
                if resp.status_code in (401, 403):
                    raise AuthExpiredError("Woolworths bootstrap auth expired")
                resp.raise_for_status()
                cats_raw = resp.json().get("ListTopLevelPiesCategories", {}).get("Categories", [])
            except (httpx.HTTPError, ValueError) as exc:
                raise ScraperError(f"Woolworths category discovery failed: {exc}") from exc

        nodes: list[CategoryNode] = []
        for cat in cats_raw:
            node_id = cat.get("NodeId")
            if not node_id or node_id in _SKIP_NODE_IDS:
                continue
            if cat.get("IsSpecial"):
                continue
            desc_lower = (cat.get("Description") or "").lower()
            if any(f in desc_lower for f in _SKIP_CATEGORY_NAME_FRAGMENTS):
                continue
            nodes.append(
                CategoryNode(
                    id=node_id,
                    name=cat.get("Description") or node_id,
                    url=cat.get("UrlFriendlyName") or node_id,
                )
            )
        return nodes

    async def crawl_category(
        self, category: CategoryNode, auth: dict, *, page_sink=None, start_page: int = 1
    ) -> Any:
        """Paginate one top-level department.

        When ``page_sink`` is provided, products are parsed and flushed per page
        instead of buffered — returns ``{"_streamed": True}`` in that case.
        Pages before ``start_page`` are skipped (page checkpoint resume).
        """
        url_name = category.url or category.id
        all_products: list[dict] = []
        # On resume, pre-count the skipped pages so TotalRecordCount termination fires correctly.
        # Woolworths uses a fixed page size of 36.
        total_seen = (start_page - 1) * 36
        today = date.today()

        async with httpx.AsyncClient(
            headers=_JSON_HEADERS,
            cookies=auth.get("cookies", {}),
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
            proxies=_get_proxy(self._proxy_session_id),
        ) as client:
            for page in range(1, self.config.max_pages_per_category + 1):
                if page < start_page:
                    continue  # skip already-checkpointed pages
                await self.rate_limiter.wait()
                data = await self._fetch_page(client, url_name, category, page)
                page_products = _extract_products(data)
                if not page_products:
                    break

                total_seen += len(page_products)

                if page_sink is not None:
                    parsed = [
                        p
                        for item in page_products
                        if (p := _parse_item(item, category, today)) is not None
                    ]
                    await page_sink(parsed, page)
                else:
                    all_products.extend(page_products)

                # Stop when we have consumed the reported total (capped at 10000).
                total = data.get("TotalRecordCount") or 0
                if total > 0 and total_seen >= total:
                    break
                # Short-page heuristic: Woolworths always returns 36 per page.
                if len(page_products) < 36:
                    break

        if page_sink is not None:
            return {"_streamed": True}

        return {
            "category_id": category.id,
            "category_name": category.name,
            "products": all_products,
        }

    async def parse_products(self, raw: Any, category: CategoryNode) -> list[CatalogProduct]:
        """Convert raw product dicts into CatalogProduct instances."""
        products: list[CatalogProduct] = []
        today = date.today()
        for item in raw.get("products", []):
            product = _parse_item(item, category, today)
            if product is not None:
                products.append(product)
        return products

    async def crawl_specials(self, auth: dict, product_sink=None, resume_page: int = 1) -> Any:
        """Fetch all Woolworths specials via the dedicated specials sub-category nodes.

        Woolworths has 12 specials sub-category pages (/shop/specials/fruit-veg, etc.)
        that map to specific NodeIds in the browse/category API. Crawling these
        directly is faster and more accurate than applying isSpecial=true to the full
        grocery tree (~7,700 specials vs ~22,000 with the isSpecial filter which
        picks up non-promotional markdowns).

        Per-node resume: completed node IDs are stored in Redis under
        ``specials_resume:woolworths:{DATE}`` (25h TTL). If the same-day crawl is
        re-triggered, already-finished nodes are skipped automatically.
        """
        import json as _json
        from datetime import date as _date

        from scrapers.catalog_base import HumanLikeRateLimiter

        PAGE_SIZE = 36
        cookies = auth.get("cookies", {})
        seen_stockcodes: set = set()
        total_products = 0
        today = _date.today()

        # Per-node resume: load completed node IDs from Redis (same-day key).
        redis = self.redis_client  # set by CatalogScraper.__init__ from db/redis args
        _resume_key = f"specials_resume:woolworths:{today.isoformat()}"
        completed_nodes: set[str] = set()
        if redis is not None:
            try:
                raw_resume = redis.get(_resume_key)
                if raw_resume:
                    completed_nodes = set(_json.loads(raw_resume))
                    self.logger.info(
                        "Woolworths specials: resuming — skipping %d already-completed nodes: %s",
                        len(completed_nodes),
                        completed_nodes,
                    )
            except Exception as exc:
                self.logger.warning("Could not read Woolworths specials resume key: %s", exc)

        # Specials crawl uses a faster rate limiter — Akamai is cookie-based,
        # not request-frequency-based, so we can safely go faster here than
        # the full catalog crawl which has to stay stealthy across many hours.
        specials_rate_limiter = HumanLikeRateLimiter(min_delay_s=1.0, max_delay_s=3.0)

        async with httpx.AsyncClient(
            headers=_JSON_HEADERS,
            cookies=cookies,
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
            proxies=_get_proxy(self._proxy_session_id),
        ) as client:
            # Re-warm session cookies — bootstrap may be stale.
            try:
                async with httpx.AsyncClient(
                    headers=_HTML_HEADERS,
                    follow_redirects=True,
                    timeout=self.TIMEOUT_S,
                    proxies=_get_proxy(self._proxy_session_id),
                ) as warm_client:
                    await warm_client.get("https://www.woolworths.com.au/")
                    for c in warm_client.cookies.jar:
                        client.cookies.set(c.name, c.value, c.domain)
            except Exception as exc:
                self.logger.warning("Woolworths homepage warm-up failed: %s", exc)

            remaining = len(_SPECIALS_NODE_IDS) - len(completed_nodes)
            self.logger.info(
                "Woolworths specials: crawling %d/%d specials categories",
                remaining,
                len(_SPECIALS_NODE_IDS),
            )

            specials_cat = CategoryNode(id="specials", name="")

            for node_id, (url_name, cat_name) in _SPECIALS_NODE_IDS.items():
                if node_id in completed_nodes:
                    self.logger.info(
                        "Woolworths specials: skipping already-completed '%s'", cat_name
                    )
                    continue

                cat_new_items: list[dict] = []
                cat_total_fetched = 0  # total items seen (including cross-category dupes)

                for page in range(1, self.config.max_pages_per_category + 1):
                    await specials_rate_limiter.wait()
                    try:
                        resp = await client.get(
                            _BROWSE_URL,
                            params={
                                "pageNumber": page,
                                "pageSize": PAGE_SIZE,
                                "sortType": "TraderRelevance",
                                "url": f"/shop/{url_name}",
                                "location": f"/shop/{url_name}",
                                "formatObject": json.dumps(
                                    {"name": cat_name}, separators=(",", ":")
                                ),
                                "categoryId": node_id,
                                "isSpecial": "true",
                            },
                        )
                    except httpx.HTTPError as exc:
                        self.logger.warning(
                            "Woolworths specials: %s page %d failed: %s", cat_name, page, exc
                        )
                        break

                    if resp.status_code in (401, 403):
                        raise AuthExpiredError(f"Woolworths specials returned {resp.status_code}")
                    if resp.status_code == 429:
                        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                        specials_rate_limiter.on_rate_limited(retry_after)
                        raise ScraperError("Woolworths specials rate-limited (429)")
                    if resp.status_code != 200:
                        break

                    try:
                        data = resp.json()
                    except ValueError:
                        break

                    page_products = _extract_products(data)
                    if not page_products:
                        break

                    cat_total_fetched += len(page_products)
                    for item in page_products:
                        sc = item.get("Stockcode")
                        key = sc if sc else id(item)
                        if key not in seen_stockcodes:
                            seen_stockcodes.add(key)
                            cat_new_items.append(item)

                    total = data.get("TotalRecordCount") or 0
                    if len(page_products) < PAGE_SIZE:
                        break
                    # Stop when we've fetched at least as many as TotalRecordCount.
                    # Use a 10% margin because the proxy can return slightly more
                    # items per page than PAGE_SIZE, so exact equality may never hit.
                    if total > 0 and cat_total_fetched >= total * 0.9:
                        break

                if cat_new_items:
                    parsed = [
                        p
                        for item in cat_new_items
                        if (p := _parse_item(item, specials_cat, today)) is not None
                    ]
                    if parsed and product_sink:
                        await product_sink(parsed, specials_cat)
                    total_products += len(cat_new_items)
                    self.logger.info(
                        "Woolworths specials: '%s' → %d unique products (running total: %d)",
                        cat_name,
                        len(cat_new_items),
                        total_products,
                    )

                # Mark this node as done in Redis so a same-day re-run skips it.
                if redis is not None:
                    try:
                        completed_nodes.add(node_id)
                        redis.setex(_resume_key, 90000, _json.dumps(list(completed_nodes)))
                    except Exception as exc:
                        self.logger.warning(
                            "Could not save Woolworths specials resume key: %s", exc
                        )

        self.logger.info(
            "Woolworths specials: fetched %d unique products across %d specials categories",
            total_products,
            len(_SPECIALS_NODE_IDS),
        )
        return {"_sunk": True, "products_found": total_products}

    async def parse_specials(self, raw: Any) -> list[CatalogProduct]:
        """Products already parsed and flushed inline during crawl_specials."""
        return []

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


def _parse_item(item: dict, category: CategoryNode, today: date) -> Optional[CatalogProduct]:
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

    # Everyday Market products have 10-digit stockcodes (range 1094737927–1141985970).
    # Real grocery SKUs are ≤7 digits. IsMarketProduct is also checked but Woolworths
    # sometimes omits it on marketplace items — the stockcode length is more reliable.
    if item.get("IsMarketProduct") or (stockcode and len(str(stockcode)) >= 10):
        return None

    image_url = item.get("MediumImageFile") or item.get("SmallImageFile")
    if not image_url and stockcode:
        image_url = f"https://cdn0.woolworths.media/content/wowproductimages/medium/{stockcode}.jpg"

    # Derive category label: use SAP department name when available, then normalise.
    additional = item.get("AdditionalAttributes") or {}

    # Secondary marketplace signal from AdditionalAttributes (guards against Woolworths
    # changing its stockcode numbering scheme in future).
    if additional.get("SourcePlatform") == "WMP":
        return None

    is_online_only = bool(item.get("IsOnlineOnly"))

    dept_name = additional.get("sapdepartmentname") or additional.get("sapsubcategoryname")
    raw_label = f"{category.name} > {dept_name}" if dept_name else category.name
    category_label = _normalise_category(raw_label) or category.name

    brand = item.get("Brand") or None
    in_stock = bool(item.get("IsInStock", True)) and bool(item.get("IsAvailable", True))

    package_size = item.get("PackageSize") or None
    # Woolworths uses "1EA" for single-item products (not informative — hide it).
    # Normalise multi-pack count markers: "44PK", "44EA" → "44 Pack".
    if package_size:
        pk_m = re.match(r"^(\d+)\s*(?:ea|each|pk|pack)$", package_size, re.IGNORECASE)
        if pk_m:
            count = int(pk_m.group(1))
            package_size = f"{count} Pack" if count > 1 else None
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
        is_online_only=is_online_only,
    )

"""
Coles full-catalog scraper (public storefront JSON, no login required).

Coles' catalog is public — no account needed. The storefront is a Next.js app
whose data routes serve JSON we can read directly:

  Bootstrap (HTML headers):
    GET https://www.coles.com.au/  ->  scrape ``buildId`` from __NEXT_DATA__
  Category tree (JSON headers):
    GET /_next/data/{buildId}/en/browse.json  ->  pageProps.allProductCategories
  Per-category products (JSON headers), paged:
    GET /_next/data/{buildId}/en/browse/{seoToken}.json?slug={seoToken}&page={n}
    ->  pageProps.results  (list; _type=="PRODUCT" are real products)

Two hard-won details drive the design:
  1. **Header discipline beats bot protection.** Coles sits behind Imperva.
     Requesting the HTML homepage with an API ``Accept: application/json`` header
     trips the challenge; using full browser headers (Sec-Fetch-*, HTML Accept,
     Upgrade-Insecure-Requests) returns the real page reliably. So the homepage
     bootstrap uses HTML headers and the data-route calls use JSON headers.
  2. **The buildId is the session key**, not a subscription key. It rotates on
     each Coles deploy, so we scrape it fresh at auth time and cache it.

We crawl per **top-level department** (17 of them). Each department's browse
response embeds a ``catalogGroupView`` subtree listing its subcategories; we use
that to label each product with its full ``L1 > L2`` path. No extra requests —
the L2 path comes for free from the data already fetched per department page.
"""

from __future__ import annotations

import asyncio
import random
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
    parse_retry_after as _retry_after,
)
from scrapers.catalog_base import (
    to_cents as _to_cents,
)

# Coles JSON requests must carry a Referer; override the shared base.
_JSON_HEADERS = {
    **_JSON_HEADERS_BASE,
    "Accept": "application/json",
    "Referer": "https://www.coles.com.au/browse",
}

_HOME_URL = "https://www.coles.com.au/"
# Coles serves product images from this CDN; imageUris carry only the relative
# path (e.g. "/8/8150288.jpg"), so we prefix it to a usable absolute URL.
_IMAGE_CDN_BASE = "https://productimages.coles.com.au/productimages"
_BUILD_ID_RE = re.compile(r'"buildId":"([^"]+)"')


class _BotChallengeError(ScraperError):
    """Internal: homepage returned an Imperva challenge instead of the app.

    Retryable — a fresh connection usually clears it.
    """


# Non-category promo/utility groups to skip when building crawl targets.
_SKIP_SEO_TOKENS = {
    "big-pack-value",
    "deliver-more-range",
    "down-down",
    "bonus-entry-products",
    "tobacco",
}


class ColesCatalogScraper(CatalogScraper):
    """Full-catalog crawler for Coles via the public storefront data routes."""

    TIMEOUT_S = 30.0

    # Imperva will silently block sustained sessions after ~20 min. A longer
    # between-category pause (30–90s) keeps the request rate low enough to
    # avoid triggering the bot-detection threshold.
    _BETWEEN_CATEGORY_MIN_S = 30.0
    _BETWEEN_CATEGORY_MAX_S = 90.0

    # How long to cool down before re-bootstrapping after Imperva blocks us.
    # First block: 5 min. Second consecutive block: 15 min. Third+: 30 min.
    _REAUTH_COOLDOWN_S = (300, 900, 1800)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client: Optional[httpx.AsyncClient] = None
        self._consecutive_bot_blocks: int = 0
        # Set after a successful bootstrap — pinned session ID used for all data
        # route requests so DataImpulse keeps the same exit IP mid-run.
        # None until first successful bootstrap.
        self._proxy_session_id: Optional[str] = None

    def _data_url(self, build_id: str, path: str) -> str:
        return f"{_HOME_URL}_next/data/{build_id}/en/{path}"

    async def authenticate(self) -> dict:
        """Bootstrap: fetch the homepage (HTML headers) and scrape the buildId.

        Returns the buildId + session cookies. No login — the catalog is public.
        Wrapped in retry/backoff because Imperva intermittently serves a bot
        challenge (a short page with no buildId, sometimes a truncated body);
        a fresh connection usually clears on the next attempt.

        Keeps the bootstrapped httpx client alive as self._client so all
        subsequent data-route requests reuse the same TLS session — Imperva
        ties its incap_ses_* cookies to the originating TLS connection.
        """
        try:
            return await self._bootstrap()
        except _BotChallengeError as exc:
            raise ScraperError(f"Coles bootstrap blocked after retries: {exc}") from exc

    @retry(
        retry=retry_if_exception_type((_BotChallengeError, httpx.TransportError)),
        stop=stop_after_attempt(8),
        wait=wait_exponential(multiplier=10, min=15, max=180),
        reraise=True,
    )
    async def _bootstrap(self) -> dict:
        import secrets

        # Close any existing client before creating a fresh TLS session.
        if self._client:
            await self._client.aclose()
            self._client = None

        # Use a fresh random sess= on every bootstrap attempt so each retry
        # gets a different DataImpulse exit IP — rotating away from any IP
        # that Imperva has already flagged.  Once this bootstrap succeeds we
        # store the sess= token and reuse it for all data-route requests.
        attempt_session_id = secrets.token_hex(8)
        client = httpx.AsyncClient(
            headers=_HTML_HEADERS,
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
            proxies=_get_proxy(attempt_session_id),
        )
        try:
            resp = await client.get(_HOME_URL)
            resp.raise_for_status()
        except httpx.TransportError:
            await client.aclose()
            raise
        except httpx.HTTPError as exc:
            await client.aclose()
            raise ScraperError(f"Coles bootstrap failed: {exc}") from exc

        match = _BUILD_ID_RE.search(resp.text)
        if not match:
            await client.aclose()
            raise _BotChallengeError(f"no buildId in {len(resp.text)}-byte response")

        # Bootstrap succeeded — pin this session ID for the rest of the run.
        self._proxy_session_id = attempt_session_id
        # Keep the client alive — Imperva ties incap_ses_* to this TLS session.
        self._client = client
        cookies = {c.name: c.value for c in client.cookies.jar}
        return {"build_id": match.group(1), "cookies": cookies}

    async def discover_categories(self) -> list[CategoryNode]:
        """Fetch the category tree and return top-level department crawl targets.

        Each returned ``CategoryNode`` carries ``children`` populated from the
        L2 subcategory list embedded in the browse response — so when a product
        is later matched by the L2 seoToken we see in its category field, we can
        label it with the full ``L1 > L2`` path without any extra requests.

        We crawl at L1 (department) granularity because the nested L2 route
        (``browse/{L1}/{L2}.json``) returns empty results. All products for a
        department come from the flat ``browse/{L1}.json`` endpoint.
        """
        auth = await self._get_or_refresh_auth()
        build_id = auth["build_id"]
        # Reuse the bootstrapped client so Imperva's TLS-bound session stays valid.
        # A new AsyncClient here gets a different TLS handshake and Imperva serves
        # a challenge page instead of JSON.
        client = self._client
        if client is None:
            raise AuthExpiredError("Coles client not initialised — bootstrap required")
        client.headers.update(_JSON_HEADERS)
        await self.rate_limiter.wait()
        try:
            resp = await client.get(self._data_url(build_id, "browse.json"))
            if resp.status_code in (401, 403):
                raise AuthExpiredError("Coles browse.json auth expired")
            resp.raise_for_status()
            if not resp.content:
                raise AuthExpiredError("Coles browse.json returned empty body")
            try:
                tree = resp.json().get("pageProps", {}).get("allProductCategories", {})
            except ValueError:
                raise AuthExpiredError(
                    f"Coles browse.json returned non-JSON ({len(resp.content)}b) — bot-protection challenge"
                )
        except httpx.HTTPError as exc:
            raise ScraperError(f"Coles category discovery failed: {exc}") from exc

        top = tree.get("catalogGroupView", []) if isinstance(tree, dict) else []
        departments: list[CategoryNode] = []
        for l1 in top:
            l1_token = l1.get("seoToken")
            l1_name = l1.get("name") or l1_token
            if not l1_token or l1_token in _SKIP_SEO_TOKENS:
                continue
            # Build the L2 children list from the embedded subcategory tree.
            children: list[CategoryNode] = []
            for l2 in l1.get("catalogGroupView") or []:
                l2_token = l2.get("seoToken")
                l2_name = l2.get("name") or l2_token
                if l2_token:
                    children.append(
                        CategoryNode(
                            id=l2_token,
                            name=l2_name,
                            url=l2_token,
                            parent_id=l1_token,
                        )
                    )
            departments.append(
                CategoryNode(
                    id=l1_token,
                    name=l1_name,
                    url=l1_token,
                    children=children,
                )
            )
        return departments

    async def crawl_category(
        self, category: CategoryNode, auth: dict, *, page_sink=None, start_page: int = 1
    ) -> Any:
        """Page through one department. Returns the accumulated pages + L2 map.

        When ``page_sink`` is provided, products are parsed and flushed per page
        instead of buffered — returns ``{"_streamed": True}`` in that case.
        Pages before ``start_page`` are skipped (page checkpoint resume).
        """
        build_id = auth["build_id"]
        seo = category.url or category.id
        l2_map = {c.id: c.name for c in (category.children or [])}
        today = date.today()

        # Between-category pause: keep the sustained request rate low enough
        # that Imperva doesn't flag the session after the first few categories.
        # Log before the sleep so the UI shows which category is next rather
        # than going dark for up to 90s.
        self.logger.info("Crawling category '%s' (start_page=%d)", category.name, start_page)
        await asyncio.sleep(
            random.uniform(self._BETWEEN_CATEGORY_MIN_S, self._BETWEEN_CATEGORY_MAX_S)
        )

        # Reuse the bootstrapped client so Imperva's TLS-bound session cookie stays valid.
        client = self._client
        if client is None:
            client = httpx.AsyncClient(
                headers=_JSON_HEADERS,
                cookies=auth.get("cookies", {}),
                follow_redirects=True,
                timeout=self.TIMEOUT_S,
                proxies=_get_proxy(self._proxy_session_id),
            )
        else:
            # Switch to JSON accept header for data-route requests.
            client.headers.update(_JSON_HEADERS)

        pages: list[dict] = []
        for page in range(1, self.config.max_pages_per_category + 1):
            if page < start_page:
                continue  # skip already-checkpointed pages
            await self.rate_limiter.wait()
            data = await self._fetch_page(client, build_id, seo, page)
            results = _extract_results(data)
            if not results:
                break  # past the last page

            if page_sink is not None:
                # Streaming mode: parse this page immediately and flush
                page_products = [
                    p
                    for item in results
                    if (p := _parse_item(item, category, today, l2_map)) is not None
                ]
                await page_sink(page_products, page)
            else:
                pages.append(data)

            total = _total_results(data)
            if total is not None:
                if page * self.config.page_size >= total:
                    break
            elif len(results) < self.config.page_size:
                break

        if page_sink is not None:
            return {"_streamed": True, "category": category.id, "l2_map": l2_map}

        # Carry L2 children as a seoToken→name dict for parse_products.
        return {"category": category.id, "pages": pages, "l2_map": l2_map}

    async def parse_products(self, raw: Any, category: CategoryNode) -> list[CatalogProduct]:
        """Flatten paged responses into CatalogProduct rows.

        Each product's category is labeled as ``L1 > L2`` when a subcategory
        can be identified from the product's embedded ``categories`` field or
        from the L2 map carried in ``raw["l2_map"]``. Falls back to the L1
        department name when no L2 match is found.
        """
        products: list[CatalogProduct] = []
        today = date.today()
        l2_map: dict[str, str] = raw.get("l2_map") or {}
        for page in raw.get("pages", []):
            for item in _extract_results(page):
                product = _parse_item(item, category, today, l2_map)
                if product is not None:
                    products.append(product)
        return products

    async def crawl_specials(self, auth: dict, product_sink=None, resume_page: int = 1) -> Any:
        """Fetch all Coles on-special products from the Next.js on-special data route.

        Rotates to a fresh DataImpulse IP every time Imperva bot-blocks us, resuming
        from the blocked page each time until all pages are exhausted.  Products are
        sunk page-by-page so nothing is lost across IP rotations.
        Returns ``{"_sunk": True, "products_found": N}``.
        """
        specials_cat = CategoryNode(id="specials", name="")
        today = date.today()
        products_found = 0
        start_page = max(1, resume_page)
        rotation_count = 0
        MAX_ROTATIONS = 20  # safety cap — 20 IPs × ~80 pages × ~60 products = 96,000 cap

        while start_page <= self.config.max_pages_per_category:
            build_id = auth.get("build_id")
            if not build_id:
                raise AuthExpiredError("Coles buildId missing from auth — re-bootstrap required")

            client = self._client
            if client is None:
                client = httpx.AsyncClient(
                    headers=_JSON_HEADERS,
                    cookies=auth.get("cookies", {}),
                    follow_redirects=True,
                    timeout=self.TIMEOUT_S,
                    proxies=_get_proxy(self._proxy_session_id),
                )
            client.headers.update(_JSON_HEADERS)

            bot_blocked_at: Optional[int] = None

            for page in range(start_page, self.config.max_pages_per_category + 1):
                await asyncio.sleep(random.uniform(3.0, 8.0))
                url = self._data_url(build_id, "on-special.json")
                params = {"slug": "on-special", "page": page}
                try:
                    resp = await client.get(url, params=params)
                except httpx.HTTPError as exc:
                    raise ScraperError(f"Coles on-special fetch failed: {exc}") from exc

                if resp.status_code in (401, 403):
                    bot_blocked_at = page
                    break
                if resp.status_code == 404:
                    bot_blocked_at = page
                    break
                if resp.status_code == 429:
                    self.rate_limiter.on_rate_limited(_retry_after(resp.headers.get("Retry-After")))
                    raise ScraperError("Coles on-special rate-limited (429)")
                try:
                    resp.raise_for_status()
                    if not resp.content:
                        bot_blocked_at = page
                        break
                    try:
                        data = resp.json()
                    except ValueError:
                        self.logger.warning(
                            "Coles on-special non-JSON body at page %d (%db) — bot block",
                            page,
                            len(resp.content),
                        )
                        bot_blocked_at = page
                        break
                except httpx.HTTPError as exc:
                    raise ScraperError(f"Coles on-special fetch failed: {exc}") from exc

                results = _extract_results(data)
                if not results:
                    # Genuine end of specials listing — done
                    start_page = self.config.max_pages_per_category + 1
                    break

                page_products = []
                for item in results:
                    product = _parse_item(item, specials_cat, today, {})
                    if product is not None:
                        page_products.append(product)

                if page_products and product_sink is not None:
                    await product_sink(page_products, specials_cat, page=page)
                products_found += len(page_products)

                # Check if we've fetched everything Coles reports
                total = _total_results(data)
                if total is not None and products_found >= total:
                    start_page = self.config.max_pages_per_category + 1
                    break
                elif total is None and len(results) < self.config.page_size:
                    start_page = self.config.max_pages_per_category + 1
                    break
                else:
                    start_page = page + 1

            if bot_blocked_at is None:
                # Inner loop completed without a block — all done
                break

            # Bot-blocked mid-run — rotate to a fresh IP and resume
            rotation_count += 1
            if rotation_count > MAX_ROTATIONS:
                self.logger.warning(
                    "Coles on-special reached max IP rotations (%d) — stopping at page %d "
                    "with %d products saved",
                    MAX_ROTATIONS,
                    bot_blocked_at,
                    products_found,
                )
                break

            self.logger.info(
                "Coles on-special bot-blocked at page %d (%d products so far) — "
                "rotating to fresh IP #%d and resuming",
                bot_blocked_at,
                products_found,
                rotation_count,
            )
            start_page = bot_blocked_at
            await self._invalidate_auth()
            auth = await self._get_or_refresh_auth()
            # _bootstrap assigned a new _proxy_session_id — next iteration uses fresh IP

        self.logger.info(
            "Fetched %d Coles on-special products across %d IP rotation(s)",
            products_found,
            rotation_count,
        )
        return {"_sunk": True, "products_found": products_found}

    async def parse_specials(self, raw: Any) -> list[CatalogProduct]:
        """Products are already parsed and sunk inline in crawl_specials."""
        return []

    async def _crawl_with_reauth(
        self, category: CategoryNode, auth: dict, *, page_sink=None, start_page: int = 1
    ) -> Any:
        """Coles override: re-auth with Imperva cool-down before retrying.

        On a bot-protection block (AuthExpiredError from non-JSON body), waits
        progressively longer before re-bootstrapping so Imperva's IP flag clears.
        Consecutive blocks get 5 / 15 / 30 min waits. Resets on a clean crawl.
        """
        try:
            result = await self.crawl_category(
                category, auth, page_sink=page_sink, start_page=start_page
            )
            self._consecutive_bot_blocks = 0  # clean run — reset counter
            return result
        except AuthExpiredError:
            cooldowns = self._REAUTH_COOLDOWN_S
            wait_s = cooldowns[min(self._consecutive_bot_blocks, len(cooldowns) - 1)]
            self._consecutive_bot_blocks += 1
            self.logger.warning(
                "Coles bot-protection block #%d for %r — cooling down %ds before re-auth",
                self._consecutive_bot_blocks,
                category.name,
                wait_s,
            )
            await asyncio.sleep(wait_s)
            await self._invalidate_auth()
            auth = await self._get_or_refresh_auth()
            # One retry after cool-down. Re-read checkpoint in case page_sink
            # flushed some pages before the block — don't re-flush them.
            retry_start = self._get_page_checkpoint(category.id) + 1
            result = await self.crawl_category(
                category, auth, page_sink=page_sink, start_page=retry_start
            )
            self._consecutive_bot_blocks = 0
            return result

    async def _fetch_page(
        self, client: httpx.AsyncClient, build_id: str, seo_token: str, page: int
    ) -> dict:
        """Fetch one browse data-route page. Raises on auth failure / 429."""
        url = self._data_url(build_id, f"browse/{seo_token}.json")
        params = {"slug": seo_token, "page": page}
        try:
            resp = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ScraperError(f"Coles page fetch failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise AuthExpiredError(f"Coles returned {resp.status_code} (auth expired)")
        if resp.status_code == 404:
            # A rotated buildId 404s every data route — treat as auth-expired so
            # the base class re-bootstraps a fresh buildId and retries.
            raise AuthExpiredError("Coles data route 404 (buildId rotated?)")
        if resp.status_code == 429:
            self.rate_limiter.on_rate_limited(_retry_after(resp.headers.get("Retry-After")))
            raise ScraperError("Coles rate-limited (429)")
        try:
            resp.raise_for_status()
            if not resp.content:
                raise AuthExpiredError("Coles returned empty body (bot-protection silent block)")
            try:
                return resp.json()
            except ValueError:
                # Imperva challenge page — HTML body, not JSON. Re-auth to get a fresh session.
                raise AuthExpiredError(
                    f"Coles returned non-JSON body ({len(resp.content)}b) — likely bot-protection challenge"
                )
        except httpx.HTTPError as exc:
            raise ScraperError(f"Coles page parse failed: {exc}") from exc


# ── Response helpers ─────────────────────────────────────────────────────────


def _extract_results(page: dict) -> list[dict]:
    """Pull the product-result list out of a browse data-route page.

    Coles nests products under ``pageProps.searchResults.results``. Older/alternate
    shapes (``pageProps.results`` or a bare top-level ``results``) are kept as
    fallbacks so the parser is resilient to storefront changes.
    """
    if not isinstance(page, dict):
        return []
    props = page.get("pageProps")
    if isinstance(props, dict):
        search = props.get("searchResults")
        if isinstance(search, dict) and isinstance(search.get("results"), list):
            return search["results"]
        if isinstance(props.get("results"), list):
            return props["results"]
    if isinstance(page.get("results"), list):
        return page["results"]
    return []


def _total_results(page: dict) -> Optional[int]:
    """Return the total product count Coles reports for a category, if present."""
    props = page.get("pageProps") if isinstance(page, dict) else None
    search = props.get("searchResults") if isinstance(props, dict) else None
    if isinstance(search, dict):
        n = search.get("noOfResults")
        if isinstance(n, int):
            return n
    return None


def _parse_item(
    item: dict,
    category: CategoryNode,
    today: date,
    l2_map: Optional[dict[str, str]] = None,
) -> Optional[CatalogProduct]:
    """Map one Coles product record to a CatalogProduct. Skips ad/non-products.

    ``l2_map`` is a ``{seoToken: name}`` dict of the L1 department's subcategories
    (from ``discover_categories``). When a product's embedded ``categories`` list
    contains a token that appears in ``l2_map``, the category label is set to
    ``"L1 > L2"``; otherwise it falls back to the L1 department name alone.
    """
    if not isinstance(item, dict):
        return None
    if item.get("_type") and item.get("_type") != "PRODUCT":
        return None  # ad tile / non-product

    name = item.get("name") or item.get("description")
    pricing = item.get("pricing") or {}
    now = pricing.get("now")
    if not name or now is None:
        return None

    price_cents = _to_cents(now)
    if price_cents is None or price_cents <= 0:
        return None

    # Coles uses was=0 (not absent) to mean "no promo".
    was = pricing.get("was")
    was_cents = _to_cents(was) if was else None
    if was_cents is not None and was_cents <= price_cents:
        was_cents = None

    image_uris = item.get("imageUris") or []
    image_url = None
    if isinstance(image_uris, list) and image_uris:
        first = image_uris[0]
        if isinstance(first, dict):
            raw_uri = first.get("uri")
            image_url = _absolute_image_url(raw_uri)

    availability = item.get("availability")
    in_stock = True
    if isinstance(availability, bool):
        in_stock = availability
    elif item.get("availabilityType") == "InStock":
        in_stock = True

    # Derive the L1 > L2 category label. Normalise the L1 department to its
    # canonical flat name, then re-append the meaningful L2 subcategory.
    #
    # NOTE: ``normalise_category`` maps a raw "L1 > RETAIL_SECTION" string (e.g.
    # "Dairy, Eggs & Fridge > GROCERIES") to a flat canonical department and
    # DROPS the suffix. Our L2 token (e.g. "Cheese") is a real subcategory we
    # want to keep, so we must normalise the L1 alone and add the L2 back — never
    # run the composed "L1 > L2" through the normaliser (that would discard L2).
    canonical_l1 = _normalise_category(category.name) or category.name
    l2_name: Optional[str] = None
    if l2_map:
        # Coles embeds a "categories" list on each product item.  Each element
        # is a dict with a "seoToken" or "name" key matching our L2 map.
        item_cats = item.get("categories") or []
        if isinstance(item_cats, list):
            for cat_entry in item_cats:
                if isinstance(cat_entry, dict):
                    token = cat_entry.get("seoToken") or cat_entry.get("id")
                    if token and token in l2_map:
                        l2_name = l2_map[token]
                        break

    category_label = f"{canonical_l1} > {l2_name}" if l2_name else canonical_l1

    return CatalogProduct(
        product_name=str(name),
        price_cents=price_cents,
        was_price_cents=was_cents,
        gtin=None,
        valid_from=today,
        image_url=image_url,
        category=category_label,
        brand=item.get("brand"),
        external_id=str(item.get("id")) if item.get("id") is not None else None,
        url=None,
        in_stock=in_stock,
        package_size=_extract_coles_package_size(item),
        unit_price=_extract_coles_unit_price(pricing),
        unit_price_label=_extract_coles_unit_price_label(pricing),
    )


def _extract_coles_package_size(item: dict) -> Optional[str]:
    """Extract package size from the product name (Coles API has no dedicated field).

    The name typically ends with the size: "Coca-Cola No Sugar 375mL 24 Pack".
    We extract just the size portion — the last token group containing a unit.
    """
    name = item.get("name") or ""
    # Match a size token at the end of the name: e.g. "375mL 24 Pack", "2L", "500g"
    m = re.search(
        r"(\d+\.?\d*\s*(?:ml|mL|L|kg|g|oz)\b(?:\s+\d+\s*(?:Pack|pk|pack))?|\d+\s*(?:Pack|pk|pack))\s*$",
        name,
    )
    if m:
        return m.group(0).strip()
    return None


_COLES_UNIT_PRICE_RE = re.compile(r"\$?([\d.]+)\s*/\s*(.+)", re.IGNORECASE)


def _extract_coles_unit_price(pricing: dict) -> Optional[float]:
    """Parse the comparable unit price from Coles pricing.comparable string.

    Examples: "$1.50 / 100g", "$2.40 / 100ml", "$0.99 / ea"
    Returns the numeric value only (e.g. 1.50).
    """
    comparable = pricing.get("comparable") or ""
    m = _COLES_UNIT_PRICE_RE.search(comparable)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _extract_coles_unit_price_label(pricing: dict) -> Optional[str]:
    """Extract the unit label from the comparable string, e.g. '100g', '100ml', 'ea'."""
    comparable = pricing.get("comparable") or ""
    m = _COLES_UNIT_PRICE_RE.search(comparable)
    if m:
        return m.group(2).strip() or None
    return None


def _absolute_image_url(uri: Optional[str]) -> Optional[str]:
    """Turn a Coles relative image path into an absolute CDN URL.

    ``imageUris`` entries look like "/8/8150288.jpg"; already-absolute URLs are
    returned unchanged.
    """
    if not uri:
        return None
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    return f"{_IMAGE_CDN_BASE}{uri if uri.startswith('/') else '/' + uri}"

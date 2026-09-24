"""
Coles Australia specials scraper using the Next.js internal data API.

Replaces the BeautifulSoup HTML scraper (`coles.py`) with the same
``/_next/data/{buildId}/`` approach already used by ``catalog_coles.py``.

Endpoint pattern:
  Bootstrap (HTML headers, homepage):
    GET https://www.coles.com.au/  ->  scrape ``buildId`` from __NEXT_DATA__
  Specials (JSON headers), paged:
    GET /_next/data/{buildId}/en/on-special.json?page={n}
    ->  pageProps.searchResults.results  (products + noOfResults)

Two Imperva-beating rules inherited from catalog_coles.py:
  1. Homepage must be fetched with full browser HTML headers; JSON data routes
     use XHR-style JSON headers.
  2. buildId rotates on every Coles deploy — scrape it fresh each run.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scrapers.base import ScrapedProduct, Scraper, ScraperError

_HOME_URL = "https://www.coles.com.au/"
_IMAGE_CDN_BASE = "https://productimages.coles.com.au/productimages"
_BUILD_ID_RE = re.compile(r'"buildId":"([^"]+)"')

# Matches catalog_coles.py header discipline exactly.
_HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AU,en-GB;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
_JSON_HEADERS = {
    "User-Agent": _HTML_HEADERS["User-Agent"],
    "Accept": "application/json",
    "Accept-Language": "en-AU,en;q=0.9",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Referer": "https://www.coles.com.au/on-special",
}

# Coles /on-special uses the same pageSize as the browse routes.
_PAGE_SIZE = 48
_MAX_PAGES = 20  # hard cap — on-special typically has 300–800 products


class _BotChallengeError(ScraperError):
    """Imperva challenge page instead of real app — retryable."""


class ColesSpecialsScraper(Scraper):
    """
    Coles weekly specials scraper using the Next.js ``/_next/data`` API.

    ``discover_source`` bootstraps the buildId from the homepage.
    ``fetch_flyer`` pages through ``/on-special.json`` and returns raw JSON pages.
    ``parse_flyer`` flattens those pages into ``ScrapedProduct`` objects.
    """

    TIMEOUT_S = 30.0

    def _data_url(self, build_id: str, path: str) -> str:
        return f"{_HOME_URL}_next/data/{build_id}/en/{path}"

    # ── discover_source ───────────────────────────────────────────────────────

    async def discover_source(self) -> dict:
        """Fetch the homepage and extract the buildId + session cookies."""
        try:
            return await self._bootstrap()
        except _BotChallengeError as exc:
            raise ScraperError(f"Coles bootstrap blocked after retries: {exc}") from exc

    @retry(
        retry=retry_if_exception_type((_BotChallengeError, httpx.TransportError)),
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=5, min=5, max=120),
        reraise=True,
    )
    async def _bootstrap(self) -> dict:
        async with httpx.AsyncClient(
            headers=_HTML_HEADERS, follow_redirects=True, timeout=self.TIMEOUT_S
        ) as client:
            try:
                resp = await client.get(_HOME_URL)
                resp.raise_for_status()
            except httpx.TransportError:
                raise
            except httpx.HTTPError as exc:
                raise ScraperError(f"Coles bootstrap failed: {exc}") from exc

            match = _BUILD_ID_RE.search(resp.text)
            if not match:
                raise _BotChallengeError(f"no buildId in {len(resp.text)}-byte response")
            cookies = {c.name: c.value for c in client.cookies.jar}
        return {"build_id": match.group(1), "cookies": cookies}

    # ── fetch_flyer ───────────────────────────────────────────────────────────

    async def fetch_flyer(self, source: dict) -> list[dict]:
        """Page through ``/on-special.json`` and return all raw JSON pages."""
        build_id = source["build_id"]
        cookies = source.get("cookies", {})
        pages: list[dict] = []

        async with httpx.AsyncClient(
            headers=_JSON_HEADERS,
            cookies=cookies,
            follow_redirects=True,
            timeout=self.TIMEOUT_S,
        ) as client:
            for page_num in range(1, _MAX_PAGES + 1):
                data = await self._fetch_page(client, build_id, page_num)
                results = _extract_results(data)
                if not results:
                    break
                pages.append(data)
                total = _total_results(data)
                if total is not None:
                    if page_num * _PAGE_SIZE >= total:
                        break
                elif len(results) < _PAGE_SIZE:
                    break

        self.logger.info("Fetched %d pages from Coles /on-special", len(pages))
        return pages

    async def _fetch_page(
        self, client: httpx.AsyncClient, build_id: str, page: int
    ) -> dict:
        url = self._data_url(build_id, "on-special.json")
        try:
            resp = await client.get(url, params={"page": page})
        except httpx.HTTPError as exc:
            raise ScraperError(f"Coles specials page fetch failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise ScraperError(f"Coles specials returned {resp.status_code}")
        if resp.status_code == 404:
            # buildId rotated — non-retryable at this layer; caller should re-bootstrap.
            raise ScraperError("Coles specials 404 — buildId may have rotated")
        if resp.status_code == 429:
            retry_after = _retry_after(resp.headers.get("Retry-After"))
            raise ScraperError(f"Coles specials rate-limited (429); retry after {retry_after}s")
        try:
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ScraperError(f"Coles specials page parse failed: {exc}") from exc

    # ── parse_flyer ───────────────────────────────────────────────────────────

    async def parse_flyer(self, flyer_data: list[dict]) -> list[ScrapedProduct]:
        """Flatten paged JSON responses into ``ScrapedProduct`` rows."""
        products: list[ScrapedProduct] = []
        today = date.today()
        valid_to = today + timedelta(days=7)

        for page in flyer_data:
            for item in _extract_results(page):
                product = _parse_item(item, today, valid_to)
                if product is not None:
                    products.append(product)

        self.logger.info("Parsed %d specials from Coles", len(products))
        return products


# ── Response helpers ──────────────────────────────────────────────────────────

def _extract_results(page: dict) -> list[dict]:
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
    props = page.get("pageProps") if isinstance(page, dict) else None
    search = props.get("searchResults") if isinstance(props, dict) else None
    if isinstance(search, dict):
        n = search.get("noOfResults")
        if isinstance(n, int):
            return n
    return None


def _parse_item(item: dict, today: date, valid_to: date) -> Optional[ScrapedProduct]:
    """Map one Coles product record to a ScrapedProduct. Skips ad/non-products."""
    if not isinstance(item, dict):
        return None
    if item.get("_type") and item.get("_type") != "PRODUCT":
        return None

    name = item.get("name") or item.get("description")
    pricing = item.get("pricing") or {}
    now = pricing.get("now")
    if not name or now is None:
        return None

    price_cents = _to_cents(now)
    if price_cents is None or price_cents <= 0:
        return None

    was = pricing.get("was")
    was_cents = _to_cents(was) if was else None
    if was_cents is not None and was_cents <= price_cents:
        was_cents = None

    image_uris = item.get("imageUris") or []
    image_url = None
    if isinstance(image_uris, list) and image_uris:
        first = image_uris[0]
        if isinstance(first, dict):
            raw_uri = first.get("uri") or first.get("altText")
            image_url = _absolute_image_url(raw_uri)

    category: Optional[str] = None
    item_cats = item.get("categories") or []
    if isinstance(item_cats, list) and item_cats:
        first_cat = item_cats[0]
        if isinstance(first_cat, dict):
            category = first_cat.get("name")

    return ScrapedProduct(
        product_name=str(name),
        price_cents=price_cents,
        was_price_cents=was_cents,
        gtin=None,
        valid_from=today,
        valid_to=valid_to,
        image_url=image_url,
        category=category,
        brand=item.get("brand"),
    )


def _absolute_image_url(uri: Optional[str]) -> Optional[str]:
    if not uri:
        return None
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri
    return f"{_IMAGE_CDN_BASE}{uri if uri.startswith('/') else '/' + uri}"


def _to_cents(value: Any) -> Optional[int]:
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _retry_after(value: Optional[str]) -> float:
    if not value:
        return 60.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 60.0

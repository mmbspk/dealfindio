"""
Open Food Facts (OFF) image enrichment.

Products created via fuzzy name-match (Coles, ALDI, IGA scrapers) have no GTIN
and therefore no image — the GTIN-only enrichment path in ``product_dedup`` never
fires for them. This module fills that gap: given a product name, it searches
OFF's product database, downloads the best matching image, and hands the bytes
back to the caller (the Celery task) to re-host on our own object store.

We deliberately **download and re-host** rather than hotlink OFF's CDN so that:
  - product images never break if OFF's CDN changes URLs or rate-limits us,
  - we stay within OFF's reuse guidelines (attribution stored in image_source),
  - we can resize/optimize later without touching the DB.

``OFTRateLimiter`` keeps us a polite guest: a randomized 2–5s gap between calls
(human-like, single-threaded within the task) and it honors ``Retry-After`` on
429s. This is intentionally simple — one task, one client, no Redis needed.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Optional

import httpx
from rapidfuzz import fuzz

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# OFF search API. v2 returns structured JSON and lets us request only the
# fields we need, keeping responses small.
_OFF_SEARCH_URL = "https://world.openfoodfacts.org/api/v2/search"
# selected_images contains OFF's curated best image per product/language,
# preferred over the raw image_front_url (which may be an uncurated upload).
_OFF_SEARCH_FIELDS = "code,product_name,brands,image_url,image_front_url,selected_images"

# Accept only real raster images we know how to store.
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
# Guard against a pathological download (OFF images are typically < 1 MB).
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
# Minimum token-sort ratio (0–100 scale) for accepting an OFF product name match.
# Rejects obviously wrong products (e.g. "cereal" matching "Greek yoghurt").
# Data-derived: at 60 false-positives drop sharply while recall stays ~90%.
_MIN_NAME_SIMILARITY = 60.0


@dataclass
class EnrichedImage:
    """Result of a successful enrichment lookup + download."""

    image_bytes: bytes
    content_type: str
    source_url: str  # the OFF CDN URL the bytes came from (for provenance)
    off_code: Optional[str]  # OFF product barcode, if the match had one
    match_score: float  # rapidfuzz token_sort_ratio score (0–100) for observability


class OFTRateLimiter:
    """Randomized inter-request delay with adaptive backoff on 429.

    Single-threaded within a task: ``wait()`` sleeps for a jittered gap since
    the previous request, plus any penalty imposed by a prior ``on_rate_limited``.
    """

    def __init__(self, min_delay_s: float, max_delay_s: float) -> None:
        self._min = min_delay_s
        self._max = max(max_delay_s, min_delay_s)
        self._loop = asyncio.get_event_loop
        self._last_request_at: Optional[float] = None
        self._penalty_until: float = 0.0

    async def wait(self) -> None:
        now = self._loop().time()
        # Respect any backoff penalty from a recent 429 first.
        if now < self._penalty_until:
            await asyncio.sleep(self._penalty_until - now)
            now = self._loop().time()

        target_gap = random.uniform(self._min, self._max)
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            remaining = target_gap - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_request_at = self._loop().time()

    def on_rate_limited(self, retry_after_s: float = 60.0) -> None:
        """Impose a backoff window after a 429 so the next wait() honors it."""
        self._penalty_until = self._loop().time() + max(retry_after_s, 1.0)


class OFTImageEnricher:
    """Searches OFF by product name and downloads the best image match."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._rate_limiter = OFTRateLimiter(
            self._settings.OFT_SEARCH_MIN_DELAY_S,
            self._settings.OFT_SEARCH_MAX_DELAY_S,
        )
        self._timeout = self._settings.OFT_SEARCH_TIMEOUT_S
        self._user_agent = self._settings.OPEN_FOOD_FACTS_USER_AGENT

    async def enrich(self, product_name: str, client: httpx.AsyncClient) -> Optional[EnrichedImage]:
        """Find and download an image for ``product_name``.

        Returns ``None`` (never raises) when no image is found, a network
        error occurs, or no OFF result clears the name-similarity threshold —
        enrichment is best-effort and must not fail the caller.
        """
        if not product_name or not product_name.strip():
            return None

        result = await self._search(product_name.strip(), client)
        if not result:
            return None
        match, score = result

        image_url = _best_image_url(match)
        if not image_url:
            logger.debug("OFF match for %r has no image", product_name)
            return None

        downloaded = await self._download_image(image_url, client)
        if not downloaded:
            return None

        image_bytes, content_type = downloaded
        return EnrichedImage(
            image_bytes=image_bytes,
            content_type=content_type,
            source_url=image_url,
            off_code=match.get("code"),
            match_score=score,
        )

    async def _search(
        self, name: str, client: httpx.AsyncClient
    ) -> Optional[tuple[dict, float]]:
        """Return the best OFF product for ``name`` that has an image and
        clears the name-similarity threshold.

        Scores all candidates and returns the highest-scoring one (not just
        the first) so that a well-named product ranked below a generic brand
        in OFF's sort order still wins. Returns ``(product_dict, score)``
        or ``None``.
        """
        await self._rate_limiter.wait()
        params = {
            "search_terms": name,
            "fields": _OFF_SEARCH_FIELDS,
            "page_size": 10,
            "sort_by": "unique_scans_n",  # prefer well-known products
        }
        try:
            resp = await client.get(
                _OFF_SEARCH_URL,
                params=params,
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
            )
            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                self._rate_limiter.on_rate_limited(retry_after)
                logger.warning("OFF rate-limited; backing off %.0fs", retry_after)
                return None
            resp.raise_for_status()
            products = resp.json().get("products", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OFF search failed for %r: %s", name, exc)
            return None

        best: Optional[tuple[dict, float]] = None
        for product in products:
            if not _best_image_url(product):
                continue
            off_name = product.get("product_name") or ""
            score = fuzz.token_sort_ratio(name.lower(), off_name.lower())
            if score < _MIN_NAME_SIMILARITY:
                logger.debug(
                    "OFF match rejected (score=%.0f < %.0f): %r vs %r",
                    score,
                    _MIN_NAME_SIMILARITY,
                    name,
                    off_name,
                )
                continue
            if best is None or score > best[1]:
                best = (product, score)
        return best

    async def _download_image(
        self, url: str, client: httpx.AsyncClient
    ) -> Optional[tuple[bytes, str]]:
        """Download an image URL, validating type and size. Best-effort."""
        await self._rate_limiter.wait()
        try:
            resp = await client.get(
                url,
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
                follow_redirects=True,
            )
            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                self._rate_limiter.on_rate_limited(retry_after)
                return None
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("OFF image download failed for %s: %s", url, exc)
            return None

        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type not in _ALLOWED_IMAGE_TYPES:
            logger.debug("Skipping non-image content-type %r from %s", content_type, url)
            return None

        data = resp.content
        if not data or len(data) > _MAX_IMAGE_BYTES:
            logger.debug("Skipping image of %d bytes from %s", len(data), url)
            return None
        # Normalize the odd "image/jpg" to canonical "image/jpeg".
        if content_type == "image/jpg":
            content_type = "image/jpeg"
        return data, content_type


def _parse_retry_after(value: Optional[str]) -> float:
    """Parse a Retry-After header (seconds form). Default 60s if absent/odd."""
    if not value:
        return 60.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 60.0


def _best_image_url(product: dict) -> Optional[str]:
    """Extract the highest-quality available image URL from an OFF product dict.

    Preference order:
    1. selected_images.front.display — OFF's curated, moderated front image
    2. image_front_url — raw front-camera upload (may be lower quality)
    3. image_url — any image on the product
    """
    selected = product.get("selected_images") or {}
    front = selected.get("front") or {}
    display = front.get("display") or {}
    if display:
        # Prefer English; fall back to whatever language key exists first.
        url = display.get("en") or next(iter(display.values()), None)
        if url:
            return url
    if product.get("image_front_url"):
        return product["image_front_url"]
    return product.get("image_url")

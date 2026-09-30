"""
Authenticated full-catalog scraper framework (Feature B).

This is a parallel system to the weekly-specials flyer scrapers. It crawls a
store's **entire** product catalog using a logged-in account, spread politely
over hours, then supports cheap **delta** re-crawls that skip categories whose
contents haven't changed.

Everything store-specific lives in a concrete ``CatalogScraper`` subclass (one
per store). The base class owns the parts every store shares:
  - the crawl lifecycle (``run_full`` / ``run_delta``),
  - human-like pacing (``HumanLikeRateLimiter``),
  - Redis-backed auth persistence with auto re-auth on 401.

Adding a new store is therefore "implement four abstract methods + register the
class" — no changes to the task, routes, config plumbing, or this file.

Security: credentials are read from settings (.env) and passed to the scraper
at construction. Only short-lived auth material (tokens/cookies) is cached in
Redis, never the password.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import redis as redis_lib
from sqlalchemy.orm import Session

from scrapers.base import ScrapedProduct, ScraperError

logger = logging.getLogger(__name__)


# ── Shared HTTP utilities ────────────────────────────────────────────────────
# One copy here; every catalog scraper imports from catalog_base instead of
# redefining its own.

SCRAPER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# HTML headers — use for homepage bootstrap requests that need to clear
# bot-protection (Imperva, Akamai, Cloudflare). The Sec-Fetch-* suite and
# Upgrade-Insecure-Requests signal a real browser navigation.
HTML_HEADERS: dict[str, str] = {
    "User-Agent": SCRAPER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AU,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# JSON headers — use for XHR/fetch API calls from within a page session.
JSON_HEADERS: dict[str, str] = {
    "User-Agent": SCRAPER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-AU,en-GB;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def get_scraper_proxy(session_id: Optional[str] = None) -> Optional[str]:
    """Return the configured proxy URL for Coles/Woolworths scrapers, or None.

    Checks the DB config store first (set via Admin UI), then falls back to
    the SCRAPER_PROXY_URL environment variable / .env setting.
    Pass the return value directly to ``httpx.AsyncClient(proxies=...)``.
    Returns None when no proxy is configured.

    If ``session_id`` is provided, it is appended as ``sess=<id>`` to the proxy
    URL so DataImpulse pins every connection in this run to the same exit IP —
    including re-auth connections after a bot-block.  Without this, each new
    TLS connection may get a different IP even when ``sessttl`` is set.
    """
    raw: Optional[str] = None
    try:
        from app.deps import _SessionLocal
        from app.services.config_store import get_config

        if _SessionLocal:
            session = _SessionLocal()
            try:
                raw = get_config("SCRAPER_PROXY_URL", session) or None
            finally:
                session.close()
    except Exception:
        pass
    if not raw:
        try:
            from app.config import get_settings

            raw = get_settings().SCRAPER_PROXY_URL or None
        except Exception:
            return None
    if not raw:
        return None
    if session_id:
        sep = "&" if "?" in raw else "?"
        raw = f"{raw}{sep}sess={session_id}"
    return raw


def to_cents(value: Any) -> Optional[int]:
    """Convert a price value (float, str, int) to integer cents. Returns None on failure."""
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def parse_retry_after(value: Optional[str], default: float = 60.0) -> float:
    """Parse a Retry-After header value into seconds. Falls back to ``default``."""
    if not value:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class CatalogProduct(ScrapedProduct):
    """A product from a full-catalog crawl.

    Extends ``ScrapedProduct`` with catalog-only fields. ``price_cents`` is the
    current shelf price; ``was_price_cents`` is set only when on promotion.
    """

    external_id: Optional[str] = None  # the store's own SKU / stockcode
    url: Optional[str] = None  # product detail page URL
    in_stock: bool = True
    package_size: Optional[str] = None  # raw size string e.g. "2L", "375mL 24 Pack"
    unit_price: Optional[float] = None  # comparable unit price e.g. 1.50 (per 100g/100ml/ea)
    unit_price_label: Optional[str] = None  # unit label for unit_price e.g. "100g", "100ml", "ea"


@dataclass
class CategoryNode:
    """A node in a store's category tree."""

    id: str
    name: str
    url: str = ""
    parent_id: Optional[str] = None
    children: list["CategoryNode"] = field(default_factory=list)


@dataclass
class CatalogStoreConfig:
    """Per-store crawl configuration, assembled from settings + credentials."""

    chain: str
    email: Optional[str] = None
    password: Optional[str] = None
    min_delay_s: float = 3.0
    max_delay_s: float = 8.0
    burst_limit: int = 5
    page_size: int = 48
    max_pages_per_category: int = 200
    enabled: bool = False
    # How long cached auth material lives in Redis (cookie stores). JWT stores
    # override this with the token's own expiry.
    auth_redis_ttl_s: int = 82800  # 23h


# ── Human-like pacing ────────────────────────────────────────────────────────


class HumanLikeRateLimiter:
    """Paces requests to look like a person browsing, not a bot hammering.

    Mechanisms:
      - **Token bucket**: allows a small burst of quick requests, then settles
        into a randomized per-request delay.
      - **Jittered delay**: each gap is ``uniform(min, max)`` seconds, measured
        from the previous request (elapsed time is subtracted).
      - **Think-time pauses**: every ``think_every`` requests, an extra longer
        pause, as if the user stopped to read.
      - **Adaptive backoff**: a 429 imposes a penalty window honored by the
        next ``wait()``.
    """

    def __init__(
        self,
        min_delay_s: float,
        max_delay_s: float,
        burst_limit: int = 5,
        think_every: int = 10,
        think_min_s: float = 5.0,
        think_max_s: float = 15.0,
    ) -> None:
        self._min = min_delay_s
        self._max = max(max_delay_s, min_delay_s)
        self._burst_limit = max(burst_limit, 0)
        self._think_every = max(think_every, 1)
        self._think_min = think_min_s
        self._think_max = think_max_s

        self._tokens = self._burst_limit
        self._request_count = 0
        self._last_request_at: Optional[float] = None
        self._penalty_until: float = 0.0

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    async def wait(self) -> None:
        """Sleep the appropriate amount before issuing the next request."""
        now = self._now()

        # Honor any backoff penalty from a recent 429 first.
        if now < self._penalty_until:
            await asyncio.sleep(self._penalty_until - now)
            now = self._now()

        # Burst allowance: the first `burst_limit` requests fire back-to-back.
        if self._tokens > 0:
            self._tokens -= 1
        else:
            target_gap = random.uniform(self._min, self._max)
            if self._last_request_at is not None:
                remaining = target_gap - (now - self._last_request_at)
                if remaining > 0:
                    await asyncio.sleep(remaining)

        self._request_count += 1
        # Periodic longer "think time" pause.
        if self._request_count % self._think_every == 0:
            await asyncio.sleep(random.uniform(self._think_min, self._think_max))

        self._last_request_at = self._now()

    def on_rate_limited(self, retry_after_s: float = 60.0) -> None:
        """Impose a backoff window after a 429 and drain the burst bucket."""
        self._penalty_until = self._now() + max(retry_after_s, 1.0)
        self._tokens = 0


# ── Scraper ABC ──────────────────────────────────────────────────────────────


class CatalogScraper(ABC):
    """Base class for authenticated full-catalog scrapers.

    Concrete subclasses implement ``authenticate``, ``discover_categories``,
    ``crawl_category`` and ``parse_products``. The base class drives the crawl,
    paces it, and manages cached auth.
    """

    #: Redis key prefix for cached auth material.
    AUTH_KEY_PREFIX = "catalog_auth"
    #: Redis key prefix for per-category response fingerprints (delta mode).
    HASH_KEY_PREFIX = "catalog_cathash"
    #: Redis key prefix for per-category 24h crawl-recency stamps.
    CRAWLED_KEY_PREFIX = "catalog_catcrawled"
    #: How long the recency stamp lives (25h — slightly longer than the skip window).
    CRAWLED_TTL_S: int = 90000  # 25h

    def __init__(
        self,
        chain: str,
        config: CatalogStoreConfig,
        db_session: Optional[Session] = None,
        redis_client: Optional[redis_lib.Redis] = None,
    ) -> None:
        self.chain = chain
        self.config = config
        self.db_session = db_session
        self.redis_client = redis_client
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        self.rate_limiter = HumanLikeRateLimiter(
            min_delay_s=config.min_delay_s,
            max_delay_s=config.max_delay_s,
            burst_limit=config.burst_limit,
        )

    # ── Abstract: implemented per store ──────────────────────────────────────

    @abstractmethod
    async def authenticate(self) -> dict:
        """Log in and return auth material.

        Returns a dict with at minimum a ``"headers"`` and/or ``"cookies"`` key
        plus an optional ``"ttl_s"`` overriding the default cache TTL (used for
        JWTs, whose expiry is known). Raise ``ScraperError`` on failure.
        """

    @abstractmethod
    async def discover_categories(self) -> list[CategoryNode]:
        """Return the flat list of leaf categories to crawl."""

    @abstractmethod
    async def crawl_category(self, category: CategoryNode, auth: dict) -> Any:
        """Fetch all raw pages for one category. Return raw response data."""

    @abstractmethod
    async def parse_products(self, raw: Any, category: CategoryNode) -> list[CatalogProduct]:
        """Parse a category's raw response into products."""

    async def crawl_specials(self, auth: dict, product_sink=None, resume_page: int = 1) -> Any:
        """Fetch all on-special products from the store's promotions endpoint.

        Override in each concrete scraper. Raise ``NotImplementedError`` is the
        default so that calling ``run_specials`` on a scraper that hasn't
        implemented this method gives a clear error immediately.

        ``product_sink`` is the same async callable used by the category loop —
        stores that paginate (e.g. Coles) should call it per page so products are
        ingested incrementally rather than buffered until the end.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} has no specials mode — implement crawl_specials()"
        )

    async def parse_specials(self, raw: Any) -> list[CatalogProduct]:
        """Parse the raw response from ``crawl_specials`` into products.

        Default: delegates to ``parse_products`` with a synthetic "Specials" category
        node. Override when the specials endpoint returns a different shape than
        the per-category endpoint.
        """
        specials_cat = CategoryNode(id="specials", name="Specials")
        return await self.parse_products(raw, specials_cat)

    # ── Auth persistence (Redis-backed) ──────────────────────────────────────

    def _auth_key(self) -> str:
        return f"{self.AUTH_KEY_PREFIX}:{self.chain}"

    async def _get_or_refresh_auth(self) -> dict:
        """Return cached auth if present, else authenticate and cache it."""
        if self.redis_client is not None:
            try:
                cached = self.redis_client.get(self._auth_key())
                if cached:
                    raw = cached.decode() if isinstance(cached, bytes) else cached
                    return json.loads(raw)
            except Exception as exc:  # cache is best-effort
                self.logger.warning("Auth cache read failed: %s", exc)

        auth = await self.authenticate()
        ttl = int(auth.get("ttl_s") or self.config.auth_redis_ttl_s)
        if self.redis_client is not None and ttl > 0:
            try:
                self.redis_client.setex(self._auth_key(), ttl, json.dumps(auth))
            except Exception as exc:
                self.logger.warning("Auth cache write failed: %s", exc)
        return auth

    async def _invalidate_auth(self) -> None:
        """Drop cached auth so the next call re-authenticates (e.g. after 401)."""
        if self.redis_client is not None:
            try:
                self.redis_client.delete(self._auth_key())
            except Exception as exc:
                self.logger.warning("Auth cache delete failed: %s", exc)

    # ── Delta fingerprints ───────────────────────────────────────────────────

    def _hash_key(self, category_id: str) -> str:
        return f"{self.HASH_KEY_PREFIX}:{self.chain}:{category_id}"

    @staticmethod
    def _fingerprint(raw: Any) -> str:
        """Stable hash of a category's raw response, for change detection."""
        try:
            payload = json.dumps(raw, sort_keys=True, default=str).encode()
        except (TypeError, ValueError):
            payload = repr(raw).encode()
        return hashlib.sha256(payload).hexdigest()

    def _category_unchanged(self, category_id: str, raw: Any) -> bool:
        """True if this category's fingerprint matches the last crawl's."""
        if self.redis_client is None:
            return False
        try:
            prev = self.redis_client.get(self._hash_key(category_id))
        except Exception:
            return False
        current = self._fingerprint(raw)
        if prev is not None:
            prev_s = prev.decode() if isinstance(prev, bytes) else prev
            if prev_s == current:
                return True
        # Store the new fingerprint for next time (best-effort).
        try:
            self.redis_client.setex(
                self._hash_key(category_id), self.config.auth_redis_ttl_s, current
            )
        except Exception:
            pass
        return False

    # ── 24-hour crawl recency ────────────────────────────────────────────────

    def _crawled_key(self, category_id: str) -> str:
        return f"{self.CRAWLED_KEY_PREFIX}:{self.chain}:{category_id}"

    def _category_recently_crawled(self, category_id: str, max_age_s: int = 86400) -> bool:
        """Return True if this category was successfully crawled within max_age_s seconds."""
        if self.redis_client is None:
            return False
        try:
            val = self.redis_client.get(self._crawled_key(category_id))
        except Exception:
            return False
        if val is None:
            return False
        try:
            import time

            stamped_at = float(val.decode() if isinstance(val, bytes) else val)
            return (time.time() - stamped_at) < max_age_s
        except (ValueError, TypeError):
            return False

    def _mark_category_crawled(self, category_id: str) -> None:
        """Store the current Unix timestamp for this category (TTL = CRAWLED_TTL_S)."""
        if self.redis_client is None:
            return
        try:
            import time

            self.redis_client.setex(
                self._crawled_key(category_id),
                self.CRAWLED_TTL_S,
                str(time.time()),
            )
        except Exception as exc:
            self.logger.warning("Failed to mark category %s crawled: %s", category_id, exc)

    # ── Crawl lifecycle ──────────────────────────────────────────────────────

    async def run_full(self, product_sink=None, on_categories_discovered=None) -> dict:
        """Crawl every category. ``mode='full'``.

        Args:
            product_sink: optional async callable ``(list[CatalogProduct],
                CategoryNode) -> None`` invoked per category so the caller can
                ingest incrementally instead of buffering the whole catalog.

        Returns a summary dict with category/product counts and any errors.
        """
        return await self._run(
            mode="full",
            product_sink=product_sink,
            on_categories_discovered=on_categories_discovered,
        )

    async def run_delta(self, product_sink=None, on_categories_discovered=None) -> dict:
        """Crawl only categories whose response changed since last time."""
        return await self._run(
            mode="delta",
            product_sink=product_sink,
            on_categories_discovered=on_categories_discovered,
        )

    async def run_specials(self, product_sink=None, resume_page: int = 1) -> dict:
        """Fetch only on-special products via the store's promotions endpoint.

        Skips category discovery entirely — calls ``crawl_specials`` which hits
        the store's dedicated specials/promotions endpoint directly. Fast: a
        typical specials run takes <2 min vs hours for a full crawl.

        ``resume_page`` skips pages already fetched in a same-day run.
        """
        return await self._run(mode="specials", product_sink=product_sink, resume_page=resume_page)

    async def _run(self, mode: str, product_sink=None, on_categories_discovered=None, resume_page: int = 1) -> dict:
        errors: list[str] = []
        categories_found = 0
        categories_crawled = 0
        categories_skipped = 0
        products_found = 0

        auth = await self._get_or_refresh_auth()

        # ── Specials mode: bypass category loop entirely ──────────────────────
        if mode == "specials":
            try:
                raw = await self._specials_with_reauth(auth, product_sink=product_sink, resume_page=resume_page)
            except Exception as exc:
                self.logger.exception("Specials fetch failed for %s", self.chain)
                raise ScraperError(f"Specials fetch failed: {exc}") from exc

            # If crawl_specials already flushed via product_sink (e.g. Coles),
            # raw will contain only a products_found count; otherwise parse+sink now.
            already_sunk = isinstance(raw, dict) and raw.get("_sunk", False)
            products_found = raw.get("products_found", 0) if already_sunk else 0

            if not already_sunk:
                try:
                    products = await self.parse_specials(raw)
                except Exception as exc:
                    self.logger.exception("Specials parse failed for %s", self.chain)
                    raise ScraperError(f"Specials parse failed: {exc}") from exc

                products_found = len(products)
                specials_cat = CategoryNode(id="specials", name="Specials")

                if product_sink is not None and products:
                    try:
                        await product_sink(products, specials_cat)
                    except Exception as exc:
                        msg = f"Specials ingest failed: {exc}"
                        self.logger.warning(msg)
                        errors.append(msg)

            return {
                "chain": self.chain,
                "mode": "specials",
                "categories_found": 1,
                "categories_crawled": 0 if errors else 1,
                "categories_skipped": 0,
                "products_found": products_found,
                "errors": errors,
                "status": "success" if not errors else "partial",
            }

        # ── Full / delta mode: category loop ─────────────────────────────────
        try:
            categories = await self.discover_categories()
        except AuthExpiredError:
            self.logger.info(
                "Auth expired during category discovery for %s; re-authenticating", self.chain
            )
            await self._invalidate_auth()
            auth = await self._get_or_refresh_auth()
            try:
                categories = await self.discover_categories()
            except Exception as exc:
                self.logger.exception("Category discovery failed after re-auth for %s", self.chain)
                raise ScraperError(f"Category discovery failed: {exc}") from exc
        except Exception as exc:
            self.logger.exception("Category discovery failed for %s", self.chain)
            raise ScraperError(f"Category discovery failed: {exc}") from exc

        categories_found = len(categories)
        if on_categories_discovered is not None:
            try:
                on_categories_discovered(categories_found)
            except Exception:
                pass  # progress callback errors must not abort the crawl

        # Shuffle so repeated runs don't hit categories in the same order.
        order = list(categories)
        random.shuffle(order)

        for category in order:
            # Skip categories already successfully crawled within the last 24h.
            # This applies to both full and delta modes so repeated runs in a day
            # don't re-crawl categories that are already up to date.
            if self._category_recently_crawled(category.id):
                self.logger.debug("Skipping %r — crawled within 24h", category.name)
                categories_skipped += 1
                continue

            try:
                raw = await self._crawl_with_reauth(category, auth)
            except Exception as exc:
                msg = f"Category {category.name!r} failed: {exc}"
                self.logger.warning(msg)
                errors.append(msg)
                continue

            if mode == "delta" and self._category_unchanged(category.id, raw):
                categories_skipped += 1
                continue
            if mode == "full":
                # Refresh the fingerprint on full crawls too, so a later delta
                # has a baseline to compare against.
                self._category_unchanged(category.id, raw)

            try:
                products = await self.parse_products(raw, category)
            except Exception as exc:
                msg = f"Parse failed for {category.name!r}: {exc}"
                self.logger.warning(msg)
                errors.append(msg)
                continue

            products_found += len(products)
            categories_crawled += 1

            if product_sink is not None and products:
                try:
                    await product_sink(products, category)
                except Exception as exc:
                    msg = f"Ingest failed for {category.name!r}: {exc}"
                    self.logger.warning(msg)
                    errors.append(msg)
                    continue  # don't mark crawled if ingest failed

            # Mark the category as successfully crawled so reruns within 24h skip it.
            self._mark_category_crawled(category.id)

        return {
            "chain": self.chain,
            "mode": mode,
            "categories_found": categories_found,
            "categories_crawled": categories_crawled,
            "categories_skipped": categories_skipped,
            "products_found": products_found,
            "errors": errors,
            "status": "success" if not errors else "partial",
        }

    async def _crawl_with_reauth(self, category: CategoryNode, auth: dict) -> Any:
        """Crawl a category, re-authenticating once on an auth failure (401)."""
        try:
            return await self.crawl_category(category, auth)
        except AuthExpiredError:
            self.logger.info("Auth expired for %s; re-authenticating", self.chain)
            await self._invalidate_auth()
            auth = await self._get_or_refresh_auth()
            return await self.crawl_category(category, auth)

    async def _specials_with_reauth(self, auth: dict, product_sink=None, resume_page: int = 1) -> Any:
        """Call crawl_specials, re-authenticating once on an auth failure.

        If the second attempt also raises AuthExpiredError the site is bot-blocking
        the session entirely (not just the token). Raise ScraperError to surface a
        clean failure rather than letting AuthExpiredError propagate unhandled.
        """
        try:
            return await self.crawl_specials(auth, product_sink=product_sink, resume_page=resume_page)
        except AuthExpiredError:
            self.logger.info("Auth expired fetching specials for %s; re-authenticating", self.chain)
            await self._invalidate_auth()
            auth = await self._get_or_refresh_auth()
            try:
                return await self.crawl_specials(auth, product_sink=product_sink, resume_page=resume_page)
            except AuthExpiredError as exc:
                raise ScraperError(
                    f"{self.chain} specials bot-blocked after re-auth — site is blocking this session"
                ) from exc


class AuthExpiredError(ScraperError):
    """Raised by ``crawl_category`` on a 401 so the base class re-authenticates."""

"""
Product deduplication service with GTIN resolution and fuzzy matching.

Implements complete product deduplication pipeline:
- GTIN resolution via Open Food Facts API
- Fuzzy name matching across retailers
- Redis-backed deduplication cache to prevent duplicate ingestion
- Complete ingest pipeline for scraped prices

This module handles the complex logic for matching scraped products against
existing products in the database, resolving duplicates, and ensuring price
reports aren't ingested multiple times within a catalogue cycle.
"""

import logging
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

import httpx
from redis import Redis
from rapidfuzz.process import extractOne
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.product import Product
from app.models.price_report import PriceReport
from app.models.store import Store

logger = logging.getLogger(__name__)


# Brand similarity below this (0-100) counts as a *conflict* — two products with
# clearly different brands (e.g. "Kirks" vs "Fanta") are never the same item even
# when their names collide (both read "orange soft drink"). Used to hard-veto a
# match. A single missing brand is not a conflict (we can't be sure), so the
# predicate only fires when BOTH brands are present.
BRAND_CONFLICT_MAX = 40


def brands_conflict(brand_a: Optional[str], brand_b: Optional[str]) -> bool:
    """Return True when two brands are both present and clearly different.

    Returns False if either brand is missing — a single unknown brand is not
    enough evidence to block a match, only two known-and-different brands are.

    Example:
        >>> brands_conflict("Kirks", "Fanta")
        True
        >>> brands_conflict("Coca-Cola", "Coca Cola")
        False
        >>> brands_conflict(None, "Fanta")
        False
    """
    if not brand_a or not brand_b:
        return False
    from rapidfuzz.fuzz import ratio as fuzz_ratio

    matcher = get_fuzzy_matcher()
    score = fuzz_ratio(
        matcher.normalize_name(brand_a),
        matcher.normalize_name(brand_b),
    )
    return score < BRAND_CONFLICT_MAX


class ScraperError(Exception):
    """Base exception for scraper-related errors."""

    pass


class DuplicateError(ScraperError):
    """Raised when a duplicate price report is detected."""

    pass


class GTINResolutionError(ScraperError):
    """Raised when GTIN resolution fails."""

    pass


class GTINResolver:
    """
    Resolve products by GTIN with Open Food Facts fallback.

    Attempts to match products by their Global Trade Item Number (GTIN/EAN-13),
    enriches from Open Food Facts API if not found locally, and creates new
    product records with enriched metadata.
    """

    OPEN_FOOD_FACTS_URL = "https://world.openfoodfacts.org/product"
    TIMEOUT = 10.0
    USER_AGENT = "dealfindio/0.1.0"

    async def lookup_gtin(self, gtin: str, session: AsyncSession) -> Optional[Product]:
        """
        Lookup product by GTIN in database.

        Queries the products table for exact GTIN match.

        Args:
            gtin: Global Trade Item Number (EAN-13, 14-digit left-padded)
            session: AsyncSession for database operations

        Returns:
            Product if found, None otherwise

        Raises:
            Exception: On database errors
        """
        try:
            # Normalize GTIN to 14 digits (left-pad with zeros)
            normalized_gtin = gtin.zfill(14)

            stmt = select(Product).where(Product.gtin == normalized_gtin)
            result = await session.execute(stmt)
            product = result.scalar_one_or_none()

            if product:
                logger.debug(f"Found product by GTIN {normalized_gtin}: {product.name}")

            return product

        except Exception as e:
            logger.error(f"Database error during GTIN lookup: {e}")
            raise

    async def enrich_from_open_food_facts(self, gtin: str) -> Optional[dict]:
        """
        Fetch product data from Open Food Facts API.

        Queries the Open Food Facts database for product metadata enrichment.
        Extracts product name, brands, image URL, and categories.

        Args:
            gtin: Global Trade Item Number to lookup

        Returns:
            Dict with keys: product_name, brands, image_url, categories
            Returns None if product not found or API error

        Raises:
            Exception: Caught and logged, returns None for resilience
        """
        try:
            normalized_gtin = gtin.zfill(14)
            url = f"{self.OPEN_FOOD_FACTS_URL}/{normalized_gtin}.json"

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    timeout=self.TIMEOUT,
                    headers={"User-Agent": self.USER_AGENT},
                )

                if response.status_code == 404:
                    logger.debug(f"Product not found in Open Food Facts: {gtin}")
                    return None

                response.raise_for_status()
                data = response.json()

                # Extract product field
                product = data.get("product", {})

                # Return enriched data
                enriched = {
                    "product_name": product.get("product_name") or product.get("generic_name"),
                    "brands": product.get("brands"),
                    "image_url": product.get("image_url") or product.get("front_url"),
                    "categories": product.get("categories"),
                }

                logger.debug(f"Enriched product from OFT: {enriched['product_name']}")
                return enriched

        except httpx.TimeoutException:
            logger.warning(f"Open Food Facts API timeout for GTIN {gtin}")
            return None
        except httpx.HTTPError as e:
            logger.warning(f"Open Food Facts API error for GTIN {gtin}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error enriching product from Open Food Facts: {e}")
            return None

    async def create_product_from_gtin(self, gtin: str, session: AsyncSession) -> Product:
        """
        Create new product record from GTIN with Open Food Facts enrichment.

        Attempts to enrich product data from Open Food Facts, then creates
        a new Product record in the database.

        Args:
            gtin: Global Trade Item Number
            session: AsyncSession for database operations

        Returns:
            Created Product record

        Raises:
            GTINResolutionError: If product creation fails
            Exception: On database errors
        """
        try:
            normalized_gtin = gtin.zfill(14)

            # Attempt enrichment from Open Food Facts
            enriched_data = await self.enrich_from_open_food_facts(normalized_gtin)

            # Create product with enriched data or defaults
            product_name = None
            if enriched_data:
                product_name = enriched_data.get("product_name")

            if not product_name:
                product_name = f"Product {normalized_gtin}"

            product = Product(
                gtin=normalized_gtin,
                name=product_name,
                brand=enriched_data.get("brands") if enriched_data else None,
                image_url=enriched_data.get("image_url") if enriched_data else None,
            )

            session.add(product)
            await session.flush()  # Flush to get the ID

            logger.info(f"Created new product from GTIN {normalized_gtin}: {product.name}")
            return product

        except Exception as e:
            logger.error(f"Failed to create product from GTIN {gtin}: {e}")
            raise GTINResolutionError(f"Failed to create product from GTIN: {str(e)}")


class FuzzyMatcher:
    """
    Fuzzy match product names across retailers.

    Uses string similarity algorithms (Levenshtein distance via rapidfuzz)
    to find products with similar names in the database. Useful for matching
    products when GTIN is not available.
    """

    MATCH_THRESHOLD = 85

    def normalize_name(self, name: str) -> str:
        """
        Normalize product name for fuzzy matching.

        Converts to lowercase, removes special characters, and strips pack size
        suffixes to improve matching accuracy.

        Args:
            name: Product name to normalize

        Returns:
            Normalized product name

        Example:
            >>> matcher.normalize_name("Coca-Cola 2L Bottle")
            "cocacola bottle"
        """
        if not name:
            return ""

        # Convert to lowercase
        normalized = name.lower()

        # Remove special characters but keep spaces
        import re
        normalized = re.sub(r"[^a-z0-9\s]", "", normalized)

        # Remove pack size suffixes (e.g., "2l", "500g", "1kg")
        normalized = re.sub(r"\b\d+\.?\d*\s*(l|ml|kg|g|oz|lb)\b", "", normalized)

        # Collapse multiple spaces
        normalized = re.sub(r"\s+", " ", normalized).strip()

        return normalized

    async def match_product_by_name(
        self,
        name: str,
        category: Optional[str],
        session: AsyncSession,
        threshold: int = MATCH_THRESHOLD,
    ) -> Optional[Product]:
        """
        Find matching product by fuzzy name match.

        Queries products (optionally filtered by category), normalizes names,
        and finds the best match using Levenshtein distance. Returns product
        only if similarity score exceeds threshold.

        Args:
            name: Product name to match
            category: Optional category filter for narrower matches
            session: AsyncSession for database operations
            threshold: Similarity threshold (0-100, default 85)

        Returns:
            Best matching Product if score >= threshold, None otherwise

        Raises:
            Exception: On database errors
        """
        try:
            normalized_input = self.normalize_name(name)

            if not normalized_input:
                logger.warning("Empty normalized name provided for matching")
                return None

            # Query products (optionally filtered by category)
            stmt = select(Product)
            if category:
                stmt = stmt.where(Product.category == category)

            result = await session.execute(stmt)
            candidates = result.scalars().all()

            if not candidates:
                logger.debug(f"No candidate products found for matching: {name}")
                return None

            # Build candidate list for fuzzy matching
            candidate_names = [self.normalize_name(p.name) for p in candidates]

            # Find best match using rapidfuzz
            best_match = extractOne(
                normalized_input,
                candidate_names,
                score_cutoff=threshold,  # 0-100 scale, same as WRatio scorer
            )

            if best_match is None:
                logger.debug(
                    f"No fuzzy match found for '{name}' "
                    f"(threshold={threshold}, candidates={len(candidates)})"
                )
                return None

            # best_match is tuple: (match_string, score, index)
            matched_name, score, idx = best_match
            matched_product = candidates[idx]

            logger.debug(
                f"Fuzzy matched '{name}' -> '{matched_product.name}' "
                f"(score={score * 100:.1f}%)"
            )

            return matched_product

        except Exception as e:
            logger.error(f"Error during fuzzy product matching: {e}")
            raise


class RedisDedup:
    """
    Redis-backed deduplication to prevent duplicate price ingestion.

    Uses Redis keys with TTL to track ingested price reports within a
    catalogue cycle (typically 7 days). Prevents the same product at the
    same price from being ingested multiple times.
    """

    PREFIX = "dedup"
    DEFAULT_TTL_DAYS = 7

    def dedup_key(
        self, product_id: UUID, store_id: UUID, price_cents: int, valid_from: date
    ) -> str:
        """
        Generate Redis key for deduplication check.

        Creates a unique key combining product, store, price, and validity date.
        This identifies a specific price observation.

        Args:
            product_id: Product UUID
            store_id: Store UUID
            price_cents: Price in AUD cents
            valid_from: Catalogue valid-from date

        Returns:
            Redis key string (e.g., "dedup:550e8400:a1b2c3d4:999:2026-08-28")

        Example:
            >>> key = dedup.dedup_key(
            ...     UUID("550e8400-e29b-41d4-a716-446655440000"),
            ...     UUID("a1b2c3d4-e5f6-47a8-b9c0-d1e2f3a4b5c6"),
            ...     999,
            ...     date(2026, 8, 28)
            ... )
            >>> key
            "dedup:550e8400:a1b2c3d4:999:2026-08-28"
        """
        return f"{self.PREFIX}:{product_id}:{store_id}:{price_cents}:{valid_from}"

    async def is_duplicate(self, key: str, redis: Redis) -> bool:
        """
        Check if this price report already ingested.

        Queries Redis for the dedup key. Returns True if key exists (duplicate),
        False if new price observation.

        Args:
            key: Redis dedup key from dedup_key()
            redis: Redis connection

        Returns:
            True if duplicate (key exists), False if new

        Raises:
            Exception: On Redis errors
        """
        try:
            exists = redis.exists(key)
            return bool(exists)
        except Exception as e:
            logger.error(f"Error checking dedup status: {e}")
            raise

    async def mark_ingested(
        self, key: str, redis: Redis, ttl_days: int = DEFAULT_TTL_DAYS
    ) -> None:
        """
        Mark as ingested with TTL.

        Sets Redis key with expiration time (default 7 days = one catalogue cycle).
        After TTL expires, the key is automatically deleted and price can be
        re-ingested if needed.

        Args:
            key: Redis dedup key from dedup_key()
            redis: Redis connection
            ttl_days: Time-to-live in days (default 7)

        Raises:
            Exception: On Redis errors
        """
        try:
            ttl_seconds = ttl_days * 86400  # 86400 seconds per day
            redis.setex(key, ttl_seconds, "1")
            logger.debug(f"Marked as ingested: {key} (TTL={ttl_days}d)")
        except Exception as e:
            logger.error(f"Error marking as ingested: {e}")
            raise


# Module-level instances (singleton pattern for easy access)
_gtin_resolver: Optional[GTINResolver] = None
_fuzzy_matcher: Optional[FuzzyMatcher] = None
_redis_dedup: Optional[RedisDedup] = None


def get_gtin_resolver() -> GTINResolver:
    """Get or create GTINResolver singleton."""
    global _gtin_resolver
    if _gtin_resolver is None:
        _gtin_resolver = GTINResolver()
    return _gtin_resolver


def get_fuzzy_matcher() -> FuzzyMatcher:
    """Get or create FuzzyMatcher singleton."""
    global _fuzzy_matcher
    if _fuzzy_matcher is None:
        _fuzzy_matcher = FuzzyMatcher()
    return _fuzzy_matcher


def get_redis_dedup() -> RedisDedup:
    """Get or create RedisDedup singleton."""
    global _redis_dedup
    if _redis_dedup is None:
        _redis_dedup = RedisDedup()
    return _redis_dedup


async def ingest_scraped_price(
    product_name: str,
    price_cents: int,
    was_price_cents: Optional[int],
    chain: str,
    valid_from: date,
    gtin: Optional[str],
    session: AsyncSession,
    redis: Redis,
    skip_fuzzy: bool = False,
    store_product_id: Optional[str] = None,
) -> PriceReport:
    """
    Complete ingest pipeline for scraped prices.

    Implements full product resolution and price ingestion pipeline:
    1. GTIN resolution (lookup or create via Open Food Facts)
    2. Fuzzy name matching (if GTIN not available)
    3. Deduplication check (Redis 7-day cache)
    4. INSERT price_report into database
    5. Mark as ingested in Redis
    6. Enqueue watchlist check task

    Args:
        product_name: Product name/description
        price_cents: Current price in AUD cents
        was_price_cents: Original price before sale (None if not on sale)
        chain: Grocery chain name (e.g., 'coles', 'woolworths')
        valid_from: Catalogue valid-from date
        gtin: Global Trade Item Number (optional)
        session: AsyncSession for database operations
        redis: Redis connection

    Returns:
        Created PriceReport record

    Raises:
        DuplicateError: If price already ingested (in Redis cache)
        GTINResolutionError: If GTIN resolution fails
        ScraperError: For other scraper-related errors
        Exception: On database or Redis errors

    Example:
        >>> from datetime import date
        >>> report = await ingest_scraped_price(
        ...     product_name="Coca-Cola 600ml",
        ...     price_cents=399,
        ...     was_price_cents=499,
        ...     chain="coles",
        ...     valid_from=date(2026, 8, 28),
        ...     gtin="5000112345678",
        ...     session=session,
        ...     redis=redis
        ... )
        >>> print(f"Ingested: {report.id}")
    """
    try:
        # Get service instances
        gtin_resolver = get_gtin_resolver()
        fuzzy_matcher = get_fuzzy_matcher()
        redis_dedup = get_redis_dedup()

        logger.info(
            f"Ingesting price: {product_name} at ${price_cents / 100:.2f} "
            f"from {chain} (valid_from={valid_from})"
        )

        # Step 1: Resolve product
        product = None

        # Priority 1: store's own product ID — matches what the catalog scraper stored
        if store_product_id:
            external_source = f"catalog_{chain}"
            stmt = select(Product).where(
                Product.external_source == external_source,
                Product.external_id == str(store_product_id),
            ).limit(1)
            result = await session.execute(stmt)
            product = result.scalar_one_or_none()

        # Priority 2: real GTIN barcode lookup
        if not product and gtin:
            product = await gtin_resolver.lookup_gtin(gtin, session)
            if not product:
                product = await gtin_resolver.create_product_from_gtin(gtin, session)

        # Priority 3: fuzzy name match (skipped for flyer ingest — too slow)
        if not product:
            if not skip_fuzzy:
                product = await fuzzy_matcher.match_product_by_name(
                    product_name, None, session
                )

        # Priority 4: create a new product record
        if not product:
            product = Product(name=product_name, gtin=None)
            session.add(product)
            await session.flush()

        logger.debug(f"Resolved product: {product.id} - {product.name}")

        # Step 2: Get store by chain (look up in database)
        # Note: In production, chain_store_id would be resolved by store lookup
        # For now, we'll use a simple chain-based lookup
        stmt = select(Store).where(Store.chain == chain).limit(1)
        result = await session.execute(stmt)
        store = result.scalar_one_or_none()

        if not store:
            # Create default store for chain if not found
            store = Store(chain=chain, name=f"{chain} (generic)")
            session.add(store)
            await session.flush()

        # Step 3: Check deduplication (Redis)
        dedup_key = redis_dedup.dedup_key(
            product.id, store.id, price_cents, valid_from
        )

        if await redis_dedup.is_duplicate(dedup_key, redis):
            error_msg = f"Duplicate price report: {dedup_key}"
            logger.warning(error_msg)
            raise DuplicateError(error_msg)

        # Step 4: Calculate discount percentage
        sale_pct_off = None
        is_on_sale = False

        if was_price_cents and price_cents < was_price_cents:
            is_on_sale = True
            sale_pct_off = (
                (was_price_cents - price_cents) / was_price_cents * 100
            )

        # Determine valid_to (typically end of catalogue cycle)
        valid_to = None
        if valid_from:
            # Assume 7-day catalogue cycle
            valid_to = datetime.combine(
                valid_from + timedelta(days=7),
                datetime.min.time()
            ).replace(tzinfo=timezone.utc)

        valid_from_dt = datetime.combine(
            valid_from,
            datetime.min.time()
        ).replace(tzinfo=timezone.utc)

        # Step 5: INSERT price_report
        price_report = PriceReport(
            product_id=product.id,
            store_id=store.id,
            price_cents=price_cents,
            was_price_cents=was_price_cents,
            source=f"flyer_{chain}",
            is_on_sale=is_on_sale,
            sale_pct_off=sale_pct_off,
            valid_from=valid_from_dt,
            valid_to=valid_to,
            confidence=1.0,
            created_at=datetime.now(timezone.utc),
        )

        session.add(price_report)
        await session.flush()

        logger.debug(f"Created PriceReport: {price_report.id}")

        # Step 6: Mark as ingested in Redis
        await redis_dedup.mark_ingested(dedup_key, redis)

        # Step 7: Enqueue watchlist check (Celery task)
        try:
            from app.workers.notification_tasks import check_watchlist_for_price

            check_watchlist_for_price.delay(str(price_report.id))
            logger.debug(f"Queued watchlist check for {price_report.id}")
        except Exception as e:
            logger.warning(f"Failed to queue watchlist check: {e}")
            # Don't fail the ingest if task queueing fails

        # Step 8: Enqueue embedding update if product has no embedding yet
        try:
            if product.name_embedding is None:
                from app.workers.embedding_tasks import embed_product
                embed_product.delay(str(product.id))
        except Exception as e:
            logger.warning(f"Failed to queue embed_product: {e}")

        return price_report

    except DuplicateError:
        raise
    except GTINResolutionError:
        raise
    except Exception as e:
        logger.error(f"Error during price ingestion: {e}", exc_info=True)
        raise ScraperError(f"Price ingestion failed: {str(e)}")

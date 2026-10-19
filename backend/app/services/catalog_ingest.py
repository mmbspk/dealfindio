"""
Catalog ingest — upsert full-catalog products into the shared schema.

The flyer ingest (``product_dedup.ingest_scraped_price``) is async and geared to
weekly specials with Redis dedup. Catalog crawls are different: they run inside a
single Celery task with one sync ``Session``, touch tens of thousands of rows,
and carry richer product metadata (image, brand, category, in-stock, SKU). This
module is the sync upsert path for that.

Matching strategy (in priority order):
  1. by GTIN, if present — globally unique; outranks all other signals;
  2. by durable ProductRetailerListing(chain, external_id) — most stable once established;
  3. by normalized name (fuzzy); cross-chain dedup only, NOT trusted for listing reassignment.
On create, only canonical product metadata is written; retailer-specific metadata
goes exclusively to ``ProductRetailerListing``. On update, we fill only missing
canonical fields (never clobber a human/OFF-sourced image with a null). A
``PriceReport`` with ``source="catalog_{chain}"`` records the observed shelf price.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.models import PriceReport, Product, ProductRetailerListing, Store
from app.services.category_normaliser import CANONICAL_CATEGORIES, normalise_category
from app.services.package_size import normalize_package_size
from app.services.product_dedup import FuzzyMatcher, brands_conflict
from scrapers.catalog_base import CatalogProduct

logger = logging.getLogger(__name__)

_matcher = FuzzyMatcher()

# ── Price sanity check ───────────────────────────────────────────────────────
# Thresholds for detecting corrupt / mismatched prices. Two-condition design
# avoids false-positives from legitimate deep discounts:
#
#   Rule A — extreme outlier: price alone is < 20% of baseline (>80% off).
#             Any legitimate ~80% sale should still be verified by a human.
#
#   Rule B — moderate outlier with suspicious "was" price: price < 35% of
#             baseline AND the retailer's own "was" price < 65% of baseline.
#             On a genuine 70% sale the "was" price should match the known
#             market price; when BOTH are far below baseline the product
#             identity itself is almost certainly wrong (a mismatched item).
#
# Both rules require at least 2 clean, recent prices from OTHER chains as a
# baseline so a single flawed report can't become the new normal.

_SUSPECT_EXTREME_RATIO = 0.20  # Rule A: price < 20% of median → flag
_SUSPECT_PRICE_RATIO = 0.35  # Rule B price leg: price < 35% of median
_SUSPECT_WAS_RATIO = 0.65  # Rule B was-price leg: was < 65% of median
_SUSPECT_MIN_SAMPLES = 2  # min other-chain prices needed for a baseline
_SUSPECT_LOOKBACK_DAYS = 90  # how far back to look for baseline prices


class ResolutionReason(str, Enum):
    """How a CatalogProduct was matched to a canonical Product.

    Used to determine whether a ProductRetailerListing may be reassigned to a
    different canonical product when a conflict is detected.
    """

    LISTING = "listing"  # durable listing hit — most trusted once established
    GTIN = "gtin"  # GTIN match — globally unique, highly trusted
    FUZZY = "fuzzy"  # composite name/brand/price score — NOT trusted for reassignment
    NEW = "new"  # no match found; caller created a new canonical Product


def _is_suspect_price(
    session: Session,
    product: Product,
    chain: str,
    price_cents: int,
    was_price_cents: int | None,
) -> bool:
    """Return True if this price looks like corrupt/mismatched data.

    Uses two independent rules so legitimate deep discounts aren't blocked:
    - Rule A: price alone is < 20% of the recent median (catches obvious garbage).
    - Rule B: BOTH price and was_price are well below baseline (the product
      identity is likely wrong — a mismatched item from fuzzy dedup).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_SUSPECT_LOOKBACK_DAYS)
    rows = (
        session.execute(
            select(PriceReport.price_cents)
            .where(
                PriceReport.product_id == product.id,
                PriceReport.created_at >= cutoff,
                PriceReport.is_flagged == False,  # noqa: E712 — SQLAlchemy needs ==
                PriceReport.source != f"catalog_{chain}",
            )
            .order_by(PriceReport.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )

    if len(rows) < _SUSPECT_MIN_SAMPLES:
        return False

    sorted_prices = sorted(rows)
    median = sorted_prices[len(sorted_prices) // 2]

    # Rule A — extreme: price is < 20% of median
    if price_cents < median * _SUSPECT_EXTREME_RATIO:
        return True

    # Rule B — moderate + was_price also low (product identity likely wrong)
    if (
        price_cents < median * _SUSPECT_PRICE_RATIO
        and was_price_cents is not None
        and was_price_cents < median * _SUSPECT_WAS_RATIO
    ):
        return True

    return False


@dataclass
class IngestCounts:
    """Tally returned by a catalog ingest batch."""

    created: int = 0
    updated: int = 0
    price_reports: int = 0


def _find_store_for_chain(session: Session, chain: str) -> Store:
    """Return the generic catalog store row for the chain, creating one if needed.

    Catalog prices are chain-wide (not per-physical-store), so we attach them to
    a dedicated "{chain} (catalog)" store row, not a physical location.
    This prevents catalog prices from incorrectly appearing under a specific suburb.
    """
    generic_name = f"{chain} (catalog)"
    store = session.execute(
        select(Store).where(
            Store.chain == chain,
            Store.name == generic_name,
        )
    ).scalar_one_or_none()
    if store is None:
        store = Store(chain=chain, name=generic_name)
        session.add(store)
        session.flush()
    return store


def _propose_candidate(
    session: Session, product_a: "Product", product_b: "Product", score: float
) -> None:
    """Write a pending match candidate if one doesn't already exist for this pair."""
    from sqlalchemy import select as sa_select

    from app.models.product_match import ProductMatchCandidate

    a_id, b_id = sorted([product_a.id, product_b.id])
    existing = session.execute(
        sa_select(ProductMatchCandidate).where(
            ProductMatchCandidate.product_a_id == a_id,
            ProductMatchCandidate.product_b_id == b_id,
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            ProductMatchCandidate(
                product_a_id=a_id,
                product_b_id=b_id,
                score=score,
                status="pending",
                proposed_by="auto",
            )
        )
        session.flush()


def _match_product(
    session: Session, cp: CatalogProduct, chain: str
) -> tuple["Product | None", float, "Product | None", ResolutionReason]:
    """Find an existing product for this catalog item.

    Returns (exact_match, score, fuzzy_candidate, resolution_reason):
    - exact_match set → caller should use it (durable listing, SKU/GTIN hit, or 100% fuzzy)
    - fuzzy_candidate set, exact_match None → 92–99% match; caller writes a review candidate
    - both None → no match found; create a new product row

    Resolution priority:
      1. GTIN — globally unique; outranks every other signal when a match exists
      2. ProductRetailerListing(chain, external_id) — durable SKU→canonical mapping
      3. Fuzzy name match — cross-chain dedup only; NOT trusted for listing reassignment

    GTIN is Step 1 so that a trusted identifier correction always wins over an
    existing (possibly stale) listing association.  When GTIN is present but has
    no matching Product we still fall through to the listing step; fuzzy is
    skipped because a novel GTIN means this is genuinely a new product.
    """
    # Step 1 — GTIN (globally unique; outranks listing when both are present)
    if cp.gtin:
        normalized = cp.gtin.zfill(14)
        found = session.execute(
            select(Product).where(Product.gtin == normalized)
        ).scalar_one_or_none()
        if found:
            return found, 100.0, None, ResolutionReason.GTIN
        # GTIN present, no match: fall through to listing so an already-
        # established mapping is not discarded; fuzzy is skipped below.

    # Step 2 — durable listing lookup
    if cp.external_id:
        listing = session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.chain == chain,
                ProductRetailerListing.external_id == str(cp.external_id),
            )
        ).scalar_one_or_none()
        if listing is not None:
            product = session.get(Product, listing.product_id)
            if product is not None:
                return product, 100.0, None, ResolutionReason.LISTING

    # Step 3 — fuzzy name match
    # Skip entirely when GTIN is present: a GTIN with no DB match means this is
    # a genuinely new product; merging it via partial name similarity would be wrong.
    if cp.gtin:
        return None, 0.0, None, ResolutionReason.NEW
    normalized_input = _matcher.normalize_name(cp.product_name)
    if not normalized_input:
        return None, 0.0, None, ResolutionReason.NEW
    # Exclude products already owned by a listing for this chain so we don't
    # accidentally merge two different products via fuzzy name.
    has_listing_for_chain = (
        select(ProductRetailerListing.id)
        .where(
            ProductRetailerListing.product_id == Product.id,
            ProductRetailerListing.chain == chain,
        )
        .exists()
    )
    candidates = list(
        session.execute(select(Product).where(~has_listing_for_chain)).scalars().all()
    )
    if not candidates:
        return None, 0.0, None, ResolutionReason.NEW
    from rapidfuzz.process import extractOne

    MIN_CANDIDATE_LEN = 15
    filtered_candidates = []
    names = []
    for p in candidates:
        n = _matcher.normalize_name(p.name)
        if n and len(n) >= MIN_CANDIDATE_LEN:
            filtered_candidates.append(p)
            names.append(n)
    if not filtered_candidates:
        return None, 0.0, None, ResolutionReason.NEW
    best = extractOne(normalized_input, names, score_cutoff=FuzzyMatcher.MATCH_THRESHOLD)
    if best is None:
        return None, 0.0, None, ResolutionReason.NEW
    _matched_name, title_score, idx = best
    matched = filtered_candidates[idx]

    # Hard veto: two products with clearly different brands are never the same
    # item, even if their names collide (e.g. Kirks "Orange" vs Fanta "Orange").
    # No merge, no review candidate.
    if brands_conflict(cp.brand, matched.brand):
        logger.debug(
            "Brand conflict veto: '%s' [%s] vs '%s' [%s]",
            cp.product_name,
            cp.brand,
            matched.name,
            matched.brand,
        )
        return None, 0.0, None, ResolutionReason.NEW

    # Composite score, weighted only over the signals actually present. Base
    # weights are title 0.70 / brand 0.20 / price 0.10; a missing signal is
    # dropped and the remaining weights are renormalised to sum to 1. This keeps
    # brand a real signal when known, never inflates "both brands unknown" into a
    # forced merge, and never lets a missing price drag a solid title match down.
    W_TITLE, W_BRAND, W_PRICE = 0.70, 0.20, 0.10

    from rapidfuzz.fuzz import ratio as fuzz_ratio

    both_brands_known = bool(cp.brand and matched.brand)
    brand_score = None
    if both_brands_known:
        brand_score = fuzz_ratio(
            _matcher.normalize_name(cp.brand or ""),
            _matcher.normalize_name(matched.brand or ""),
        )

    # CatalogProduct carries the shelf price as ``price_cents`` (there is no
    # ``price`` attribute); convert to dollars to compare against the matched
    # product's latest price_report. Only a real comparison contributes.
    price_score = None
    a_price = cp.price_cents / 100.0 if cp.price_cents else None
    if a_price is not None and a_price > 0:
        from sqlalchemy import text as sa_text

        row = session.execute(
            sa_text(
                """
                SELECT price_cents FROM price_reports
                WHERE product_id = :pid
                ORDER BY created_at DESC LIMIT 1
            """
            ),
            {"pid": str(matched.id)},
        ).fetchone()
        if row is not None:
            b_price = row.price_cents / 100.0
            pct_diff = abs(a_price - b_price) / max(a_price, b_price)
            price_score = max(0.0, 100.0 * (1.0 - pct_diff / 0.5))

    weighted = title_score * W_TITLE
    total_weight = W_TITLE
    if brand_score is not None:
        weighted += brand_score * W_BRAND
        total_weight += W_BRAND
    if price_score is not None:
        weighted += price_score * W_PRICE
        total_weight += W_PRICE
    score = weighted / total_weight

    # Size-variant veto: when both sides have parseable canonical sizes that differ,
    # the products are definitively different pack sizes.  Capping the score would
    # still surface them as a review candidate — instead return early with no
    # candidate so _propose_candidate() is never called.
    if cp.package_size and matched.package_size_norm:
        cp_norm = normalize_package_size(cp.package_size)
        if cp_norm and cp_norm != matched.package_size_norm:
            logger.debug(
                "Size variant veto: '%s' (%s) vs '%s' (%s) — not a match candidate",
                cp.product_name,
                cp_norm,
                matched.name,
                matched.package_size_norm,
            )
            return None, 0.0, None, ResolutionReason.NEW

    if score >= 100.0:
        # Perfect match — auto-merge
        return matched, score, None, ResolutionReason.FUZZY
    # 92–99%: surface for admin review, don't auto-merge
    return None, score, matched, ResolutionReason.NEW


def _upsert_retailer_listing(
    session: Session,
    cp: CatalogProduct,
    chain: str,
    product: Product,
    resolution: ResolutionReason,
    now: datetime,
) -> None:
    """Create or refresh the ProductRetailerListing for this catalog item.

    Only writes when ``cp.external_id`` is set — listings require a stable
    retailer SKU as their identity key.

    Reassignment policy when an existing listing points at a different product:
    - Dangling (old product deleted): always reassign (log INFO).
    - GTIN: allow — globally unique; this is the trusted correction path (log WARNING).
    - FUZZY: never — unstable name similarity must not move an established SKU
      mapping (log WARNING, keep existing, still refresh metadata).
    - LISTING: invariant violation — _match_product resolved through this very
      listing so product_id should already match; log ERROR, keep existing.
    - NEW: block — a newly created product should not steal an established listing
      (log WARNING, keep existing).
    """
    if not cp.external_id:
        return

    external_id = str(cp.external_id)
    external_source = f"catalog_{chain}"

    listing = session.execute(
        select(ProductRetailerListing).where(
            ProductRetailerListing.chain == chain,
            ProductRetailerListing.external_id == external_id,
        )
    ).scalar_one_or_none()

    if listing is None:
        session.add(
            ProductRetailerListing(
                product_id=product.id,
                chain=chain,
                external_id=external_id,
                external_source=external_source,
                external_name=cp.product_name or None,
                external_brand=cp.brand or None,
                external_category=cp.category or None,
                external_image_url=cp.image_url or None,
                external_url=cp.url or None,
                package_size_raw=cp.package_size or None,
                in_stock=cp.in_stock,
                is_online_only=cp.is_online_only,
                catalog_last_seen_at=now,
            )
        )
        session.flush()
        return

    # Listing exists — check for product_id change
    if listing.product_id != product.id:
        old_product = session.get(Product, listing.product_id)

        if old_product is None:
            # Dangling listing (old product was deleted); always reassign
            logger.info(
                "Reassigning dangling listing %s/%s old_product=%s new_product=%s",
                chain,
                external_id,
                listing.product_id,
                product.id,
            )
            listing.product_id = product.id

        elif resolution == ResolutionReason.GTIN:
            # GTIN is globally unique and trusted — the only correction path
            # allowed to move an established listing to a different canonical product.
            logger.warning(
                "Reassigning listing %s/%s via GTIN: old_product=%s new_product=%s",
                chain,
                external_id,
                listing.product_id,
                product.id,
            )
            listing.product_id = product.id

        elif resolution == ResolutionReason.FUZZY:
            # Fuzzy match suggests a different product — do NOT move the listing.
            # The durable SKU association wins over unstable name similarity.
            logger.warning(
                "Blocked fuzzy reassignment for listing %s/%s: "
                "keeping product_id=%s fuzzy_suggested=%s",
                chain,
                external_id,
                listing.product_id,
                product.id,
            )
            _refresh_listing_metadata(listing, cp, external_source, now)
            session.flush()
            return

        elif resolution == ResolutionReason.LISTING:
            # _match_product resolved via this very listing, so product_id must
            # already match — a conflict here is an invariant violation.
            logger.error(
                "Invariant violation: LISTING resolution for %s/%s returned "
                "product=%s but listing.product_id=%s; keeping existing",
                chain,
                external_id,
                product.id,
                listing.product_id,
            )
            _refresh_listing_metadata(listing, cp, external_source, now)
            session.flush()
            return

        else:
            # NEW (and any future reason): block reassignment.
            # An established (chain, external_id) mapping is authoritative;
            # a newly-created product must not steal it.  The mismatch is logged
            # so it can be investigated.
            logger.warning(
                "Blocked %s reassignment for listing %s/%s: " "keeping product_id=%s proposed=%s",
                resolution.value,
                chain,
                external_id,
                listing.product_id,
                product.id,
            )
            _refresh_listing_metadata(listing, cp, external_source, now)
            session.flush()
            return

    _refresh_listing_metadata(listing, cp, external_source, now)
    session.flush()


def _refresh_listing_metadata(
    listing: ProductRetailerListing,
    cp: CatalogProduct,
    external_source: str,
    now: datetime,
) -> None:
    """Update all mutable retailer metadata fields from the latest crawl.

    String fields are updated only when the new value is non-empty to avoid
    nulling out previously stored data when a crawl returns incomplete metadata.
    Liveness signals (in_stock, is_online_only, catalog_last_seen_at) always refresh.
    """
    listing.external_source = external_source
    if cp.product_name:
        listing.external_name = cp.product_name
    if cp.brand:
        listing.external_brand = cp.brand
    if cp.category:
        listing.external_category = cp.category
    if cp.image_url:
        listing.external_image_url = cp.image_url
    if cp.url:
        listing.external_url = cp.url
    if cp.package_size:
        listing.package_size_raw = cp.package_size
    listing.in_stock = cp.in_stock
    listing.is_online_only = cp.is_online_only
    listing.catalog_last_seen_at = now


def recompute_product_canonical_fields(
    session: Session, product_id
) -> tuple[str | None, str | None]:
    """Return (package_size_norm, category) for a product from its listings.

    Single query ordered by catalog_last_seen_at DESC NULLS LAST, id ASC.
    Iterates in that order and returns the first parseable value for each field.
    Crawl-order independent; unknown external_category values are skipped (never
    returned verbatim).
    """
    rows = session.execute(
        select(
            ProductRetailerListing.package_size_raw,
            ProductRetailerListing.external_category,
        )
        .where(
            ProductRetailerListing.product_id == product_id,
            or_(
                ProductRetailerListing.package_size_raw.isnot(None),
                ProductRetailerListing.external_category.isnot(None),
            ),
        )
        .order_by(
            ProductRetailerListing.catalog_last_seen_at.desc().nulls_last(),
            ProductRetailerListing.id.asc(),
        )
    ).all()

    pkg_norm: str | None = None
    category: str | None = None
    for raw_size, raw_cat in rows:
        if pkg_norm is None and raw_size:
            pkg_norm = normalize_package_size(raw_size)
        if category is None and raw_cat:
            category = normalise_category(raw_cat)
        if pkg_norm is not None and category is not None:
            break

    return pkg_norm, category


def recompute_product_package_size_norm(session: Session, product_id) -> str | None:
    """Return canonical package_size_norm for a product from its listings.

    Convenience wrapper around recompute_product_canonical_fields.
    """
    norm, _ = recompute_product_canonical_fields(session, product_id)
    return norm


def recompute_product_category(session: Session, product_id) -> str | None:
    """Return canonical category for a product from its listings.

    Queries all listings ordered by catalog_last_seen_at DESC NULLS LAST, id ASC,
    returns the first non-None normalised category value.  Unknown raw categories
    return None — never stored verbatim.  Crawl-order independent.
    """
    _, category = recompute_product_canonical_fields(session, product_id)
    return category


def ingest_catalog_product(
    cp: CatalogProduct,
    chain: str,
    session: Session,
    store: Store,
    now: Optional[datetime] = None,
) -> tuple[bool, bool]:
    """Upsert one catalog product + its price observation.

    Returns ``(created, updated)`` booleans for tallying.
    """
    now = now or datetime.now(timezone.utc)
    product, match_score, fuzzy_candidate, resolution = _match_product(session, cp, chain)
    created = False
    updated = False

    if product is None:
        product = Product(
            gtin=cp.gtin.zfill(14) if cp.gtin else None,
            name=cp.product_name,
            brand=cp.brand,
            unit_price=cp.unit_price,
            unit_price_label=cp.unit_price_label,
        )
        session.add(product)
        session.flush()
        if fuzzy_candidate is not None:
            _propose_candidate(session, product, fuzzy_candidate, match_score)
        created = True
    else:
        # Fill only missing canonical metadata — never clobber an existing brand.
        if cp.brand and not product.brand:
            product.brand = cp.brand
            updated = True
        if cp.unit_price is not None and (
            product.unit_price is None or product.unit_price != cp.unit_price
        ):
            product.unit_price = cp.unit_price
            updated = True
        if cp.unit_price_label and product.unit_price_label != cp.unit_price_label:
            product.unit_price_label = cp.unit_price_label
            updated = True

    # Upsert the retailer listing — sole source of retailer-specific metadata.
    # Happens after canonical product_id is confirmed and flushed.
    # Participates in the same savepoint as the Product write.
    _upsert_retailer_listing(session, cp, chain, product, resolution, now)

    # Derive canonical package_size_norm and category from all listings in one query:
    # catalog_last_seen_at DESC NULLS LAST, id ASC — picks first parseable value each.
    # Crawl-order independent; unknown categories are never returned verbatim.
    new_norm, new_category = recompute_product_canonical_fields(session, product.id)
    if new_norm != product.package_size_norm:
        product.package_size_norm = new_norm
        if not created:
            updated = True

    # Transitional compatibility: listing canonical wins when present.
    # When listings have no canonical evidence yet (all external_category NULL),
    # do NOT erase a valid existing canonical — instead normalize/clear invalids.
    if new_category is not None:
        if new_category != product.category:
            product.category = new_category
            if not created:
                updated = True
    elif product.category not in CANONICAL_CATEGORIES:
        # Existing value is invalid (non-canonical raw string or None);
        # normalize it — never allow an unknown raw string to remain.
        normalised = normalise_category(product.category)
        if normalised != product.category:
            product.category = normalised
            if not created:
                updated = True

    # Record the observed shelf price.
    was = cp.was_price_cents
    if cp.is_on_sale is not None:
        is_on_sale = cp.is_on_sale
    else:
        is_on_sale = bool(was and cp.price_cents < was)
    sale_pct_off = ((was - cp.price_cents) / was * 100) if (is_on_sale and was) else None
    valid_from = cp.valid_from or date.today()
    valid_from_dt = datetime.combine(valid_from, datetime.min.time()).replace(tzinfo=timezone.utc)
    valid_to_dt = (
        datetime.combine(cp.valid_to, datetime.min.time()).replace(tzinfo=timezone.utc)
        if cp.valid_to
        else None
    )

    is_suspect = _is_suspect_price(session, product, chain, cp.price_cents, was)
    if is_suspect:
        logger.warning(
            "SUSPECT price flagged: %d¢ (was %s¢) for %r [chain=%s, product_id=%s] — "
            "price deviates strongly from cross-chain baseline; stored but excluded from results",
            cp.price_cents,
            was,
            product.name,
            chain,
            product.id,
        )

    # Change-log pattern: only insert a new row when the price data actually changed.
    # If the most-recent observation for this (product, store) is identical in every
    # price field, just advance its created_at so it remains "current" — this prevents
    # the table from accumulating duplicate rows every crawl and keeps historical
    # queries meaningful (a new row genuinely means the price changed).
    existing_pr = (
        session.execute(
            select(PriceReport)
            .where(
                PriceReport.product_id == product.id,
                PriceReport.store_id == store.id,
            )
            .order_by(desc(PriceReport.created_at))
            .limit(1)
        )
        .scalars()
        .first()
    )
    if (
        existing_pr is not None
        and existing_pr.price_cents == cp.price_cents
        and existing_pr.was_price_cents == was
        and bool(existing_pr.is_on_sale) == is_on_sale
        and bool(existing_pr.is_flagged) == is_suspect
    ):
        existing_pr.created_at = now
        session.flush()
        return created, updated

    price_report = PriceReport(
        product_id=product.id,
        store_id=store.id,
        price_cents=cp.price_cents,
        was_price_cents=was,
        source=f"catalog_{chain}",
        is_on_sale=is_on_sale,
        sale_pct_off=sale_pct_off,
        valid_from=valid_from_dt,
        valid_to=valid_to_dt,
        confidence=0.1 if is_suspect else 1.0,
        is_flagged=is_suspect,
        created_at=now,
    )
    session.add(price_report)
    session.flush()

    # Trigger watchlist notification check (on-sale only — skip routine shelf prices).
    if is_on_sale:
        try:
            from app.workers.notification_tasks import check_watchlist_for_price

            check_watchlist_for_price.delay(str(price_report.id))
        except Exception as exc:
            logger.warning("Failed to enqueue watchlist check for %s: %s", price_report.id, exc)

    return created, updated


def ingest_catalog_products(
    products: list[CatalogProduct],
    chain: str,
    session: Session,
) -> IngestCounts:
    """Ingest a batch of catalog products (one category's worth).

    Commits nothing — the caller (task) owns the transaction boundary.
    """
    counts = IngestCounts()
    if not products:
        return counts
    store = _find_store_for_chain(session, chain)
    now = datetime.now(timezone.utc)
    for cp in products:
        try:
            with session.begin_nested():
                created, updated = ingest_catalog_product(cp, chain, session, store, now)
        except Exception as exc:
            logger.warning("Catalog ingest failed for %r: %s", cp.product_name, exc)
            continue
        counts.created += int(created)
        counts.updated += int(updated and not created)
        counts.price_reports += 1
    return counts

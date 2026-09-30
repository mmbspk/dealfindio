"""
Catalog ingest — upsert full-catalog products into the shared schema.

The flyer ingest (``product_dedup.ingest_scraped_price``) is async and geared to
weekly specials with Redis dedup. Catalog crawls are different: they run inside a
single Celery task with one sync ``Session``, touch tens of thousands of rows,
and carry richer product metadata (image, brand, category, in-stock, SKU). This
module is the sync upsert path for that.

Matching strategy (in priority order):
  1. by GTIN, if present;
  2. by external SKU stored on a prior catalog row (via ``image_source`` marker
     is not used for this — SKUs live in price_report.source-independent lookup);
  3. by normalized name.
On create, all provided metadata is written. On update, we fill only missing
fields (never clobber a human/OFF-sourced image with a null) and always refresh
``catalog_last_seen_at`` / ``in_stock``. A ``PriceReport`` with
``source="catalog_{chain}"`` records the observed shelf price.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import PriceReport, Product, Store
from app.services.category_normaliser import normalise_category
from app.services.product_dedup import FuzzyMatcher, brands_conflict
from scrapers.catalog_base import CatalogProduct

logger = logging.getLogger(__name__)

_matcher = FuzzyMatcher()


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
) -> tuple["Product | None", float, "Product | None"]:
    """Find an existing product for this catalog item.

    Returns (exact_match, score, fuzzy_candidate):
    - exact_match set → caller should use it (SKU/GTIN hit or 100% fuzzy)
    - fuzzy_candidate set, exact_match None → 92–99% match; caller writes a review candidate
    - both None → no match found; create a new product row

    Priority: (1) the store's own SKU (external_source + external_id) — the only
    truly stable key, so it must win; (2) GTIN; (3) fuzzy name match as a last
    resort. Matching SKU first prevents the fuzzy matcher from over-merging
    distinct variants (e.g. different flavours of one brand line).
    """
    external_source = f"catalog_{chain}"
    if cp.external_id:
        found = session.execute(
            select(Product).where(
                Product.external_source == external_source,
                Product.external_id == str(cp.external_id),
            )
        ).scalar_one_or_none()
        if found:
            return found, 100.0, None

    if cp.gtin:
        normalized = cp.gtin.zfill(14)
        found = session.execute(
            select(Product).where(Product.gtin == normalized)
        ).scalar_one_or_none()
        if found:
            return found, 100.0, None

    # Fuzzy name match — skip only if GTIN is set (GTIN is globally unique, so
    # no match means it's genuinely a new product). If we have an external_id
    # but no GTIN, still try fuzzy matching against products from OTHER chains
    # (same-chain miss already handled above). This is what makes cross-chain
    # dedup work for stores like ALDI that never share SKUs with Coles/Woolworths.
    if cp.gtin:
        return None, 0.0, None
    normalized_input = _matcher.normalize_name(cp.product_name)
    if not normalized_input:
        return None, 0.0, None
    # Exclude products already owned by this chain's SKU namespace so we don't
    # accidentally merge two different ALDI products via fuzzy name.
    external_source = f"catalog_{chain}"
    candidates = list(
        session.execute(
            select(Product).where(
                or_(Product.external_source != external_source, Product.external_source.is_(None))
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return None, 0.0, None
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
        return None, 0.0, None
    best = extractOne(normalized_input, names, score_cutoff=FuzzyMatcher.MATCH_THRESHOLD)
    if best is None:
        return None, 0.0, None
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
        return None, 0.0, None

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

    if score >= 100.0:
        # Perfect match — auto-merge
        return matched, score, None
    # 92–99%: surface for admin review, don't auto-merge
    return None, score, matched


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
    product, match_score, fuzzy_candidate = _match_product(session, cp, chain)
    created = False
    updated = False

    if product is None:
        product = Product(
            gtin=cp.gtin.zfill(14) if cp.gtin else None,
            name=cp.product_name,
            brand=cp.brand,
            category=normalise_category(cp.category),
            image_url=cp.image_url,
            image_source=f"catalog_{chain}" if cp.image_url else None,
            image_enriched_at=now if cp.image_url else None,
            catalog_last_seen_at=now,
            in_stock=cp.in_stock,
            external_id=str(cp.external_id) if cp.external_id else None,
            external_source=f"catalog_{chain}" if cp.external_id else None,
            package_size=cp.package_size,
            unit_price=cp.unit_price,
            unit_price_label=cp.unit_price_label,
        )
        session.add(product)
        session.flush()
        if fuzzy_candidate is not None:
            _propose_candidate(session, product, fuzzy_candidate, match_score)
        created = True
    else:
        # Fill only missing metadata — never clobber an existing image/brand.
        if cp.image_url and not product.image_url:
            product.image_url = cp.image_url
            product.image_source = f"catalog_{chain}"
            product.image_enriched_at = now
            updated = True
        if cp.brand and not product.brand:
            product.brand = cp.brand
            updated = True
        if cp.category and not product.category:
            product.category = normalise_category(cp.category)
            updated = True
        if cp.package_size and not product.package_size:
            product.package_size = cp.package_size
            updated = True
        if cp.unit_price is not None and (
            product.unit_price is None or product.unit_price != cp.unit_price
        ):
            product.unit_price = cp.unit_price
            updated = True
        if cp.unit_price_label and product.unit_price_label != cp.unit_price_label:
            product.unit_price_label = cp.unit_price_label
            updated = True
        # Always refresh liveness signals.
        product.catalog_last_seen_at = now
        product.in_stock = cp.in_stock
        updated = True

    # Record the observed shelf price.
    was = cp.was_price_cents
    is_on_sale = bool(was and cp.price_cents < was)
    sale_pct_off = ((was - cp.price_cents) / was * 100) if is_on_sale else None
    valid_from = cp.valid_from or date.today()
    valid_from_dt = datetime.combine(valid_from, datetime.min.time()).replace(tzinfo=timezone.utc)
    valid_to_dt = (
        datetime.combine(cp.valid_to, datetime.min.time()).replace(tzinfo=timezone.utc)
        if cp.valid_to
        else None
    )

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
        confidence=1.0,
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
            created, updated = ingest_catalog_product(cp, chain, session, store, now)
        except Exception as exc:
            logger.warning("Catalog ingest failed for %r: %s", cp.product_name, exc)
            continue
        counts.created += int(created)
        counts.updated += int(updated and not created)
        counts.price_reports += 1
    return counts

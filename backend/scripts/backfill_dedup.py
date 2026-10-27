"""
One-shot backfill: merge product rows that belong to the same real-world item
but were created by different chain crawlers before the cross-chain fuzzy-match
fix was applied.

Strategy:
  For each "source" chain (aldi, coles, iga) that has no GTINs:
    - Load all its products (via ProductRetailerListing)
    - For each product, run the same fuzzy-name match against products from
      OTHER chains (same logic as catalog_ingest._match_product)
    - If a match is found, re-point all price_reports / watchlist rows from the
      duplicate to the canonical row, then delete the duplicate

Safe to re-run: already-merged rows are skipped because they no longer exist.

Story #18 fixes (schema now stores chain on ProductRetailerListing, not Product):
  - Query source/candidate products via ProductRetailerListing.external_source JOIN
  - Added package-size guard: skip merges when both products have a known
    package_size_norm and the values differ (prevents e.g. "Coke 375mL" → "Coke 2L")
  - Added propagate_gtin_on_merge before DELETE
  - Added IGA to SOURCE_CHAINS (previously omitted)
  - Hard-excluded known conflict products (5fda8bda, c24cb38e) from all batches
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow running from repo root or backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "backfill-script")

from rapidfuzz.process import extract as fuzzy_extract
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Product
from app.models.product_retailer_listing import ProductRetailerListing
from app.services.product_dedup import FuzzyMatcher, brands_conflict

_matcher = FuzzyMatcher()
# Use a tighter threshold for backfill — 85 produces false positives when one
# shared word (e.g. "Chocolate", "Almonds") dominates the score across long names.
MATCH_THRESHOLD = 92

# Which chains to treat as "needs merging" — i.e. they have no GTINs
# so their products may duplicate rows from gtin-bearing chains.
# IGA added in Story #18 (was incorrectly excluded).
SOURCE_CHAINS = ["catalog_aldi", "catalog_coles", "catalog_iga"]

# Products that must never be touched by any automatic remediation batch.
# See backend/validation_output/ for the audit evidence.
#   5fda8bda: Tasmanian Heritage Camembert — IGA 125g / Woolworths 200g (bad merge)
#   c24cb38e: ALDI Energy Drink — raw=4x250mL, product name says 200ml
EXCLUDED_PRODUCT_IDS: frozenset[str] = frozenset(
    [
        "5fda8bda-c948-4d82-99f7-36e22253d354",
        "c24cb38e-6990-45c1-9ba0-c1047c13ba34",
    ]
)


def load_source_products(session: Session, source_chain: str) -> list[Product]:
    """Load all products that have a listing from source_chain."""
    stmt = (
        select(Product)
        .join(ProductRetailerListing, ProductRetailerListing.product_id == Product.id)
        .where(ProductRetailerListing.external_source == source_chain)
    )
    return list(session.execute(stmt).scalars().all())


def load_candidates(session: Session, exclude_sources: list[str]) -> list[Product]:
    """Load all products that do NOT have any listing from the excluded source chains."""
    # A product is a candidate if none of its listings come from the excluded chains.
    excluded_ids_subquery = (
        select(ProductRetailerListing.product_id)
        .where(ProductRetailerListing.external_source.in_(exclude_sources))
        .scalar_subquery()
    )
    stmt = select(Product).where(Product.id.not_in(excluded_ids_subquery))
    return list(session.execute(stmt).scalars().all())


def merge_into(session: Session, duplicate: Product, canonical: Product, dry_run: bool) -> None:
    """Re-point all references from duplicate → canonical, then delete duplicate.

    Delegates to _merge_products so every write-mode merge is audited in
    product_merge_log and follows the same GTIN propagation / candidate
    invalidation logic as the admin UI and the AI dedup stream.
    """
    if dry_run:
        print(
            f"  [DRY] would merge '{duplicate.name}' ({str(duplicate.id)[:8]})"
            f" → '{canonical.name}' ({str(canonical.id)[:8]})"
        )
        return

    from app.routers.admin import _merge_products

    _merge_products(session, str(canonical.id), str(duplicate.id), reviewed_by="script")


def run_backfill(session: Session, source_chain: str, dry_run: bool) -> tuple[int, int]:
    """
    Returns (checked, merged) counts for one source chain.
    """
    source_products = load_source_products(session, source_chain)
    if not source_products:
        print(f"  No products found for {source_chain}, skipping.")
        return 0, 0

    # Candidates = everything NOT from this chain
    candidates = load_candidates(session, [source_chain])
    if not candidates:
        print("  No candidate products to match against.")
        return len(source_products), 0

    # Filter candidates to those with meaningful names (≥15 normalized chars).
    # Very short names like "Chocolate", "Ring", "Can 375ml" cause false positives
    # because one shared word dominates the fuzzy score.
    MIN_CANDIDATE_LEN = 15
    filtered_candidates: list[Product] = []
    filtered_names: list[str] = []
    for p in candidates:
        n = _matcher.normalize_name(p.name)
        if n and len(n) >= MIN_CANDIDATE_LEN:
            filtered_candidates.append(p)
            filtered_names.append(n)

    # Build set of non-source-chain external_sources per source product.
    # If a source product already has a listing in the candidate's chain it is
    # already represented there — skip to avoid false cross-chain merges.
    src_product_ids = [sp.id for sp in source_products]  # UUID objects (not str) for IN clause
    _extra_rows = session.execute(
        select(ProductRetailerListing.product_id, ProductRetailerListing.external_source)
        .where(ProductRetailerListing.product_id.in_(src_product_ids))
        .where(ProductRetailerListing.external_source != source_chain)
    ).all()
    src_extra_chains: dict[str, set[str]] = {}
    for _pid, _ext_src in _extra_rows:
        src_extra_chains.setdefault(str(_pid), set()).add(_ext_src)

    checked = 0
    merged = 0

    for src_product in source_products:
        checked += 1
        src_id = str(src_product.id)

        # Never touch permanently-excluded conflict products.
        if src_id in EXCLUDED_PRODUCT_IDS:
            continue

        normalized = _matcher.normalize_name(src_product.name)
        if not normalized or len(normalized) < MIN_CANDIDATE_LEN:
            continue

        # Use extract(limit=10) rather than extractOne so that if the top candidate
        # fails the size guard we can try the next-best match (Issue #2 from PR review).
        results = fuzzy_extract(normalized, filtered_names, score_cutoff=MATCH_THRESHOLD, limit=10)
        if not results:
            continue

        # Iterate candidates in score order until one passes all guards.
        best_size_skip: tuple | None = None  # (canonical, src_size, cand_size, score)
        chosen: tuple | None = None  # (idx, score, canonical, size_is_null)

        for _cand_name, score, idx in results:
            canonical = filtered_candidates[idx]
            cand_id = str(canonical.id)

            if cand_id in EXCLUDED_PRODUCT_IDS or canonical.id == src_product.id:
                continue

            # Brand veto: never merge across a clear brand conflict.
            if brands_conflict(src_product.brand, canonical.brand):
                print(
                    f"  SKIP (brand conflict): '{src_product.name}' [{src_product.brand}] "
                    f"vs '{canonical.name}' [{canonical.brand}]"
                )
                continue

            # Multi-chain guard: skip if source product already has a listing in
            # one of the candidate's chains — it is already represented there.
            extra = src_extra_chains.get(src_id)
            if extra:
                cand_chains = set(
                    session.execute(
                        select(ProductRetailerListing.external_source).where(
                            ProductRetailerListing.product_id == canonical.id
                        )
                    )
                    .scalars()
                    .all()
                )
                if extra & cand_chains:
                    continue

            # Package-size guard: skip when both sizes are known and differ.
            src_size = src_product.package_size_norm
            cand_size = canonical.package_size_norm
            if src_size and cand_size and src_size != cand_size:
                # Record the best size-conflict skip for logging, then try next.
                if best_size_skip is None:
                    best_size_skip = (canonical, src_size, cand_size, score)
                continue

            # All guards passed.
            chosen = (idx, score, canonical, not (src_size and cand_size))
            break

        if chosen is None:
            # No valid candidate — log the best size-conflict hit if there was one.
            if best_size_skip is not None:
                can, s_size, c_size, s = best_size_skip
                print(
                    f"  SKIP (size conflict): '{src_product.name}' [{s_size}] "
                    f"vs '{can.name}' [{c_size}] (score={s:.0f})"
                )
            continue

        idx, score, canonical, size_is_null = chosen
        size_note = (
            " [SIZE UNVERIFIED — one side missing package_size_norm]" if size_is_null else ""
        )
        print(
            f"  MATCH ({score:.0f}): '{src_product.name}' [{source_chain}] → '{canonical.name}'{size_note}"
        )
        # In write mode, skip candidates without confirmed sizes on both sides.
        # A missing package_size_norm means we cannot rule out a size conflict,
        # so these require explicit human approval before any irreversible merge.
        # In dry-run the candidates are still reported for visibility.
        if size_is_null and not dry_run:
            print(
                f"  HOLD (write-mode, size unverified): '{src_product.name}' — "
                "re-run after package_size_norm is populated on both products."
            )
            continue
        merge_into(session, src_product, canonical, dry_run)
        merged += 1

        if not dry_run:
            # Remove from candidates list so it can't be matched again
            filtered_candidates.pop(idx)
            filtered_names.pop(idx)

        if checked % 100 == 0:
            print(f"  ... {checked}/{len(source_products)} checked, {merged} merged so far")
            if not dry_run:
                session.commit()

    if not dry_run:
        session.commit()

    return checked, merged


def main():
    parser = argparse.ArgumentParser(description="Backfill cross-chain product dedup")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without writing")
    parser.add_argument("--chain", help="Only process one source chain (e.g. catalog_aldi)")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)

    chains = [args.chain] if args.chain else SOURCE_CHAINS

    with Session(engine) as session:
        total_checked = 0
        total_merged = 0

        for chain in chains:
            print(f"\n{'='*60}")
            print(f"Processing {chain} ({'DRY RUN' if args.dry_run else 'LIVE'})")
            print(f"{'='*60}")
            checked, merged = run_backfill(session, chain, args.dry_run)
            total_checked += checked
            total_merged += merged
            print(f"  Done: {checked} checked, {merged} merged")

    print(f"\n{'='*60}")
    print(f"TOTAL: {total_checked} checked, {total_merged} merged")
    if args.dry_run:
        print("(dry run — no changes written)")


if __name__ == "__main__":
    main()

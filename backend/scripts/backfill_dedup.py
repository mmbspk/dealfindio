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

from rapidfuzz.process import extractOne
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Product
from app.models.product_retailer_listing import ProductRetailerListing
from app.services.ai_dedup import propagate_gtin_on_merge
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
    """Re-point all references from duplicate → canonical, then delete duplicate."""
    dup_id = str(duplicate.id)
    can_id = str(canonical.id)

    if dry_run:
        print(f"  [DRY] would merge '{duplicate.name}' ({dup_id[:8]}) → '{canonical.name}' ({can_id[:8]})")
        return

    # Propagate a valid EAN-13 GTIN from the dropped record to the survivor
    # before the DELETE removes it.
    propagate_gtin_on_merge(session, can_id, dup_id)

    # Re-point price_reports
    session.execute(
        text("UPDATE price_reports SET product_id = :can WHERE product_id = :dup"),
        {"can": can_id, "dup": dup_id},
    )
    # Re-point watchlist items (skip if user already watches canonical in same list)
    session.execute(
        text(
            """
            UPDATE user_watchlist SET product_id = :can
            WHERE product_id = :dup
              AND NOT EXISTS (
                SELECT 1 FROM user_watchlist u2
                WHERE u2.product_id = :can
                  AND u2.user_id = user_watchlist.user_id
                  AND (u2.list_id = user_watchlist.list_id OR (u2.list_id IS NULL AND user_watchlist.list_id IS NULL))
              )
        """
        ),
        {"can": can_id, "dup": dup_id},
    )
    # Delete any remaining watchlist rows that would conflict
    session.execute(
        text("DELETE FROM user_watchlist WHERE product_id = :dup"),
        {"dup": dup_id},
    )
    # Delete the duplicate product row
    session.execute(
        text("DELETE FROM products WHERE id = :dup"),
        {"dup": dup_id},
    )
    session.flush()


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

        result = extractOne(normalized, filtered_names, score_cutoff=MATCH_THRESHOLD)
        if result is None:
            continue

        _name, score, idx = result
        canonical = filtered_candidates[idx]

        if str(canonical.id) in EXCLUDED_PRODUCT_IDS:
            continue

        if canonical.id == src_product.id:
            continue  # already the same row

        # Brand veto: never merge across a clear brand conflict (e.g. Kirks vs
        # Fanta both named "Orange Soft Drink").
        if brands_conflict(src_product.brand, canonical.brand):
            print(
                f"  SKIP (brand conflict): '{src_product.name}' [{src_product.brand}] "
                f"vs '{canonical.name}' [{canonical.brand}]"
            )
            continue

        # Package-size guard: if both products have a normalised package size and
        # they differ, they are different products even when names match closely.
        # (e.g. "Coke 375mL" norm="375mL" vs "Coke 2L" norm="2L" — both normalise
        # to "coke" after FuzzyMatcher strips size tokens.)
        src_size = src_product.package_size_norm
        cand_size = canonical.package_size_norm
        if src_size and cand_size and src_size != cand_size:
            print(
                f"  SKIP (size conflict): '{src_product.name}' [{src_size}] "
                f"vs '{canonical.name}' [{cand_size}] (score={score:.0f})"
            )
            continue

        print(
            f"  MATCH ({score:.0f}): '{src_product.name}' [{source_chain}] → '{canonical.name}'"
        )
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

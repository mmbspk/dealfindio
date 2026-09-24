"""
One-shot backfill: merge product rows that belong to the same real-world item
but were created by different chain crawlers before the cross-chain fuzzy-match
fix was applied.

Strategy:
  For each "source" chain (aldi, coles) that has no GTINs:
    - Load all its products
    - For each product, run the same fuzzy-name match against products from
      OTHER chains (same logic as catalog_ingest._match_product)
    - If a match is found, re-point all price_reports / watchlist rows from the
      duplicate to the canonical row, then delete the duplicate

Safe to re-run: already-merged rows are skipped because they no longer exist.
"""

from __future__ import annotations

import argparse
import sys
import os

# Allow running from repo root or backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "backfill-script")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from rapidfuzz.process import extractOne

from app.config import get_settings
from app.models import Product, PriceReport
from app.models.watchlist import UserWatchlist
from app.services.product_dedup import FuzzyMatcher, brands_conflict

_matcher = FuzzyMatcher()
# Use a tighter threshold for backfill — 85 produces false positives when one
# shared word (e.g. "Chocolate", "Almonds") dominates the score across long names.
MATCH_THRESHOLD = 92

# Which chains to treat as "needs merging" — i.e. they have no GTINs
# so their products may duplicate rows from gtin-bearing chains.
SOURCE_CHAINS = ["catalog_aldi", "catalog_coles"]
# Chains whose products are the preferred canonical rows (have GTINs)
CANONICAL_CHAINS = ["catalog_woolworths"]


def load_candidates(session: Session, exclude_sources: list[str]) -> list[Product]:
    """Load all products NOT from the source chains as merge targets."""
    return list(
        session.query(Product)
        .filter(
            ~Product.external_source.in_(exclude_sources)
            | Product.external_source.is_(None)
        )
        .all()
    )


def merge_into(session: Session, duplicate: Product, canonical: Product, dry_run: bool) -> None:
    """Re-point all references from duplicate → canonical, then delete duplicate."""
    dup_id = duplicate.id
    can_id = canonical.id

    if dry_run:
        print(f"  [DRY] would merge '{duplicate.name}' ({dup_id}) → '{canonical.name}' ({can_id})")
        return

    # Re-point price_reports
    session.execute(
        text("UPDATE price_reports SET product_id = :can WHERE product_id = :dup"),
        {"can": can_id, "dup": dup_id},
    )
    # Re-point watchlist items (skip if user already watches canonical in same list)
    session.execute(
        text("""
            UPDATE user_watchlist SET product_id = :can
            WHERE product_id = :dup
              AND NOT EXISTS (
                SELECT 1 FROM user_watchlist u2
                WHERE u2.product_id = :can
                  AND u2.user_id = user_watchlist.user_id
                  AND (u2.list_id = user_watchlist.list_id OR (u2.list_id IS NULL AND user_watchlist.list_id IS NULL))
              )
        """),
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
    source_products = (
        session.query(Product)
        .filter(Product.external_source == source_chain)
        .all()
    )
    if not source_products:
        print(f"  No products found for {source_chain}, skipping.")
        return 0, 0

    # Candidates = everything NOT from this chain
    candidates = load_candidates(session, [source_chain])
    if not candidates:
        print(f"  No candidate products to match against.")
        return len(source_products), 0

    # Filter candidates to those with meaningful names (≥15 normalized chars).
    # Very short Coles names like "Chocolate", "Ring", "Can 375ml" cause false positives
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
        normalized = _matcher.normalize_name(src_product.name)
        if not normalized or len(normalized) < MIN_CANDIDATE_LEN:
            continue

        result = extractOne(normalized, filtered_names, score_cutoff=MATCH_THRESHOLD)
        if result is None:
            continue

        _name, score, idx = result
        canonical = filtered_candidates[idx]

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

        print(f"  MATCH ({score:.0f}): '{src_product.name}' [{source_chain}] → '{canonical.name}' [{canonical.external_source}]")
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

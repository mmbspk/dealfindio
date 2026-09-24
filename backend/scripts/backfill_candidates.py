"""
One-shot backfill: scan existing products and write product_match_candidates rows
for cross-chain pairs in the 92–99% fuzzy-score band that haven't been merged yet
and don't already have a candidate row.

Run once after the product_match_candidates migration. Safe to re-run — skips
pairs that already have a candidate row.

Usage:
    python3 scripts/backfill_candidates.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "backfill-script")

from rapidfuzz.process import extractOne
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Product
from app.models.product_match import ProductMatchCandidate
from app.services.product_dedup import FuzzyMatcher

_matcher = FuzzyMatcher()

# Chains that don't use GTINs — their products may fuzzy-match canonical rows
SOURCE_CHAINS = ["catalog_aldi", "catalog_coles"]
# Canonical chains (have GTINs) — preferred targets for matching
CANONICAL_CHAINS = ["catalog_woolworths"]

# Fuzzy match band: [LOW, HIGH). 100+ would be an exact merge (already done).
SCORE_LOW = 92.0
SCORE_HIGH = 100.0
MIN_CANDIDATE_LEN = 15


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill product_match_candidates")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without writing")
    parser.add_argument("--limit", type=int, default=0, help="Max candidates to write (0 = unlimited)")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL, echo=False)

    with Session(engine) as session:
        # Load all canonical products as potential match targets
        canonical = list(session.execute(
            select(Product).where(Product.external_source.in_(CANONICAL_CHAINS))
        ).scalars().all())
        print(f"Loaded {len(canonical)} canonical products (woolworths)")

        # Build filtered name list for rapidfuzz
        filtered_canonical = []
        canonical_names = []
        for p in canonical:
            n = _matcher.normalize_name(p.name)
            if n and len(n) >= MIN_CANDIDATE_LEN:
                filtered_canonical.append(p)
                canonical_names.append(n)
        print(f"  → {len(filtered_canonical)} after min-length filter")

        # Load existing candidate pairs to skip already-proposed ones
        existing_pairs: set[tuple] = set()
        for row in session.execute(select(ProductMatchCandidate)).scalars().all():
            existing_pairs.add((str(row.product_a_id), str(row.product_b_id)))
        print(f"Loaded {len(existing_pairs)} existing candidate pairs (will skip)")

        # Scan source-chain products
        source_products = list(session.execute(
            select(Product).where(Product.external_source.in_(SOURCE_CHAINS))
        ).scalars().all())
        print(f"Scanning {len(source_products)} source products ({', '.join(SOURCE_CHAINS)})")

        written = 0
        skipped_existing = 0
        skipped_no_match = 0
        skipped_exact = 0

        for sp in source_products:
            if args.limit and written >= args.limit:
                break

            norm = _matcher.normalize_name(sp.name)
            if not norm or len(norm) < MIN_CANDIDATE_LEN:
                skipped_no_match += 1
                continue

            best = extractOne(norm, canonical_names, score_cutoff=SCORE_LOW)
            if best is None:
                skipped_no_match += 1
                continue

            _name, score, idx = best
            if score >= SCORE_HIGH:
                # Would have been auto-merged — skip (backfill_dedup already handled these)
                skipped_exact += 1
                continue

            matched = filtered_canonical[idx]
            a_id, b_id = sorted([str(sp.id), str(matched.id)])
            if (a_id, b_id) in existing_pairs:
                skipped_existing += 1
                continue

            print(f"  {score:.1f}%  [{sp.external_source}] {sp.name!r}  ←→  [{matched.external_source}] {matched.name!r}")

            if not args.dry_run:
                session.add(ProductMatchCandidate(
                    product_a_id=a_id,
                    product_b_id=b_id,
                    score=score,
                    status="pending",
                    proposed_by="auto",
                ))
                existing_pairs.add((a_id, b_id))
                written += 1

                if written % 200 == 0:
                    session.flush()
            else:
                written += 1

        if not args.dry_run:
            session.commit()
            print(f"\nDone: {written} candidates written, "
                  f"{skipped_existing} skipped (already existed), "
                  f"{skipped_exact} skipped (exact/already merged), "
                  f"{skipped_no_match} skipped (no match)")
        else:
            print(f"\nDry-run: {written} would be written, "
                  f"{skipped_existing} already exist, "
                  f"{skipped_exact} are exact (already merged), "
                  f"{skipped_no_match} had no match")


if __name__ == "__main__":
    main()

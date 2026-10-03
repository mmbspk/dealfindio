"""
Chain-agnostic image backfill via the Open Food Facts enrichment pipeline.

Finds products missing a self-hosted image (``image_url IS NULL``) for the
given chain(s) and runs the OFF enrichment pipeline against them directly
(no Celery broker required — runs in-process).

Usage:
    python scripts/backfill_images.py --chain aldi
    python scripts/backfill_images.py --chain all --limit 200
    python scripts/backfill_images.py --chain coles --dry-run

If ``--chain all`` is given, all five supported chains are processed in order.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "backfill-script")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SUPPORTED_CHAINS = ("coles", "woolworths", "aldi", "iga", "drakes")
_DEFAULT_LIMIT = 500


def _run_enrichment(chain: str | None, limit: int, dry_run: bool) -> dict:
    """Run the OFF enrichment pipeline synchronously for one chain (or all)."""
    from app.workers.image_tasks import _find_products_missing_images, _enrich_all

    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL, echo=False)
    session = Session(engine)
    try:
        products = _find_products_missing_images(session, chain, limit)
        candidates = len(products)
        log.info("Chain=%s: %d candidates found (limit=%d)", chain or "all", candidates, limit)
        if not candidates:
            return {"chain": chain, "candidates": 0, "enriched": 0, "skipped": 0}

        if dry_run:
            log.info("[dry-run] Would process %d products for chain=%s", candidates, chain or "all")
            return {"chain": chain, "candidates": candidates, "enriched": 0, "skipped": 0, "dry_run": True}

        targets = [(p.id, p.name, p.gtin) for p in products]
        results = asyncio.run(_enrich_all(targets))

        store = get_object_store()
        enriched = skipped = 0
        for product_id, enriched_image in results:
            if enriched_image is None:
                skipped += 1
                continue
            try:
                key = product_image_key(product_id, enriched_image.content_type)
                public_url = store.put_object(key, enriched_image.image_bytes, enriched_image.content_type)
            except Exception as exc:
                log.warning("Failed to store image for %s: %s", product_id, exc)
                skipped += 1
                continue
            product = session.get(Product, product_id)
            if product is None:
                skipped += 1
                continue
            # Write to pending review queue — not directly to image_url.
            product.off_image_url = public_url
            product.off_image_score = enriched_image.match_score
            product.off_image_off_code = enriched_image.off_code
            product.off_image_off_name = enriched_image.off_product_name
            product.off_image_status = "pending"
            log.info("  ⏳ %s (score=%.0f) → pending review", product.name, enriched_image.match_score)
            enriched += 1

        session.commit()
        log.info(
            "Chain=%s done: %d enriched, %d skipped of %d candidates",
            chain or "all", enriched, skipped, candidates,
        )
        return {"chain": chain, "candidates": candidates, "enriched": enriched, "skipped": skipped}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill product images via Open Food Facts")
    parser.add_argument(
        "--chain",
        default="all",
        choices=[*SUPPORTED_CHAINS, "all"],
        help="Chain to backfill (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        help=f"Max products per chain (default: {_DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count candidates without writing any images",
    )
    args = parser.parse_args()

    chains = list(SUPPORTED_CHAINS) if args.chain == "all" else [args.chain]
    totals = {"candidates": 0, "enriched": 0, "skipped": 0}

    for chain in chains:
        log.info("=== Processing chain: %s ===", chain)
        result = _run_enrichment(chain, args.limit, args.dry_run)
        totals["candidates"] += result.get("candidates", 0)
        totals["enriched"] += result.get("enriched", 0)
        totals["skipped"] += result.get("skipped", 0)

    log.info(
        "=== Backfill complete: %d candidates, %d enriched, %d skipped%s ===",
        totals["candidates"],
        totals["enriched"],
        totals["skipped"],
        " [dry-run]" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()

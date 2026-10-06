"""
Purge Woolworths products that were not seen in the most recent completed full crawl.

After adding the IsOnlineOnly filter to catalog_woolworths.py, run a fresh Woolworths
full crawl, then run this script to delete products that were skipped (online-only items,
delisted products, etc.).

Usage:
    poetry run python scripts/purge_woolworths_online_only.py            # dry run
    poetry run python scripts/purge_woolworths_online_only.py --execute  # actually delete
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://dealfindio:dealfindio_dev@localhost:5432/dealfindio_dev",
)


def main(execute: bool) -> None:
    engine = create_engine(DATABASE_URL)

    with engine.begin() as conn:
        # Find the most recent completed Woolworths full crawl.
        row = conn.execute(text("""
            SELECT id, run_started_at, products_found, products_new, products_updated
            FROM catalog_crawl_runs
            WHERE chain = 'woolworths'
              AND mode = 'full'
              AND status IN ('completed', 'completed_with_errors')
            ORDER BY run_started_at DESC
            LIMIT 1
        """)).fetchone()

        if row is None:
            print("No completed Woolworths full crawl found. Run one first.")
            sys.exit(1)

        run_id, cutoff, found, new_, updated = row
        print(f"Reference crawl: {run_id}")
        print(f"  Started at:      {cutoff}")
        print(f"  products_found:  {found:,}  (new={new_:,}  updated={updated:,})")
        print()

        # Products not seen in (or before) that crawl.
        stale = conn.execute(text("""
            SELECT id, name, external_id, catalog_last_seen_at
            FROM products
            WHERE external_source = 'catalog_woolworths'
              AND (catalog_last_seen_at IS NULL OR catalog_last_seen_at < :cutoff)
        """), {"cutoff": cutoff}).fetchall()

        if not stale:
            print("No stale Woolworths products found — nothing to delete.")
            return

        stale_ids = [str(r[0]) for r in stale]
        print(f"Stale products to purge: {len(stale_ids):,}")
        print("Sample:")
        for r in stale[:10]:
            print(f"  {r[1]!r:50s}  external_id={r[2]}  last_seen={r[3]}")
        if len(stale) > 10:
            print(f"  ... and {len(stale) - 10:,} more")
        print()

        if not execute:
            print("DRY RUN — pass --execute to delete.")
            return

        # ── Delete from child tables with NO ACTION FK first ────────────────
        ids_literal = ", ".join(f"'{i}'" for i in stale_ids)

        pred_del = conn.execute(text(f"""
            DELETE FROM price_predictions WHERE product_id::text IN ({ids_literal})
        """)).rowcount
        print(f"Deleted price_predictions rows: {pred_del:,}")

        report_del = conn.execute(text(f"""
            DELETE FROM price_reports WHERE product_id::text IN ({ids_literal})
        """)).rowcount
        print(f"Deleted price_reports rows: {report_del:,}")

        watch_del = conn.execute(text(f"""
            DELETE FROM user_watchlist WHERE product_id::text IN ({ids_literal})
        """)).rowcount
        print(f"Deleted user_watchlist rows: {watch_del:,}")

        # ── Delete products (cascades to product_match_candidates) ──────────
        prod_del = conn.execute(text(f"""
            DELETE FROM products WHERE id::text IN ({ids_literal})
        """)).rowcount
        print(f"Deleted products rows: {prod_del:,}")

        print()
        print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Actually delete (default: dry run)")
    args = parser.parse_args()
    main(execute=args.execute)

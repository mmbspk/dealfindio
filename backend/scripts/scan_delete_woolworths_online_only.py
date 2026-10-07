"""
Scan Woolworths categories and mark online-only products in the DB.

For each category, fetches all pages from the Woolworths API and checks the
IsOnlineOnly flag per product. Products that are online-only are marked in the
products table (is_online_only = TRUE). Marketplace products are skipped as
before (they're filtered from DB entirely by IsMarketProduct check).

After marking, optionally runs the DELETE to purge them from all tables.

Usage:
    # Mark only (safe preview — shows count + sample before touching DB):
    poetry run python scripts/scan_delete_woolworths_online_only.py

    # Mark + delete in one go:
    poetry run python scripts/scan_delete_woolworths_online_only.py --execute
"""

import argparse
import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import create_engine, text

from scrapers.catalog_base import CatalogStoreConfig, get_scraper_proxy
from scrapers.catalog_woolworths import (
    _BROWSE_URL,
    _JSON_HEADERS,
    WoolworthsCatalogScraper,
    _extract_products,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://dealfindio:dealfindio_dev@localhost:5432/dealfindio_dev",
)

_PAGE_DELAY = 0.25  # seconds between page requests


async def collect_online_only_ids() -> set[str]:
    """Return external_ids of all Woolworths products where IsOnlineOnly=True."""
    import fakeredis

    cfg = CatalogStoreConfig(
        chain="woolworths",
        min_delay_s=0,
        max_delay_s=0,
        burst_limit=100,
        page_size=36,
        max_pages_per_category=999,
        enabled=True,
    )
    scraper = WoolworthsCatalogScraper("woolworths", cfg, redis_client=fakeredis.FakeStrictRedis())

    print("Bootstrapping Woolworths...", flush=True)
    auth = await scraper._get_or_refresh_auth()
    session_id = scraper._proxy_session_id
    cookies = auth.get("cookies", {})
    print(f"  Bootstrap OK (session_id={session_id})", flush=True)

    categories = await scraper.discover_categories()
    print(f"  {len(categories)} categories discovered\n", flush=True)

    online_only_ids: set[str] = set()

    async with httpx.AsyncClient(
        headers=_JSON_HEADERS,
        cookies=cookies,
        follow_redirects=True,
        timeout=30.0,
        proxies=get_scraper_proxy(session_id),
    ) as client:
        for cat in categories:
            url_name = cat.url or cat.id
            page = 1
            cat_online = 0
            cat_total = 0

            while True:
                params = {
                    "pageNumber": page,
                    "pageSize": 36,
                    "sortType": "TraderRelevance",
                    "url": f"/shop/browse/{url_name}",
                    "location": f"/shop/browse/{url_name}",
                    "formatObject": json.dumps({"name": cat.name}, separators=(",", ":")),
                    "isSpecial": "false",
                    "categoryId": cat.id,
                }
                try:
                    resp = await client.get(_BROWSE_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    print(f"  [{cat.name}] page {page} error: {exc}", flush=True)
                    break

                products = _extract_products(data)
                if not products:
                    break

                total_records = data.get("TotalRecordCount") or 0
                if not total_records and data.get("Bundles"):
                    total_records = data["Bundles"][0].get("TotalRecordCount", 0)

                for item in products:
                    stockcode = item.get("Stockcode")
                    if not stockcode:
                        continue
                    if item.get("IsMarketProduct"):
                        continue  # already excluded at category level
                    cat_total += 1
                    if item.get("IsOnlineOnly"):
                        online_only_ids.add(str(stockcode))
                        cat_online += 1

                total_seen = (page - 1) * 36 + len(products)
                if total_records and total_seen >= total_records:
                    break
                page += 1
                await asyncio.sleep(_PAGE_DELAY)

            print(
                f"  [{cat.name}] {cat_total} products  →  {cat_online} online-only",
                flush=True,
            )

    print(f"\nTotal online-only external_ids found: {len(online_only_ids):,}", flush=True)
    return online_only_ids


def mark_and_delete(online_only_ids: set[str], execute: bool) -> None:
    if not online_only_ids:
        print("No online-only products found — nothing to do.")
        return

    engine = create_engine(DATABASE_URL)

    with engine.begin() as conn:
        # Mark matching DB products
        marked = conn.execute(
            text(
                """
            UPDATE products
            SET is_online_only = TRUE
            WHERE external_source = 'catalog_woolworths'
              AND external_id = ANY(:ids)
        """
            ),
            {"ids": list(online_only_ids)},
        ).rowcount
        print(f"Marked {marked:,} existing DB products as is_online_only=TRUE")

        # Preview what would be deleted
        sample = conn.execute(
            text(
                """
            SELECT name, external_id, category
            FROM products
            WHERE external_source = 'catalog_woolworths' AND is_online_only = TRUE
            LIMIT 10
        """
            )
        ).fetchall()
        total_marked = conn.execute(
            text(
                """
            SELECT COUNT(*) FROM products
            WHERE external_source = 'catalog_woolworths' AND is_online_only = TRUE
        """
            )
        ).scalar()

        print(f"\nProducts flagged is_online_only=TRUE in DB: {total_marked:,}")
        print("Sample:")
        for r in sample:
            print(f"  {r[0]!r:50s}  external_id={r[1]}  cat={r[2]}")

        if not execute:
            print("\nDRY RUN — re-run with --execute to delete flagged products.")
            return

        # Delete from child tables (NO ACTION FK) then products (cascades match_candidates)
        stale_ids_rows = conn.execute(
            text(
                """
            SELECT id FROM products
            WHERE external_source = 'catalog_woolworths' AND is_online_only = TRUE
        """
            )
        ).fetchall()
        stale_ids = [str(r[0]) for r in stale_ids_rows]

        ids_literal = ", ".join(f"'{i}'" for i in stale_ids)
        pred = conn.execute(
            text(f"DELETE FROM price_predictions WHERE product_id::text IN ({ids_literal})")
        ).rowcount
        rep = conn.execute(
            text(f"DELETE FROM price_reports     WHERE product_id::text IN ({ids_literal})")
        ).rowcount
        wl = conn.execute(
            text(f"DELETE FROM user_watchlist    WHERE product_id::text IN ({ids_literal})")
        ).rowcount
        prod = conn.execute(
            text(f"DELETE FROM products          WHERE id::text         IN ({ids_literal})")
        ).rowcount

        print(
            f"\nDeleted: {prod:,} products  {pred:,} predictions  {rep:,} price_reports  {wl:,} watchlist"
        )
        print("Done.")


async def main(execute: bool) -> None:
    online_only_ids = await collect_online_only_ids()
    mark_and_delete(online_only_ids, execute=execute)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute", action="store_true", help="Delete after marking (default: dry run)"
    )
    args = parser.parse_args()
    asyncio.run(main(execute=args.execute))

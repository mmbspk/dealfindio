"""
Backfill image_url for IGA products that have image_url = NULL.

Fetches all 13 nav-level categories from the IGA storefront BFF, collects
every product's image dict, and UPDATEs only null-image rows by external_id.
Does NOT trigger a full catalog crawl or touch prices/stock/categories.

Usage:
    python scripts/backfill_iga_images.py [--dry-run] [--store-id 32600]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "backfill-script")

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_BASE = "https://www.igashop.com.au/api/storefront"
_SHOPPING_MODE = "11111111-1111-1111-1111-111111111111"
_PAGE_SIZE = 48
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (dealfindio-backfill)",
    "x-shopping-mode": _SHOPPING_MODE,
}


async def _get_nav_categories(client: httpx.AsyncClient, store_id: str) -> list[dict]:
    url = f"{_BASE}/stores/{store_id}/categoryHierarchy"
    r = await client.get(url)
    r.raise_for_status()
    data = r.json()
    # Unwrap outer list/dict to find the Grocery root's children
    if isinstance(data, dict):
        data = data.get("children") or data.get("categories") or data.get("items") or []
    if not isinstance(data, list):
        return []
    nav_cats: list[dict] = []
    for top in data:
        if not isinstance(top, dict):
            continue
        for nav in (top.get("children") or []):
            if not isinstance(nav, dict):
                continue
            cat_id = str(
                nav.get("identifier")
                or nav.get("retailerCategoryId")
                or nav.get("id")
                or nav.get("categoryId")
                or ""
            )
            name = (
                nav.get("displayName") or nav.get("name") or nav.get("categoryName") or ""
            ).strip()
            if cat_id and name:
                nav_cats.append({"id": cat_id, "name": name})
    return nav_cats


async def _fetch_all_images(
    client: httpx.AsyncClient, store_id: str, categories: list[dict]
) -> dict[str, str]:
    """Return mapping of external_id → image_url for all products in all categories."""
    images: dict[str, str] = {}
    for cat in categories:
        cat_id = cat["id"]
        skip = 0
        log.info("Fetching images for category: %s (id=%s)", cat["name"], cat_id)
        while True:
            url = f"{_BASE}/stores/{store_id}/categories/{cat_id}/search"
            r = await client.get(url, params={"take": _PAGE_SIZE, "skip": skip})
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", "10"))
                log.warning("Rate limited, waiting %.0fs", wait)
                await asyncio.sleep(wait)
                continue
            if r.status_code != 200:
                log.warning("HTTP %d for cat %s skip=%d, skipping", r.status_code, cat_id, skip)
                break
            data = r.json()
            items = data.get("items") or []
            total = data.get("total") or 0
            for item in items:
                sku = str(item.get("sku") or item.get("id") or item.get("productId") or "")
                if not sku:
                    continue
                image_raw = item.get("image") or {}
                img_url: str | None = None
                if isinstance(image_raw, str) and image_raw.startswith("http"):
                    img_url = image_raw
                elif isinstance(image_raw, dict):
                    img_url = (
                        image_raw.get("default")
                        or image_raw.get("cell")
                        or image_raw.get("details")
                    )
                if img_url:
                    images[sku] = img_url
            skip += len(items)
            if skip >= total or not items:
                break
        log.info(
            "  → %d images collected so far (category done: %s)", len(images), cat["name"]
        )
    return images


def _apply_backfill(session: Session, images: dict[str, str], dry_run: bool) -> int:
    if not images:
        log.info("No images to backfill.")
        return 0

    # Only update products that are missing an image
    null_ids_rows = session.execute(
        text(
            "SELECT external_id FROM products "
            "WHERE external_source = 'catalog_iga' AND image_url IS NULL"
        )
    ).fetchall()
    null_ids = {row[0] for row in null_ids_rows}
    log.info("Products with null image_url: %d", len(null_ids))

    to_update = {sku: url for sku, url in images.items() if sku in null_ids}
    log.info("Matched %d products with images from API", len(to_update))

    if dry_run:
        log.info("[dry-run] Would update %d rows.", len(to_update))
        return len(to_update)

    batch: list[dict] = [
        {"sku": sku, "url": url} for sku, url in to_update.items()
    ]
    if batch:
        session.execute(
            text(
                "UPDATE products SET image_url = :url, image_source = 'catalog_iga' "
                "WHERE external_source = 'catalog_iga' AND external_id = :sku "
                "AND image_url IS NULL"
            ),
            batch,
        )
        session.commit()
        log.info("Updated %d products with image URLs.", len(batch))
    return len(batch)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill IGA product images")
    parser.add_argument("--dry-run", action="store_true", help="Print counts without writing")
    parser.add_argument("--store-id", default="32600", help="IGA store ID (default: 32600)")
    args = parser.parse_args()

    from app.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL, echo=False)
    session = Session(engine)

    async with httpx.AsyncClient(headers=_HEADERS, timeout=30.0, follow_redirects=True) as client:
        log.info("Fetching nav-level categories from store %s ...", args.store_id)
        categories = await _get_nav_categories(client, args.store_id)
        log.info("Found %d nav categories: %s", len(categories), [c["name"] for c in categories])

        if not categories:
            log.error("No categories found — check store ID or API availability.")
            return

        log.info("Fetching product images across all categories ...")
        images = await _fetch_all_images(client, args.store_id, categories)
        log.info("Total distinct product images fetched: %d", len(images))

    updated = _apply_backfill(session, images, dry_run=args.dry_run)
    session.close()
    log.info("Done. %d products %s.", updated, "would be updated" if args.dry_run else "updated")


if __name__ == "__main__":
    asyncio.run(main())

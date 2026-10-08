"""
Batch helpers for reading per-product metadata from ProductRetailerListing.

Used by routers to apply the #49c read migration: retailer-specific fields
(image, package size, stock status) are sourced from ProductRetailerListing
rather than the legacy columns on Product.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product_retailer_listing import ProductRetailerListing


def get_listing_enrichment_map(
    db: Session,
    product_ids: list[UUID],
) -> dict[UUID, dict]:
    """Batch-fetch best listing values for a set of products.

    Returns a dict keyed by product_id, present ONLY for products that have at
    least one listing.  Each value dict contains:
      external_image_url  — most-recently-seen non-null listing image
      package_size_raw    — most-recently-seen non-null package-size string
      any_in_stock        — True if any listing for this product is in-stock

    Ordering: catalog_last_seen_at DESC NULLS LAST, id ASC ensures the first
    non-null value seen per product is always the freshest one.
    """
    if not product_ids:
        return {}

    rows = db.execute(
        select(
            ProductRetailerListing.product_id,
            ProductRetailerListing.external_image_url,
            ProductRetailerListing.package_size_raw,
            ProductRetailerListing.in_stock,
        )
        .where(ProductRetailerListing.product_id.in_(product_ids))
        .order_by(
            ProductRetailerListing.catalog_last_seen_at.desc().nulls_last(),
            ProductRetailerListing.id.asc(),
        )
    ).all()

    result: dict[UUID, dict] = {}
    for row in rows:
        pid = row.product_id
        if pid not in result:
            result[pid] = {
                "external_image_url": None,
                "package_size_raw": None,
                "any_in_stock": False,
            }
        if row.in_stock:
            result[pid]["any_in_stock"] = True
        if result[pid]["external_image_url"] is None and row.external_image_url:
            result[pid]["external_image_url"] = row.external_image_url
        if result[pid]["package_size_raw"] is None and row.package_size_raw:
            result[pid]["package_size_raw"] = row.package_size_raw

    return result

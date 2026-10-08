"""
Batch helpers for reading per-product metadata from ProductRetailerListing.

Used by routers to apply the #49c read migration: retailer-specific fields
(image, package size, stock status) are sourced from ProductRetailerListing
rather than the legacy columns on Product.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Product, ProductRetailerListing


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


def get_listing_enrichment_by_chain_map(
    db: Session,
    product_ids: list[UUID],
) -> dict[tuple[UUID, str], dict]:
    """Batch-fetch best listing values per (product_id, chain) pair.

    Returns a dict keyed by (product_id, chain), present ONLY for pairs
    that have at least one listing.  Each value dict contains:
      external_image_url  — most-recently-seen non-null image for this (product, chain)
      package_size_raw    — most-recently-seen non-null package size for this (product, chain)
      any_in_stock        — True if any listing for this (product, chain) is in-stock

    Use this instead of get_listing_enrichment_map when the caller needs
    per-chain resolution (e.g. deal cards, which belong to a specific store chain).
    """
    if not product_ids:
        return {}

    rows = db.execute(
        select(
            ProductRetailerListing.product_id,
            ProductRetailerListing.chain,
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

    result: dict[tuple, dict] = {}
    for row in rows:
        key = (row.product_id, row.chain)
        if key not in result:
            result[key] = {
                "external_image_url": None,
                "package_size_raw": None,
                "any_in_stock": False,
            }
        if row.in_stock:
            result[key]["any_in_stock"] = True
        if result[key]["external_image_url"] is None and row.external_image_url:
            result[key]["external_image_url"] = row.external_image_url
        if result[key]["package_size_raw"] is None and row.package_size_raw:
            result[key]["package_size_raw"] = row.package_size_raw

    return result


def in_stock_filter_clause(in_stock: bool):
    """SQLAlchemy WHERE clause for no-chain in_stock filter with missing-listing fallback.

    Without this, NOT EXISTS(listing with in_stock=True) incorrectly matches
    products that have zero listings, because NOT EXISTS of an empty set is True.

    Correct semantics:
      in_stock=True:  has a listing with in_stock=True
                      OR no listing exists AND Product.in_stock is True
      in_stock=False: has at least one listing AND none are in-stock
                      OR no listing exists AND Product.in_stock is False

    Both OR branches include a TODO(#49e): once legacy Product columns are
    dropped the no-listing branch can be removed entirely.
    """
    has_any_listing = (
        select(ProductRetailerListing.id)
        .where(ProductRetailerListing.product_id == Product.id)
        .exists()
    )
    has_in_stock_listing = (
        select(ProductRetailerListing.id)
        .where(
            ProductRetailerListing.product_id == Product.id,
            ProductRetailerListing.in_stock == True,  # noqa: E712
        )
        .exists()
    )

    if in_stock:
        return or_(
            has_in_stock_listing,
            and_(~has_any_listing, Product.in_stock == True),  # noqa: E712  # TODO(#49e)
        )
    else:
        return or_(
            and_(has_any_listing, ~has_in_stock_listing),
            and_(~has_any_listing, Product.in_stock == False),  # noqa: E712  # TODO(#49e)
        )

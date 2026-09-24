"""
Deals router — location-aware feed of current sale items.

GET /api/v1/deals/           — deals without geo filter
GET /api/v1/deals/feed       — same, alias used by the mobile app
Both accept optional lat/lon/radius_km to restrict to nearby stores.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import PriceReport, Product, Store
from app.services.config_store import get_runtime_typed
from app.services.geo import haversine_km

logger = logging.getLogger(__name__)

router = APIRouter()


def _fetch_deals(
    db: Session,
    lat: float | None,
    lon: float | None,
    radius_km: int,
    limit: int,
    days: int,
    chain: str | None = None,
    category: str | None = None,
    offset: int = 0,
) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Best (highest discount) on-sale price per product — one row per product_id
    best_subq_base = (
        select(
            PriceReport.product_id,
            func.max(PriceReport.sale_pct_off).label("max_pct_off"),
        )
        .join(Product, PriceReport.product_id == Product.id)
        .join(Store, PriceReport.store_id == Store.id)
        .where(
            and_(
                PriceReport.is_on_sale == True,
                PriceReport.created_at >= cutoff,
            )
        )
    )
    if chain:
        best_subq_base = best_subq_base.where(Store.chain == chain)
    if category:
        best_subq_base = best_subq_base.where(Product.category == category)
    best_per_product_subq = best_subq_base.group_by(PriceReport.product_id).subquery()

    outer = (
        select(PriceReport, Product, Store)
        .join(Product, PriceReport.product_id == Product.id)
        .join(Store, PriceReport.store_id == Store.id)
        .join(
            best_per_product_subq,
            and_(
                PriceReport.product_id == best_per_product_subq.c.product_id,
                PriceReport.sale_pct_off == best_per_product_subq.c.max_pct_off,
            ),
        )
        .where(Store.is_active == True)
    )
    if chain:
        outer = outer.where(Store.chain == chain)
    if category:
        outer = outer.where(Product.category == category)

    rows = db.execute(
        outer.distinct(PriceReport.product_id)
        .order_by(PriceReport.product_id, desc(PriceReport.created_at))
        .limit(max((offset + limit) * 4, 500))
    ).all()

    # Collect all qualifying deals (respects geo filter + dedup), then paginate
    all_deals: list[dict] = []
    seen_products: set = set()
    for price_report, product, store in rows:
        if product.id in seen_products:
            continue

        distance_km: float | None = None
        if lat is not None and lon is not None and store.lat is not None and store.lon is not None:
            distance_km = haversine_km(lat, lon, store.lat, store.lon)

        seen_products.add(product.id)
        all_deals.append(
            {
                "id": str(price_report.id),
                "product_id": str(product.id),
                "product_name": product.name,
                "brand": product.brand,
                "category": product.category,
                "image_url": product.image_url,
                "store_id": str(store.id),
                "store_name": store.name,
                "chain": store.chain,
                "suburb": store.suburb,
                "state": store.state,
                "price_aud": price_report.price_cents / 100,
                "was_price_aud": (
                    price_report.was_price_cents / 100 if price_report.was_price_cents else None
                ),
                "discount_pct": price_report.sale_pct_off,
                "is_verified": price_report.is_verified,
                "valid_from": price_report.valid_from.isoformat()
                if price_report.valid_from
                else None,
                "distance_km": round(distance_km, 2) if distance_km is not None else None,
            }
        )

    # Sort: when geo is provided sort by distance, else by discount
    if lat is not None and lon is not None:
        all_deals.sort(key=lambda d: (d["distance_km"] or 9999, -(d["discount_pct"] or 0)))
    else:
        all_deals.sort(key=lambda d: -(d["discount_pct"] or 0))

    return all_deals[offset : offset + limit]


@router.get("/")
async def list_deals(
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[int, Query(ge=1, le=100)] = 10,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    days: Annotated[int | None, Query(ge=1, le=90)] = None,
    chain: Annotated[str | None, Query(max_length=50)] = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
    db: Session = Depends(get_db),
) -> dict:
    try:
        effective_days = (
            days if days is not None else (get_runtime_typed("DEALS_FEED_DAYS", db) or 7)
        )
        deals = _fetch_deals(
            db, lat, lon, radius_km, limit, effective_days, chain, category, offset
        )
        return {"count": len(deals), "items": deals}
    except Exception as e:
        logger.error(f"Deals feed failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve deals",
        )


@router.get("/feed")
async def deals_feed(
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[int, Query(ge=1, le=100)] = 10,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    days: Annotated[int | None, Query(ge=1, le=90)] = None,
    chain: Annotated[str | None, Query(max_length=50)] = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        effective_days = (
            days if days is not None else (get_runtime_typed("DEALS_FEED_DAYS", db) or 7)
        )
        return _fetch_deals(db, lat, lon, radius_km, limit, effective_days, chain, category, offset)
    except Exception as e:
        logger.error(f"Deals feed failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve deals",
        )

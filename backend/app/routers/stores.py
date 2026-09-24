"""
Stores router with nearby search using PostGIS and deals endpoints.

Implements REST API for finding nearby stores, store details, and current deals
at specific store locations.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import PriceReport, Product, Store
from app.services.geo import haversine_km
from app.schemas.store import (
    DealResponse,
    NearbyStoresResponse,
    StoreDealsResponse,
    StoreDetailResponse,
    StoreResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Stores"])


@router.get("/nearby", response_model=NearbyStoresResponse)
async def get_nearby_stores(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    radius_km: Annotated[int, Query(ge=1, le=100)] = 10,
    chain: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    db: Session = Depends(get_db),
) -> NearbyStoresResponse:
    """
    Find stores near user location.

    Uses Haversine distance formula to find stores within radius.
    In production, would use PostGIS ST_DWithin for better performance.

    Query Parameters:
        lat: User latitude (-90 to 90)
        lon: User longitude (-180 to 180)
        radius_km: Search radius in kilometers (1-100, default 10)
        chain: Filter by grocery chain (optional)
        limit: Maximum results (1-100, default 20)

    Returns:
        NearbyStoresResponse: List of nearby stores with distances

    Raises:
        HTTPException: If parameters invalid or query fails
    """
    try:
        # Build base query
        query = select(Store).where(Store.is_active == True)

        if chain:
            query = query.where(Store.chain == chain)

        stores = db.execute(query).scalars().all()

        # Calculate distances using Haversine formula
        # (In production with PostGIS, filter in DB: ST_DWithin(location, point, radius*1000))
        stores_with_distance = []

        for store in stores:
            if store.lat is None or store.lon is None:
                continue

            distance_km = haversine_km(lat, lon, store.lat, store.lon)

            if distance_km <= radius_km:
                stores_with_distance.append((store, distance_km))

        # Sort by distance and apply limit
        stores_with_distance.sort(key=lambda x: x[1])
        stores_with_distance = stores_with_distance[:limit]

        store_responses = [
            StoreResponse(
                id=store.id,
                chain=store.chain,
                name=store.name,
                address=store.address,
                suburb=store.suburb,
                state=store.state,
                postcode=store.postcode,
                lat=store.lat,
                lon=store.lon,
                is_active=store.is_active,
                distance_km=distance_km,
            )
            for store, distance_km in stores_with_distance
        ]

        return NearbyStoresResponse(
            count=len(store_responses),
            stores=store_responses,
        )

    except Exception as e:
        logger.error(f"Failed to get nearby stores: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve nearby stores",
        )


@router.get("/{store_id}", response_model=StoreDetailResponse)
async def get_store(
    store_id: UUID,
    db: Session = Depends(get_db),
) -> StoreDetailResponse:
    """
    Get store detail and metadata.

    Path Parameters:
        store_id: UUID of the store

    Returns:
        StoreDetailResponse: Store details with product count and last update

    Raises:
        HTTPException: 404 if store not found
    """
    try:
        store = db.execute(
            select(Store).where(Store.id == store_id)
        ).scalar_one_or_none()

        if not store:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Store {store_id} not found",
            )

        # Count unique products with prices at this store
        product_count = db.execute(
            select(func.count(func.distinct(PriceReport.product_id))).where(
                PriceReport.store_id == store_id
            )
        ).scalar() or 0

        # Find last price update
        last_update = db.execute(
            select(func.max(PriceReport.created_at)).where(
                PriceReport.store_id == store_id
            )
        ).scalar()

        return StoreDetailResponse(
            id=store.id,
            chain=store.chain,
            name=store.name,
            address=store.address,
            suburb=store.suburb,
            state=store.state,
            postcode=store.postcode,
            lat=store.lat,
            lon=store.lon,
            is_active=store.is_active,
            product_count=product_count,
            last_price_update=last_update.isoformat() if last_update else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get store {store_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve store",
        )


@router.get("/{store_id}/deals", response_model=StoreDealsResponse)
async def get_store_deals(
    store_id: UUID,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    db: Session = Depends(get_db),
) -> StoreDealsResponse:
    """
    Get current deals and promotions at a store.

    Retrieves products on sale, sorted by discount percentage (highest first).

    Path Parameters:
        store_id: UUID of the store

    Query Parameters:
        days: Number of days back to search (1-30, default 7)
        limit: Maximum deals to return (1-500, default 50)

    Returns:
        StoreDealsResponse: Current deals and promotions

    Raises:
        HTTPException: 404 if store not found
    """
    try:
        # Verify store exists
        store = db.execute(
            select(Store).where(Store.id == store_id)
        ).scalar_one_or_none()

        if not store:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Store {store_id} not found",
            )

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Get deals: products on sale, sorted by discount
        deals_query = (
            select(PriceReport, Product)
            .join(Product, PriceReport.product_id == Product.id)
            .where(
                and_(
                    PriceReport.store_id == store_id,
                    PriceReport.is_on_sale == True,
                    PriceReport.created_at >= cutoff,
                )
            )
            .order_by(desc(PriceReport.sale_pct_off))
            .limit(limit)
        )

        results = db.execute(deals_query).all()

        deal_responses = [
            DealResponse(
                product_id=product.id,
                product_name=product.name,
                price_aud=price_report.price_cents / 100,
                was_price_aud=(
                    price_report.was_price_cents / 100
                    if price_report.was_price_cents
                    else None
                ),
                discount_pct=price_report.sale_pct_off,
            )
            for price_report, product in results
        ]

        return StoreDealsResponse(
            store_id=store_id,
            store_name=store.name,
            chain=store.chain,
            deals_count=len(deal_responses),
            deals=deal_responses,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get deals for store {store_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve store deals",
        )

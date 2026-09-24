"""
Products router with search, detail, price history, and per-product store endpoints.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, asc, desc, func, select
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import PricePrediction, PriceReport, Product, Store
from app.schemas.prediction import PricePredictionResponse
from app.schemas.product import (
    PriceByChainResponse,
    PriceHistoryResponse,
    PricePointResponse,
    ProductDetailResponse,
    ProductResponse,
)
from app.services.geo import haversine_km
from app.services.search import search_products_by_gtin, search_products_ranked

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Products"])


def _attach_prices(db: Session, products: list[Product]) -> list[ProductResponse]:
    """Attach cheapest current price to each product in one batch query."""
    if not products:
        return []

    product_ids = [p.id for p in products]

    # Latest price report per product: pick the lowest price_cents row per product_id
    latest_subq = (
        select(
            PriceReport.product_id,
            func.min(PriceReport.price_cents).label("min_price_cents"),
        )
        .where(PriceReport.product_id.in_(product_ids))
        .group_by(PriceReport.product_id)
        .subquery()
    )

    # Join back to get was_price / discount / on_sale for the cheapest row
    price_rows = db.execute(
        select(
            PriceReport.product_id,
            PriceReport.price_cents,
            PriceReport.was_price_cents,
            PriceReport.sale_pct_off,
            PriceReport.is_on_sale,
        )
        .join(
            latest_subq,
            and_(
                PriceReport.product_id == latest_subq.c.product_id,
                PriceReport.price_cents == latest_subq.c.min_price_cents,
            ),
        )
        .distinct(PriceReport.product_id)
        .order_by(PriceReport.product_id, desc(PriceReport.created_at))
    ).fetchall()

    price_map: dict = {
        row.product_id: {
            "lowest_price_aud": row.price_cents / 100,
            "was_price_aud": row.was_price_cents / 100 if row.was_price_cents else None,
            "discount_pct": float(row.sale_pct_off) if row.sale_pct_off else None,
            "is_on_sale": bool(row.is_on_sale),
        }
        for row in price_rows
    }

    results = []
    for p in products:
        data = ProductResponse.model_validate(p)
        price = price_map.get(p.id)
        if price:
            data.lowest_price_aud = price["lowest_price_aud"]
            data.was_price_aud = price["was_price_aud"]
            data.discount_pct = price["discount_pct"]
            data.is_on_sale = price["is_on_sale"]
        results.append(data)
    return results


@router.get("/categories", response_model=list[str])
async def list_categories(db: Session = Depends(get_db)) -> list[str]:
    """Return all distinct non-null product categories, sorted."""
    rows = db.execute(
        select(Product.category)
        .where(Product.category.isnot(None))
        .distinct()
        .order_by(Product.category)
    ).scalars().all()
    return list(rows)


@router.get("/search", response_model=list[ProductResponse])
async def search_products(
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    gtin: Annotated[str | None, Query(max_length=14)] = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
    chain: Annotated[str | None, Query(max_length=50)] = None,
    in_stock: Annotated[bool | None, Query()] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(ge=1, le=100)] = 25.0,
    sort: Annotated[
        Literal["relevance", "price_asc", "price_desc", "discount", "name", "newest"],
        Query(),
    ] = "relevance",
    limit: Annotated[int, Query(ge=1, le=200)] = 48,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
) -> list[ProductResponse]:
    """
    Browse/search products with optional filters.

    Query Parameters:
        q: Free-text query — ranked by trigram + semantic similarity (RRF fusion),
           boosted by sale magnitude and geo proximity when lat/lon provided
        gtin: Exact GTIN match
        category: Filter by category (exact)
        chain: Filter by chain (e.g. ``coles``)
        in_stock: Filter by stock status
        lat / lon: User location — nearby-store products score higher
        radius_km: Max store distance for geo signal (default 25km)
        sort: relevance (default), price_asc, price_desc, discount, name, newest
        limit: Max results (1-200, default 48)
        offset: Pagination offset
    """
    try:
        if gtin:
            results = search_products_by_gtin(db, gtin=gtin)
        elif q:
            results = search_products_ranked(
                db,
                q=q,
                category=category,
                chain=chain,
                in_stock=in_stock,
                lat=lat,
                lon=lon,
                radius_km=radius_km,
                limit=limit,
                offset=offset,
            )
        else:
            # No query: return all products filtered + sorted
            stmt = select(Product)
            if category:
                stmt = stmt.where(Product.category == category)
            if chain:
                stmt = stmt.where(Product.external_source == f"catalog_{chain}")
            if in_stock is not None:
                stmt = stmt.where(Product.in_stock == in_stock)

            if sort == "name":
                stmt = stmt.order_by(asc(Product.name))
            elif sort == "newest":
                stmt = stmt.order_by(desc(Product.created_at))
            elif sort in ("price_asc", "price_desc", "discount"):
                # For browse (no query), price sort requires a join to PriceReport
                best_price_subq = (
                    select(
                        PriceReport.product_id,
                        func.min(PriceReport.price_cents).label("min_price"),
                        func.max(PriceReport.sale_pct_off).label("max_disc"),
                    )
                    .group_by(PriceReport.product_id)
                    .subquery()
                )
                stmt = stmt.outerjoin(best_price_subq, Product.id == best_price_subq.c.product_id)
                if sort == "price_asc":
                    stmt = stmt.order_by(asc(best_price_subq.c.min_price).nulls_last())
                elif sort == "price_desc":
                    stmt = stmt.order_by(desc(best_price_subq.c.min_price).nulls_last())
                else:  # discount
                    stmt = stmt.order_by(desc(best_price_subq.c.max_disc).nulls_last())
            else:
                stmt = stmt.order_by(asc(Product.name))

            stmt = stmt.limit(limit).offset(offset)
            results = list(db.execute(stmt).scalars().all())

        products = _attach_prices(db, results)

        # Post-process sort for ranked results (relevance already sorted by search service)
        if q and sort != "relevance":
            if sort == "price_asc":
                products.sort(key=lambda p: p.lowest_price_aud or float("inf"))
            elif sort == "price_desc":
                products.sort(key=lambda p: p.lowest_price_aud or 0, reverse=True)
            elif sort == "discount":
                products.sort(key=lambda p: p.discount_pct or 0, reverse=True)
            elif sort == "name":
                products.sort(key=lambda p: p.name.lower())
            elif sort == "newest":
                pass  # relevance order is fine for newest in search context

        return products

    except Exception as e:
        logger.error(f"Product search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Product search failed",
        )


@router.get("/{product_id}", response_model=ProductDetailResponse)
async def get_product(
    product_id: UUID,
    db: Session = Depends(get_db),
) -> ProductDetailResponse:
    """
    Get single product detail with latest prices by chain.

    Path Parameters:
        product_id: UUID of the product

    Returns:
        ProductDetailResponse: Product details with current prices by chain

    Raises:
        HTTPException: 404 if product not found
    """
    try:
        product = db.execute(
            select(Product).where(Product.id == product_id)
        ).scalar_one_or_none()

        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id} not found",
            )

        # Get latest prices by chain
        # Subquery to find latest price report per chain
        latest_prices_subquery = (
            select(
                PriceReport.store_id,
                func.max(PriceReport.created_at).label("max_created_at"),
            )
            .where(
                and_(
                    PriceReport.product_id == product_id,
                    PriceReport.created_at
                    >= datetime.now(timezone.utc) - timedelta(days=90),
                )
            )
            .group_by(PriceReport.store_id)
            .subquery()
        )

        # Get price reports
        price_reports = db.execute(
            select(PriceReport, Store)
            .join(Store, PriceReport.store_id == Store.id)
            .join(
                latest_prices_subquery,
                and_(
                    PriceReport.store_id == latest_prices_subquery.c.store_id,
                    PriceReport.created_at == latest_prices_subquery.c.max_created_at,
                ),
            )
        ).all()

        # Group by chain and calculate stats
        chain_stats = {}
        last_updated = None

        for price_report, store in price_reports:
            if store.chain not in chain_stats:
                chain_stats[store.chain] = {
                    "prices": [],
                    "stores": set(),
                }

            chain_stats[store.chain]["prices"].append(price_report.price_cents / 100)
            chain_stats[store.chain]["stores"].add(store.name)

            if not last_updated or price_report.created_at > last_updated:
                last_updated = price_report.created_at

        # Build price by chain responses
        latest_prices = []
        for chain, data in chain_stats.items():
            prices = data["prices"]
            latest_prices.append(
                PriceByChainResponse(
                    chain=chain,
                    store_count=len(data["stores"]),
                    lowest_price_aud=min(prices),
                    highest_price_aud=max(prices),
                    avg_price_aud=sum(prices) / len(prices),
                    is_on_sale=any(
                        pr.is_on_sale
                        for pr, s in price_reports
                        if s.chain == chain
                    ),
                )
            )

        return ProductDetailResponse(
            id=product.id,
            gtin=product.gtin,
            name=product.name,
            brand=product.brand,
            category=product.category,
            unit_type=product.unit_type,
            unit_size=product.unit_size,
            image_url=product.image_url,
            latest_prices=latest_prices,
            last_updated=last_updated,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get product {product_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve product",
        )


@router.get("/{product_id}/prices", response_model=PriceHistoryResponse)
async def get_price_history(
    product_id: UUID,
    chain: Annotated[str | None, Query()] = None,
    days: Annotated[int, Query(ge=7, le=365)] = 90,
    db: Session = Depends(get_db),
) -> PriceHistoryResponse:
    """
    Get price history for product over time.

    Path Parameters:
        product_id: UUID of the product

    Query Parameters:
        chain: Filter by grocery chain name (optional)
        days: Historical period to retrieve (7-365, default 90 days)

    Returns:
        PriceHistoryResponse: Historical price data points for charting

    Raises:
        HTTPException: 404 if product not found
    """
    try:
        # Verify product exists
        product = db.execute(
            select(Product).where(Product.id == product_id)
        ).scalar_one_or_none()

        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id} not found",
            )

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        query = (
            select(PriceReport, Store)
            .join(Store, PriceReport.store_id == Store.id)
            .where(
                and_(
                    PriceReport.product_id == product_id,
                    PriceReport.created_at >= cutoff,
                )
            )
        )

        if chain:
            query = query.where(Store.chain == chain)

        price_reports = db.execute(
            query.order_by(PriceReport.created_at)
        ).all()

        # Convert to response format
        data = []
        min_price = None
        max_price = None
        sum_price = 0
        count = 0

        for price_report, store in price_reports:
            price_aud = price_report.price_cents / 100

            if min_price is None or price_aud < min_price:
                min_price = price_aud
            if max_price is None or price_aud > max_price:
                max_price = price_aud

            sum_price += price_aud
            count += 1

            data.append(
                PricePointResponse(
                    date=price_report.created_at,
                    price_aud=price_aud,
                    chain=store.chain,
                    store_name=store.name,
                    is_on_sale=price_report.is_on_sale,
                    discount_pct=price_report.sale_pct_off,
                )
            )

        avg_price = sum_price / count if count > 0 else None

        return PriceHistoryResponse(
            product_id=product_id,
            product_name=product.name,
            data=data,
            min_price=min_price,
            max_price=max_price,
            avg_price=avg_price,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get price history for {product_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve price history",
        )


@router.get("/{product_id}/stores")
async def get_product_stores(
    product_id: UUID,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[int, Query(ge=1, le=100)] = 25,
    db: Session = Depends(get_db),
) -> dict:
    """
    Get stores that carry this product with their latest prices.

    Optionally pass lat/lon to sort by distance and restrict to radius_km.
    Without lat/lon, returns all stores with any price in the last 30 days.
    """
    try:
        product = db.execute(
            select(Product).where(Product.id == product_id)
        ).scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        # Latest price per store for this product in the last 30 days
        latest_subq = (
            select(
                PriceReport.store_id,
                func.max(PriceReport.created_at).label("max_created_at"),
            )
            .where(
                and_(
                    PriceReport.product_id == product_id,
                    PriceReport.created_at >= cutoff,
                )
            )
            .group_by(PriceReport.store_id)
            .subquery()
        )

        rows = db.execute(
            select(PriceReport, Store)
            .join(Store, PriceReport.store_id == Store.id)
            .join(
                latest_subq,
                and_(
                    PriceReport.store_id == latest_subq.c.store_id,
                    PriceReport.created_at == latest_subq.c.max_created_at,
                ),
            )
        ).all()

        stores = []
        seen_store_ids: set = set()
        for price_report, store in rows:
            if store.id in seen_store_ids:
                continue
            seen_store_ids.add(store.id)

            distance_km: float | None = None
            if lat is not None and lon is not None and store.lat is not None and store.lon is not None:
                distance_km = haversine_km(lat, lon, store.lat, store.lon)
                if distance_km > radius_km:
                    continue

            stores.append({
                "id": str(store.id),
                "chain": store.chain,
                "name": store.name,
                "address": store.address,
                "suburb": store.suburb,
                "state": store.state,
                "postcode": store.postcode,
                "lat": store.lat,
                "lon": store.lon,
                "price_aud": price_report.price_cents / 100,
                "is_on_sale": price_report.is_on_sale,
                "discount_pct": price_report.sale_pct_off,
                "distance_km": round(distance_km, 2) if distance_km is not None else None,
            })

        # Sort by distance if provided (stores without coords go last), else by price
        if lat is not None and lon is not None:
            stores.sort(key=lambda s: s["distance_km"] if s["distance_km"] is not None else 9999)
        else:
            stores.sort(key=lambda s: s["price_aud"])

        return {"product_id": str(product_id), "count": len(stores), "stores": stores}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get stores for product {product_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve product stores",
        )


@router.get("/{product_id}/predictions", response_model=list[PricePredictionResponse])
async def get_product_predictions_nested(
    product_id: UUID,
    db: Session = Depends(get_db),
) -> list[PricePredictionResponse]:
    """
    Nested alias for GET /api/v1/predictions/{product_id}.
    Returns upcoming ML price predictions for this product, grouped by chain.
    """
    product = db.execute(
        select(Product).where(Product.id == product_id)
    ).scalar_one_or_none()

    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )

    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(PricePrediction)
        .where(
            PricePrediction.product_id == product_id,
            PricePrediction.prediction_week >= now,
        )
        .order_by(PricePrediction.prediction_week, PricePrediction.chain)
    ).scalars().all()

    return [PricePredictionResponse.from_orm(r) for r in rows]


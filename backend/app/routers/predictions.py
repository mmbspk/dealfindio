"""
Predictions router — ML price forecasts per product.

GET /api/v1/predictions/{product_id}
    Returns upcoming PricePrediction rows for the given product (current + future weeks).
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import PricePrediction, Product
from app.schemas.prediction import PricePredictionResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_predictions(db: Session, product_id: UUID) -> list[PricePredictionResponse]:
    """Shared query: future predictions for a product, sorted by week then chain."""
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


@router.get("/{product_id}", response_model=list[PricePredictionResponse])
async def get_product_predictions(
    product_id: UUID,
    db: Session = Depends(get_db),
) -> list[PricePredictionResponse]:
    """
    Get ML price predictions for a product.

    Returns upcoming predicted prices grouped by chain, for the next 1–2 weeks.
    Returns an empty list if the product has no predictions yet (ML runs weekly).

    Path Parameters:
        product_id: UUID of the product

    Returns:
        list[PricePredictionResponse]: Upcoming price predictions ordered by week
    """
    product = db.execute(
        select(Product).where(Product.id == product_id)
    ).scalar_one_or_none()

    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )

    try:
        return _get_predictions(db, product_id)
    except Exception as exc:
        logger.error(f"Failed to retrieve predictions for {product_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve predictions",
        )

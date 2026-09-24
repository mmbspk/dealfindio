"""
Price prediction response schemas for REST API.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PricePredictionResponse(BaseModel):
    id: UUID
    product_id: UUID
    chain: str | None
    predicted_price_aud: float
    prediction_week: datetime
    ci_low_aud: float | None
    ci_high_aud: float | None
    is_likely_sale: bool
    model_version: str | None
    generated_at: datetime

    @classmethod
    def from_orm(cls, obj) -> "PricePredictionResponse":  # type: ignore[override]
        return cls(
            id=obj.id,
            product_id=obj.product_id,
            chain=obj.chain,
            predicted_price_aud=obj.predicted_price_cents / 100,
            prediction_week=obj.prediction_week,
            ci_low_aud=obj.confidence_interval_low / 100 if obj.confidence_interval_low is not None else None,
            ci_high_aud=obj.confidence_interval_high / 100 if obj.confidence_interval_high is not None else None,
            is_likely_sale=obj.is_likely_sale,
            model_version=obj.model_version,
            generated_at=obj.created_at,  # PricePrediction inherits created_at from BaseModel
        )

    class Config:
        from_attributes = True

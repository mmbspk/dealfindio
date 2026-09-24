"""Watchlist schemas — lists and items."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# ── Lists ────────────────────────────────────────────────────────────────────

class WatchlistListCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    emoji: str | None = Field(None, max_length=10)


class WatchlistListResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    emoji: str | None = None
    item_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


# ── Items ────────────────────────────────────────────────────────────────────

class WatchlistItemCreate(BaseModel):
    product_id: UUID
    target_price_cents: int | None = Field(None, ge=1)
    target_pct_off: float | None = Field(None, ge=0.1, le=100.0)
    alert_on_prediction: bool = False
    preferred_chains: list[str] | None = None


class WatchlistItemResponse(BaseModel):
    id: UUID
    user_id: UUID
    list_id: UUID | None = None
    product_id: UUID
    product_name: str | None = None
    product_image_url: str | None = None
    product_brand: str | None = None
    product_category: str | None = None
    current_price_aud: float | None = None
    current_chain: str | None = None
    is_on_sale: bool = False
    target_price_cents: int | None = None
    target_pct_off: float | None = None
    alert_on_prediction: bool = False
    preferred_chains: list[str] | None = None
    is_active: bool = True
    created_at: datetime

    class Config:
        from_attributes = True


# Backwards-compat aliases used by existing router + tests
WatchlistCreateRequest = WatchlistItemCreate
WatchlistResponse = WatchlistItemResponse


class WatchlistDetailResponse(WatchlistItemResponse):
    min_price_aud: float | None = None
    max_price_aud: float | None = None
    avg_price_aud: float | None = None
    recent_price_count: int = 0
    recent_notifications_count: int = 0


# ── Product watchlist status (for the Save-to-list popover) ──────────────────

class ProductWatchlistStatus(BaseModel):
    """Which of the user's lists contain a given product."""
    list_id: UUID
    list_name: str
    list_emoji: str | None = None
    in_list: bool
    item_id: UUID | None = None

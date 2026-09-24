"""
Store response schemas for REST API.

Defines Pydantic models for store nearby search, detail, and deals endpoints.
"""

from uuid import UUID

from pydantic import BaseModel, Field


class DealResponse(BaseModel):
    """Current deal/promotion at a store."""

    product_id: UUID
    product_name: str = Field(..., description="Product name")
    price_aud: float = Field(..., description="Current price (AUD)")
    was_price_aud: float | None = Field(None, description="Original price before discount")
    discount_pct: float | None = Field(None, description="Discount percentage")


class StoreResponse(BaseModel):
    """Store location response schema."""

    id: UUID
    chain: str = Field(..., description="Grocery chain name")
    name: str = Field(..., description="Store display name")
    address: str | None = Field(None, description="Street address")
    suburb: str | None = Field(None, description="Suburb/city name")
    state: str | None = Field(None, description="Australian state code")
    postcode: str | None = Field(None, description="Postal code")
    lat: float | None = Field(None, description="Latitude coordinate")
    lon: float | None = Field(None, description="Longitude coordinate")
    is_active: bool = Field(True, description="Whether store is active/open")
    distance_km: float | None = Field(None, description="Distance from query location (km)")

    class Config:
        from_attributes = True


class NearbyStoresResponse(BaseModel):
    """Response for nearby stores query."""

    count: int = Field(..., description="Number of stores returned")
    stores: list[StoreResponse] = Field(..., description="List of nearby stores")


class StoreDetailResponse(StoreResponse):
    """Store detail with additional information."""

    phone: str | None = Field(None, description="Store phone number")
    hours: str | None = Field(None, description="Store opening hours")
    product_count: int = Field(0, description="Number of products with prices at this store")
    last_price_update: str | None = Field(None, description="Last price data refresh timestamp")


class StoreDealsResponse(BaseModel):
    """Response for store deals/current promotions."""

    store_id: UUID
    store_name: str
    chain: str
    deals_count: int = Field(..., description="Number of current deals")
    deals: list[DealResponse] = Field(..., description="List of current deals")

"""
Product response schemas for REST API.

Defines Pydantic models for product search, detail, and price history responses.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ProductResponse(BaseModel):
    """Product search result response schema."""

    id: UUID
    gtin: str | None = Field(None, description="Global Trade Item Number (EAN-13)")
    name: str = Field(..., description="Product name/description")
    brand: str | None = Field(None, description="Manufacturer/brand name")
    category: str | None = Field(None, description="Product category")
    unit_type: str | None = Field(None, description="Unit of measurement (ea, kg, L, g, ml)")
    unit_size: float | None = Field(None, description="Quantity per unit")
    package_size: str | None = Field(
        None, description="Raw package size string e.g. '2L', '375mL 24 Pack'"
    )
    unit_price: float | None = Field(None, description="Comparable unit price e.g. 1.50 per 100g")
    unit_price_label: str | None = Field(
        None, description="Unit label for unit_price e.g. '100g', '100ml', 'ea'"
    )
    image_url: str | None = Field(None, description="Self-hosted product image URL (preferred)")
    external_image_url: str | None = Field(None, description="Original retailer CDN URL (fallback)")
    image_source: str | None = Field(
        None, description="Source of image_url — 'open_food_facts' or null"
    )

    @model_validator(mode="after")
    def _coalesce_image(self) -> "ProductResponse":
        # Expose the best available image as image_url so existing consumers
        # never see NULL while the enrichment pipeline catches up.
        if not self.image_url and self.external_image_url:
            self.image_url = self.external_image_url
        return self

    in_stock: bool = Field(True, description="Whether the product is in stock")

    # Cheapest current price across all stores/chains (null if no price data yet)
    lowest_price_aud: float | None = Field(None, description="Cheapest current price in AUD")
    cheapest_chain: str | None = Field(None, description="Chain name with the lowest price")
    available_chains: list[str] = Field(
        default_factory=list, description="All chains currently carrying this product"
    )
    was_price_aud: float | None = Field(
        None, description="Original price before discount, if on sale"
    )
    discount_pct: float | None = Field(None, description="Discount percentage if on sale")
    is_on_sale: bool = Field(False, description="Whether currently on sale somewhere")

    class Config:
        from_attributes = True


class PricePointResponse(BaseModel):
    """Single price data point for history chart."""

    date: datetime = Field(..., description="Timestamp of price observation")
    price_aud: float = Field(..., description="Price in AUD")
    chain: str | None = Field(None, description="Grocery chain name")
    store_name: str | None = Field(None, description="Store location")
    is_on_sale: bool = Field(False, description="Whether item is on sale")
    discount_pct: float | None = Field(None, description="Discount percentage if on sale")


class PriceHistoryResponse(BaseModel):
    """Price history for a product over time."""

    product_id: UUID
    product_name: str | None = None
    data: list[PricePointResponse] = Field(
        default_factory=list, description="List of price observations over time"
    )
    min_price: float | None = None
    max_price: float | None = None
    avg_price: float | None = None


class PriceByChainResponse(BaseModel):
    """Latest price for a product at a specific chain."""

    chain: str = Field(..., description="Grocery chain name")
    store_count: int = Field(..., description="Number of stores with this price")
    lowest_price_aud: float = Field(..., description="Lowest price at this chain (AUD)")
    highest_price_aud: float = Field(..., description="Highest price at this chain (AUD)")
    avg_price_aud: float = Field(..., description="Average price at this chain (AUD)")
    is_on_sale: bool = Field(False, description="Whether on sale at any store")


class ProductDetailResponse(ProductResponse):
    """Product detail with latest pricing information."""

    latest_prices: list[PriceByChainResponse] = Field(
        default_factory=list, description="Latest prices by chain"
    )
    last_updated: datetime | None = Field(None, description="Last price update timestamp")

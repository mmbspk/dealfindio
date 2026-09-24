"""
Price report request and response schemas for REST API.

Defines Pydantic models for community price report submission,
moderation, and response endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CommunityPriceReportRequest(BaseModel):
    """Request schema for submitting a new community price report."""

    product_gtin: str | None = Field(
        None,
        max_length=14,
        description="Product GTIN (EAN-13/EAN-14) for lookup",
    )
    product_name: str | None = Field(
        None,
        max_length=255,
        description="Product name for fuzzy matching",
    )
    store_id: UUID = Field(..., description="UUID of the store")
    price_cents: int = Field(
        ...,
        ge=0,
        description="Current price in AUD cents",
    )
    was_price_cents: int | None = Field(
        None,
        ge=0,
        description="Original price before sale in AUD cents",
    )
    latitude: float = Field(
        ...,
        ge=-90,
        le=90,
        description="User's current latitude",
    )
    longitude: float = Field(
        ...,
        ge=-180,
        le=180,
        description="User's current longitude",
    )


class PriceReportResponse(BaseModel):
    """Response for a successfully created price report."""

    id: UUID = Field(..., description="Unique report ID")
    product_id: UUID = Field(..., description="Product ID")
    product_name: str = Field(..., description="Product name")
    store_id: UUID = Field(..., description="Store ID")
    store_name: str = Field(..., description="Store name")
    price_aud: float = Field(..., description="Price in AUD")
    was_price_aud: float | None = Field(
        None,
        description="Original price in AUD before sale",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score (0.0-1.0)",
    )
    is_verified: bool = Field(..., description="Whether report is verified")
    reporter_id: UUID = Field(..., description="Reporter user ID")
    created_at: datetime = Field(..., description="Creation timestamp")

    class Config:
        from_attributes = True


class FlaggedReportResponse(BaseModel):
    """Response for a flagged report in moderation queue."""

    id: UUID = Field(..., description="Report ID")
    product_id: UUID | None = Field(None, description="Product ID")
    product_name: str = Field(..., description="Product name")
    store_id: UUID | None = Field(None, description="Store ID")
    store_name: str = Field(..., description="Store name")
    price_aud: float = Field(..., description="Reported price in AUD")
    was_price_aud: float | None = Field(None, description="Original price in AUD")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score",
    )
    is_verified: bool = Field(..., description="Whether verified")
    reporter_email: str = Field(..., description="Reporter email address")
    reporter_reputation: float = Field(
        ...,
        ge=0.0,
        le=5.0,
        description="Reporter reputation score",
    )
    reporter_contribution_count: int = Field(
        ...,
        ge=0,
        description="Reporter contribution count",
    )
    mean_price_30d_aud: float = Field(
        ...,
        description="Mean price over 30 days in AUD",
    )
    is_flagged: bool = Field(..., description="Whether report is flagged")
    created_at: datetime = Field(..., description="Creation timestamp")

    class Config:
        from_attributes = True


class FlaggedReportsListResponse(BaseModel):
    """Response for list of flagged reports."""

    total_count: int = Field(..., description="Total number of flagged reports")
    count: int = Field(..., description="Number of reports in this response")
    limit: int = Field(..., description="Query limit")
    offset: int = Field(..., description="Query offset")
    reports: list[FlaggedReportResponse] = Field(
        ...,
        description="List of flagged reports",
    )

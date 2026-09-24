"""Pydantic schemas for the receipt upload / OCR endpoints."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ExtractedProduct(BaseModel):
    """A single product line item extracted from a receipt image."""

    name: str = Field(..., description="Product name as read from the receipt")
    price: float = Field(..., description="Price in AUD dollars")
    quantity: int = Field(1, description="Quantity purchased")
    unit_price: float | None = Field(None, description="Unit price in AUD, if shown")


class ExtractedReceipt(BaseModel):
    """Structured data extracted from a receipt image by Claude vision."""

    products: list[ExtractedProduct] = Field(default_factory=list)
    store_name: str | None = Field(None, description="Store name if detected")
    receipt_date: str | None = Field(None, description="ISO date if detected")
    total: float | None = Field(None, description="Receipt total in AUD")
    parsed_at: str | None = Field(None, description="Parse timestamp (ISO 8601)")


class CreatedReceiptReport(BaseModel):
    """A price report created from a matched receipt line item."""

    price_report_id: UUID = Field(..., description="Created price report ID")
    product_id: UUID = Field(..., description="Matched product ID")
    product_name: str = Field(..., description="Matched product name")
    confidence: float = Field(..., description="Initial confidence (0.0-1.0)")


class UnmatchedReceiptItem(BaseModel):
    """A receipt line item that could not be matched to a known product."""

    name: str = Field(..., description="Receipt line item name")
    price: float = Field(..., description="Price in AUD dollars")
    reason: str = Field(..., description="Why it could not be matched")


class ReceiptUploadResponse(BaseModel):
    """Response returned after uploading and parsing a receipt image."""

    status: str = Field(..., description="'success' or 'partial'")
    extracted_data: ExtractedReceipt = Field(..., description="Raw parsed receipt")
    created_reports: list[CreatedReceiptReport] = Field(default_factory=list)
    unmatched_items: list[UnmatchedReceiptItem] = Field(default_factory=list)
    message: str = Field(..., description="Human-readable summary")

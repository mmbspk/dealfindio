"""
Per-chain retailer listing for a canonical product.

One row per (chain, external_id) pair. A single canonical product may have
multiple listings from the same chain when retailers assign different SKUs to
regional assortments or store-scoped product IDs (e.g. IGA multi-store).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ProductRetailerListing(Base):
    """
    Retailer-specific listing data for a canonical product.

    Holds fields that are per-listing rather than per-canonical-product:
    the retailer's own SKU, name, category, image, and freshness timestamp.
    These will supersede the leaky columns on Product once the #49 series
    (dual-write → read-migration → deprecation) is complete.
    """

    __tablename__ = "product_retailer_listings"

    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    chain: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_name: Mapped[str | None] = mapped_column(String(500))
    external_brand: Mapped[str | None] = mapped_column(String(255))
    external_category: Mapped[str | None] = mapped_column(String(200))
    catalog_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_online_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    external_image_url: Mapped[str | None] = mapped_column(String(500))

    product: Mapped["Product"] = relationship(
        back_populates="retailer_listings",
        foreign_keys=[product_id],
    )

    __table_args__ = (
        UniqueConstraint("chain", "external_id", name="uq_listing_chain_external_id"),
        Index("idx_listing_product_chain", "product_id", "chain"),
        Index("idx_listing_chain_last_seen", "chain", "catalog_last_seen_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ProductRetailerListing chain={self.chain}"
            f" external_id={self.external_id}"
            f" product_id={self.product_id}>"
        )

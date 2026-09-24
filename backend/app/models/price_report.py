"""
Price report model for product pricing data.

Records individual price observations for products at specific stores.
Price reports can come from community users or automated scrapers.
Includes sale information, confidence scores, and validity ranges.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class PriceReport(Base):
    """
    Individual price observation for a product at a store.

    Records when a specific product was observed at a specific price at a store.
    Includes source (community report or scraper), validity range, sale information,
    and confidence/verification metrics.

    Price amounts are always stored in AUD cents (integer) for precision.

    Attributes:
        product_id: Foreign key to Product
        store_id: Foreign key to Store
        price_cents: Current price in AUD cents
        was_price_cents: Original price before sale (in cents)
        unit_price_cents: Price per unit in AUD cents
        unit_type: Unit type for unit_price (e.g., 'per_100g')
        source: Source of report ('community', 'flyer_coles', 'flyer_woolworths', etc.)
        reporter_user_id: FK to UserProfile if from community (NULL for scrapers)
        is_on_sale: Whether item is currently on sale
        sale_pct_off: Discount percentage if on sale
        valid_from: Start time for price validity
        valid_to: End time for price validity
        confidence: Confidence score (0.0-1.0)
        is_verified: Whether price has been verified by another user
        is_flagged: Whether report is flagged as potential outlier
        lat: Geographic latitude of observation
        lon: Geographic longitude of observation
        product: Relationship to Product
        store: Relationship to Store
        reporter: Relationship to UserProfile (if community report)
        notifications: Relationship to triggered notifications
    """

    __tablename__ = "price_reports"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    store_id: Mapped[UUID] = mapped_column(ForeignKey("stores.id"))
    price_cents: Mapped[int] = mapped_column(nullable=False)
    was_price_cents: Mapped[int | None]
    unit_price_cents: Mapped[int | None]
    unit_type: Mapped[str | None] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    reporter_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    is_on_sale: Mapped[bool] = mapped_column(default=False)
    sale_pct_off: Mapped[float | None]
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(default=1.0)
    is_verified: Mapped[bool] = mapped_column(default=False)
    is_flagged: Mapped[bool] = mapped_column(default=False)
    upvote_count: Mapped[int] = mapped_column(default=0)
    lat: Mapped[float | None]
    lon: Mapped[float | None]

    # Relationships
    product: Mapped["Product"] = relationship(
        back_populates="price_reports",
        foreign_keys=[product_id],
    )
    store: Mapped["Store"] = relationship(
        back_populates="price_reports",
        foreign_keys=[store_id],
    )
    reporter: Mapped["UserProfile | None"] = relationship(
        back_populates="price_reports",
        foreign_keys=[reporter_user_id],
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="price_report",
        cascade="all, delete-orphan",
        foreign_keys="Notification.price_report_id",
    )

    __table_args__ = (
        Index("idx_pr_product_store", "product_id", "store_id"),
        Index("idx_pr_created_at", "created_at"),
        Index("idx_pr_source", "source"),
        Index("idx_pr_valid_range", "valid_from", "valid_to"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0"),
        CheckConstraint("price_cents >= 0"),
    )

    def __repr__(self) -> str:
        return f"<PriceReport product_id={self.product_id} store_id={self.store_id} price={self.price_cents}c id={self.id}>"

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ProductMatchCandidate(Base):
    """
    Pending cross-chain product match proposed by the fuzzy-name matcher.

    100% matches are auto-merged at ingest time and never appear here.
    92–99% matches are written here for admin review.
    Admins may also manually propose matches (score=100, proposed_by='admin:…').

    The pair (product_a_id, product_b_id) is always stored with the lower UUID
    first to prevent duplicate rows for the same logical pair.
    On "approved", product_b is merged into product_a (price_reports and
    watchlist rows re-pointed, product_b deleted).
    """

    __tablename__ = "product_match_candidates"
    __table_args__ = (
        UniqueConstraint("product_a_id", "product_b_id", name="uq_match_pair"),
        Index("idx_match_status_score", "status", "score"),
        Index("idx_match_product_a", "product_a_id"),
        Index("idx_match_product_b", "product_b_id"),
    )

    product_a_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    product_b_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    proposed_by: Mapped[str] = mapped_column(String(50), nullable=False, default="auto")
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # AI judge verdict (nullable — populated only by the Claude dedup pass).
    # Auditable + drives the admin UI chip. See app/services/ai_dedup.py.
    ai_verdict: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ai_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ai_keep: Mapped[Optional[str]] = mapped_column(String(1), nullable=True)
    ai_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    ai_model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ai_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    product_a: Mapped["Product"] = relationship(  # noqa: F821
        "Product", foreign_keys=[product_a_id], lazy="joined"
    )
    product_b: Mapped["Product"] = relationship(  # noqa: F821
        "Product", foreign_keys=[product_b_id], lazy="joined"
    )

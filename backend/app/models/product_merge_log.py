from sqlalchemy import Float, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ProductMergeLog(Base):
    """Permanent audit record for every product merge.

    All ID columns are plain VARCHARs — not FKs — so rows survive both the
    product deletion and the cascade-delete of the product_match_candidates row
    that happens at merge time.  created_at (from Base) serves as merged_at.
    """

    __tablename__ = "product_merge_log"

    # Survivor
    keep_id: Mapped[str] = mapped_column(String(36), nullable=False)
    keep_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    keep_external_source: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Deleted product
    drop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    drop_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    drop_brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drop_gtin: Mapped[str | None] = mapped_column(String(14), nullable=True)
    drop_external_source: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Candidate snapshot (not a FK — CASCADE-deleted at merge time)
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Provenance
    proposed_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_reports_moved: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), nullable=False
    )

    __table_args__ = (
        Index("idx_merge_log_keep_id", "keep_id"),
        Index("idx_merge_log_drop_id", "drop_id"),
        Index("idx_merge_log_candidate_id", "candidate_id"),
    )

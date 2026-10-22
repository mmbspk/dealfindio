from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PackageSizeRemediationLog(Base):
    """Immutable audit record for every package_size_norm correction.

    All ID columns are plain VARCHARs — not FKs — so rows survive any
    subsequent product deletion or listing reassignment.
    """

    __tablename__ = "package_size_remediation_log"

    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    before_norm: Mapped[str | None] = mapped_column(String(100), nullable=True)
    after_norm: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest_proposed_norm: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest_generated_at: Mapped[str] = mapped_column(String(50), nullable=False)
    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        Index("idx_remediation_log_product_id", "product_id"),
        Index("idx_remediation_log_batch_id", "batch_id"),
    )

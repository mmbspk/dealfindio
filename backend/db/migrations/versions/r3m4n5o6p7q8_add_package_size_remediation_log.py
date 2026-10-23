"""add package_size_remediation_log

Revision ID: r3m4n5o6p7q8
Revises: q2l3m4n5o6p7
Create Date: 2026-10-22
"""

from alembic import op
import sqlalchemy as sa

revision = "r3m4n5o6p7q8"
down_revision = "q2l3m4n5o6p7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "package_size_remediation_log",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("before_norm", sa.String(100), nullable=True),
        sa.Column("after_norm", sa.String(100), nullable=False),
        sa.Column("manifest_proposed_norm", sa.String(100), nullable=False),
        sa.Column("manifest_generated_at", sa.String(50), nullable=False),
        sa.Column("classification", sa.String(50), nullable=False),
        sa.Column("approved_by", sa.String(255), nullable=False),
    )
    op.create_index("idx_remediation_log_product_id", "package_size_remediation_log", ["product_id"])
    op.create_index("idx_remediation_log_batch_id", "package_size_remediation_log", ["batch_id"])


def downgrade() -> None:
    op.drop_index("idx_remediation_log_batch_id", table_name="package_size_remediation_log")
    op.drop_index("idx_remediation_log_product_id", table_name="package_size_remediation_log")
    op.drop_table("package_size_remediation_log")

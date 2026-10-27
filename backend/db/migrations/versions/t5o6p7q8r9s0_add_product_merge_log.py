"""add product_merge_log

Revision ID: t5o6p7q8r9s0
Revises: s4n5o6p7q8r9
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "t5o6p7q8r9s0"
down_revision = "s4n5o6p7q8r9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_merge_log",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # Survivor
        sa.Column("keep_id", sa.String(36), nullable=False),
        sa.Column("keep_name", sa.String(500), nullable=True),
        sa.Column("keep_external_source", sa.String(50), nullable=True),
        # Deleted product
        sa.Column("drop_id", sa.String(36), nullable=False),
        sa.Column("drop_name", sa.String(500), nullable=True),
        sa.Column("drop_brand", sa.String(255), nullable=True),
        sa.Column("drop_gtin", sa.String(14), nullable=True),
        sa.Column("drop_external_source", sa.String(50), nullable=True),
        # Candidate snapshot (not a FK — CASCADE-deleted at merge time)
        sa.Column("candidate_id", sa.String(36), nullable=True),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("ai_verdict", sa.String(20), nullable=True),
        sa.Column("ai_confidence", sa.Float, nullable=True),
        sa.Column("ai_reason", sa.String(300), nullable=True),
        # Provenance
        sa.Column("proposed_by", sa.String(50), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("price_reports_moved", sa.Integer, server_default="0", nullable=False),
    )
    op.create_index("idx_merge_log_keep_id", "product_merge_log", ["keep_id"])
    op.create_index("idx_merge_log_drop_id", "product_merge_log", ["drop_id"])
    op.create_index("idx_merge_log_candidate_id", "product_merge_log", ["candidate_id"])


def downgrade() -> None:
    op.drop_index("idx_merge_log_candidate_id", table_name="product_merge_log")
    op.drop_index("idx_merge_log_drop_id", table_name="product_merge_log")
    op.drop_index("idx_merge_log_keep_id", table_name="product_merge_log")
    op.drop_table("product_merge_log")

"""add_ai_dedup_columns

Revision ID: d4e5f6a7b8c9
Revises: b3c4d5e6f7a8
Create Date: 2026-09-10 09:00:00.000000

Adds nullable ai_* columns to product_match_candidates so the Claude dedup
judge can persist its verdict (same/different + confidence + which record to
keep + reason + model). Auditable and drives the admin review UI chip. No
backfill — existing rows keep NULLs and remain pure fuzzy candidates.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_match_candidates", sa.Column("ai_verdict", sa.String(20), nullable=True)
    )
    op.add_column(
        "product_match_candidates", sa.Column("ai_confidence", sa.Float(), nullable=True)
    )
    op.add_column(
        "product_match_candidates", sa.Column("ai_keep", sa.String(1), nullable=True)
    )
    op.add_column(
        "product_match_candidates", sa.Column("ai_reason", sa.String(300), nullable=True)
    )
    op.add_column(
        "product_match_candidates", sa.Column("ai_model", sa.String(50), nullable=True)
    )
    op.add_column(
        "product_match_candidates",
        sa.Column("ai_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_match_candidates", "ai_reviewed_at")
    op.drop_column("product_match_candidates", "ai_model")
    op.drop_column("product_match_candidates", "ai_reason")
    op.drop_column("product_match_candidates", "ai_keep")
    op.drop_column("product_match_candidates", "ai_confidence")
    op.drop_column("product_match_candidates", "ai_verdict")

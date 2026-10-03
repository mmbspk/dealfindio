"""add_off_image_review_columns

Revision ID: f66f8b6ede4b
Revises: j5e6f7a8b9c0
Create Date: 2026-10-03 11:32:33.931197

OFF image review queue — new enrichment results land in off_image_url as
'pending' and only graduate to image_url once approved (manually or by AI).
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f66f8b6ede4b"
down_revision: str | None = "j5e6f7a8b9c0"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("off_image_url", sa.String(500), nullable=True))
    op.add_column("products", sa.Column("off_image_score", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("off_image_off_code", sa.String(50), nullable=True))
    op.add_column("products", sa.Column("off_image_off_name", sa.String(300), nullable=True))
    op.add_column("products", sa.Column("off_image_status", sa.String(20), nullable=True))
    op.add_column("products", sa.Column("off_image_ai_verdict", sa.String(20), nullable=True))
    op.add_column("products", sa.Column("off_image_ai_reason", sa.String(300), nullable=True))
    op.add_column("products", sa.Column("off_image_ai_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("idx_products_off_image_status", "products", ["off_image_status"])


def downgrade() -> None:
    op.drop_index("idx_products_off_image_status", table_name="products")
    op.drop_column("products", "off_image_ai_reviewed_at")
    op.drop_column("products", "off_image_ai_reason")
    op.drop_column("products", "off_image_ai_verdict")
    op.drop_column("products", "off_image_status")
    op.drop_column("products", "off_image_off_name")
    op.drop_column("products", "off_image_off_code")
    op.drop_column("products", "off_image_score")
    op.drop_column("products", "off_image_url")

"""add_product_match_candidates

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-09 09:00:00.000000

Adds product_match_candidates table for admin dedup review queue.
92-99% fuzzy matches are written here for human review; 100% auto-merge
continues at ingest time as before.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3c4d5e6f7a8"
down_revision: str | None = "c1a2b3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_match_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "product_a_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_b_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("proposed_by", sa.String(50), nullable=False, server_default="auto"),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_match_pair", "product_match_candidates", ["product_a_id", "product_b_id"]
    )
    op.create_index("idx_match_status_score", "product_match_candidates", ["status", "score"])
    op.create_index("idx_match_product_a", "product_match_candidates", ["product_a_id"])
    op.create_index("idx_match_product_b", "product_match_candidates", ["product_b_id"])


def downgrade() -> None:
    op.drop_index("idx_match_product_b", table_name="product_match_candidates")
    op.drop_index("idx_match_product_a", table_name="product_match_candidates")
    op.drop_index("idx_match_status_score", table_name="product_match_candidates")
    op.drop_constraint("uq_match_pair", "product_match_candidates", type_="unique")
    op.drop_table("product_match_candidates")

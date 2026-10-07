"""add product_retailer_listings table

Revision ID: m8h9i0j1k2l3
Revises: l7g8h9i0j1k2
Create Date: 2026-10-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "m8h9i0j1k2l3"
down_revision = "l7g8h9i0j1k2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_retailer_listings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
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
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chain", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("external_name", sa.String(500), nullable=True),
        sa.Column("external_brand", sa.String(255), nullable=True),
        sa.Column("external_category", sa.String(200), nullable=True),
        sa.Column("catalog_last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "in_stock",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "is_online_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("external_image_url", sa.String(500), nullable=True),
    )
    op.create_unique_constraint(
        "uq_listing_chain_external_id",
        "product_retailer_listings",
        ["chain", "external_id"],
    )
    op.create_index(
        "idx_listing_product_chain",
        "product_retailer_listings",
        ["product_id", "chain"],
    )
    op.create_index(
        "idx_listing_chain_last_seen",
        "product_retailer_listings",
        ["chain", "catalog_last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_listing_chain_last_seen", table_name="product_retailer_listings")
    op.drop_index("idx_listing_product_chain", table_name="product_retailer_listings")
    op.drop_constraint(
        "uq_listing_chain_external_id",
        "product_retailer_listings",
        type_="unique",
    )
    op.drop_table("product_retailer_listings")

"""drop legacy retailer columns from products table

Removes the seven retailer-specific columns that were dual-written during
the #49 migration and are no longer needed now that ProductRetailerListing
is the sole authoritative source of retailer metadata.

Revision ID: n9i0j1k2l3m4
Revises: m8h9i0j1k2l3, f66f8b6ede4b
Create Date: 2026-10-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "n9i0j1k2l3m4"
# Merges both live heads: the main #49 chain (m8h9i0j1k2l3) and the
# off-image-review branch (f66f8b6ede4b) that diverged from j5e6f7a8b9c0.
down_revision = ("m8h9i0j1k2l3", "f66f8b6ede4b")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the partial unique index on (external_source, external_id) first
    op.drop_index("uq_products_external_source_id", table_name="products", if_exists=True)

    op.drop_column("products", "external_id")
    op.drop_column("products", "external_source")
    op.drop_column("products", "external_image_url")
    op.drop_column("products", "package_size")
    op.drop_column("products", "in_stock")
    op.drop_column("products", "is_online_only")
    op.drop_column("products", "catalog_last_seen_at")


def downgrade() -> None:
    op.add_column("products", sa.Column("catalog_last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("products", sa.Column("is_online_only", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("products", sa.Column("in_stock", sa.Boolean(), server_default="true", nullable=False))
    op.add_column("products", sa.Column("package_size", sa.String(100), nullable=True))
    op.add_column("products", sa.Column("external_image_url", sa.String(500), nullable=True))
    op.add_column("products", sa.Column("external_source", sa.String(50), nullable=True))
    op.add_column("products", sa.Column("external_id", sa.String(64), nullable=True))

    op.create_index(
        "uq_products_external_source_id",
        "products",
        ["external_source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_source IS NOT NULL AND external_id IS NOT NULL"),
    )

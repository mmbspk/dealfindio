"""add package_size_norm to products

Adds Product.package_size_norm — a canonical normalized size string
(e.g. "375mL", "1.5L", "500g", "24x375mL") derived from the raw
package_size strings supplied by scrapers.

Backfills existing products from the best available
ProductRetailerListing.package_size_raw value per product.

Revision ID: o0j1k2l3m4n5
Revises: n9i0j1k2l3m4
Create Date: 2026-10-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "o0j1k2l3m4n5"
down_revision = "n9i0j1k2l3m4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("package_size_norm", sa.String(50), nullable=True))

    # Backfill: pick the most-recently-updated listing's package_size_raw per product
    # and run it through the Python normalizer.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT DISTINCT ON (product_id)
                product_id,
                package_size_raw
            FROM product_retailer_listings
            WHERE package_size_raw IS NOT NULL
            ORDER BY product_id, updated_at DESC NULLS LAST
            """
        )
    ).fetchall()

    if rows:
        from app.services.package_size import normalize_package_size

        updates = []
        for product_id, raw in rows:
            norm = normalize_package_size(raw)
            if norm:
                updates.append({"pid": str(product_id), "norm": norm})

        if updates:
            bind.execute(
                sa.text(
                    "UPDATE products SET package_size_norm = :norm WHERE id::text = :pid"
                ),
                updates,
            )


def downgrade() -> None:
    op.drop_column("products", "package_size_norm")

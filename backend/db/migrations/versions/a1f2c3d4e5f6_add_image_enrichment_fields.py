"""add_image_enrichment_fields

Adds image provenance columns to products and a partial index over rows
that still lack an image, so the OFT enrichment task can cheaply find work.

Revision ID: a1f2c3d4e5f6
Revises: 7992a9dcbef9
Create Date: 2026-09-02 05:10:00.000000

"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f2c3d4e5f6'
down_revision: str | None = '7992a9dcbef9'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('products', sa.Column('image_source', sa.String(length=50), nullable=True))
    op.add_column(
        'products',
        sa.Column('image_enriched_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index: only rows missing an image. Keeps the enricher's
    # "find products to work on" query fast even as the table grows.
    op.create_index(
        'idx_products_image_null',
        'products',
        ['id'],
        unique=False,
        postgresql_where=sa.text('image_url IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('idx_products_image_null', table_name='products')
    op.drop_column('products', 'image_enriched_at')
    op.drop_column('products', 'image_source')

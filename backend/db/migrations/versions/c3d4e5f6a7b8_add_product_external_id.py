"""add_product_external_id

Adds external_id (store SKU) + external_source to products so catalog crawls
can dedupe on a stable unique key instead of over-merging via fuzzy name match.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-02 07:30:00.000000

"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: str | None = 'b2c3d4e5f6a7'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('products', sa.Column('external_id', sa.String(length=64), nullable=True))
    op.add_column('products', sa.Column('external_source', sa.String(length=50), nullable=True))
    # Composite lookup for catalog dedup: "does source X already have SKU Y?".
    op.create_index(
        'idx_products_external',
        'products',
        ['external_source', 'external_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_products_external', table_name='products')
    op.drop_column('products', 'external_source')
    op.drop_column('products', 'external_id')

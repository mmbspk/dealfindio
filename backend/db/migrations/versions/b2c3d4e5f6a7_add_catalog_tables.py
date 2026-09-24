"""add_catalog_tables

Creates catalog_crawl_runs (audit/progress for full-catalog crawls) and adds
catalog_last_seen_at + in_stock to products.

Revision ID: b2c3d4e5f6a7
Revises: a1f2c3d4e5f6
Create Date: 2026-09-02 06:00:00.000000

"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: str | None = 'a1f2c3d4e5f6'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'catalog_crawl_runs',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('chain', sa.String(length=50), nullable=False),
        sa.Column('mode', sa.String(length=20), nullable=False, server_default='full'),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='running'),
        sa.Column('categories_found', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('categories_crawled', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('categories_skipped', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('products_found', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('products_new', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('products_updated', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('run_started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('run_finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('errors_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_catalog_run_chain', 'catalog_crawl_runs', ['chain'])
    op.create_index('idx_catalog_run_started', 'catalog_crawl_runs', ['run_started_at'])

    op.add_column(
        'products',
        sa.Column('catalog_last_seen_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'products',
        sa.Column('in_stock', sa.Boolean(), nullable=False, server_default='true'),
    )


def downgrade() -> None:
    op.drop_column('products', 'in_stock')
    op.drop_column('products', 'catalog_last_seen_at')
    op.drop_index('idx_catalog_run_started', table_name='catalog_crawl_runs')
    op.drop_index('idx_catalog_run_chain', table_name='catalog_crawl_runs')
    op.drop_table('catalog_crawl_runs')

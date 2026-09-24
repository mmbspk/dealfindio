"""add_package_size_unit_price

Revision ID: 7581dd367a9e
Revises: d4e5f6a7b8c9
Create Date: 2026-09-10 15:41:40.286517

"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7581dd367a9e'
down_revision: str | None = 'd4e5f6a7b8c9'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('products', sa.Column('package_size', sa.String(length=100), nullable=True))
    op.add_column('products', sa.Column('unit_price', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('products', 'unit_price')
    op.drop_column('products', 'package_size')

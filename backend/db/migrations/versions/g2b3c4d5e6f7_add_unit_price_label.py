"""add unit_price_label to products

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-14

"""
from alembic import op
import sqlalchemy as sa

revision = 'g2b3c4d5e6f7'
down_revision = 'f7e8d9c0b1a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('products', sa.Column('unit_price_label', sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column('products', 'unit_price_label')

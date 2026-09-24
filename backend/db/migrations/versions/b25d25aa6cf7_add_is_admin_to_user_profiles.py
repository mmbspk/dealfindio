"""add_is_admin_to_user_profiles

Revision ID: b25d25aa6cf7
Revises: e4f5a6b7c8d9
Create Date: 2026-09-06 14:42:20.990116

"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b25d25aa6cf7'
down_revision: str | None = 'e4f5a6b7c8d9'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('user_profiles', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('user_profiles', 'is_admin')

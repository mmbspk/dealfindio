"""widen_status_varchar_to_50

Revision ID: 7992a9dcbef9
Revises: d1ddbe5a0eef
Create Date: 2026-09-02 04:39:28.466158

"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7992a9dcbef9'
down_revision: str | None = 'd1ddbe5a0eef'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('flyer_scrape_runs', 'status',
               existing_type=sa.VARCHAR(length=20),
               type_=sa.String(length=50),
               existing_nullable=False)


def downgrade() -> None:
    op.alter_column('flyer_scrape_runs', 'status',
               existing_type=sa.String(length=50),
               type_=sa.VARCHAR(length=20),
               existing_nullable=False)

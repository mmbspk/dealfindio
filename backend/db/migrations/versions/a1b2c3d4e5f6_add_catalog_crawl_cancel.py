"""add_catalog_crawl_cancel

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-11 10:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "7581dd367a9e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_crawl_runs",
        sa.Column("celery_task_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalog_crawl_runs", "celery_task_id")

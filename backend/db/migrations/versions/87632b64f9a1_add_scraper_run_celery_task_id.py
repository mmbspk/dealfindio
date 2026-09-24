"""add_scraper_run_celery_task_id

Revision ID: 87632b64f9a1
Revises: a1b2c3d4e5f6
Create Date: 2026-09-11 10:30:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "87632b64f9a1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flyer_scrape_runs",
        sa.Column("celery_task_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("flyer_scrape_runs", "celery_task_id")

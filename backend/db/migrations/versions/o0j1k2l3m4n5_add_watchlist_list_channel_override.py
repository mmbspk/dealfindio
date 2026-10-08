"""add channel_override to watchlist_lists

Adds a nullable VARCHAR(10) column to watchlist_lists so users can override
the notification channel per list. NULL = inherit user-level defaults.
Valid non-null values: 'push', 'email', 'both', 'none'.

Revision ID: o0j1k2l3m4n5
Revises: m8h9i0j1k2l3
Create Date: 2026-10-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "o0j1k2l3m4n5"
down_revision = "m8h9i0j1k2l3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "watchlist_lists",
        sa.Column("channel_override", sa.String(10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("watchlist_lists", "channel_override")

"""Stub for missing migration k6f7a8b9c0d1 — repairs broken revision chain.

The migration file for k6f7a8b9c0d1 was lost (DB is already past this point
at l7g8h9i0j1k2). This stub lets Alembic rebuild its revision map without
making any schema changes.

Revision ID: k6f7a8b9c0d1
Revises: j5e6f7a8b9c0
Create Date: 2026-10-08 (stub created to repair chain)
"""

from alembic import op

revision = "k6f7a8b9c0d1"
down_revision = "j5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

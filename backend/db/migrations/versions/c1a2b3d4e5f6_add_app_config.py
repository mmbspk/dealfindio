"""Add app_config table for encrypted admin key/value store.

Revision ID: c1a2b3d4e5f6
Revises: a2b3c4d5e6f7
Create Date: 2026-09-08 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "c1a2b3d4e5f6"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("value_enc", sa.Text, nullable=True),
        sa.Column("is_secret", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("app_config")

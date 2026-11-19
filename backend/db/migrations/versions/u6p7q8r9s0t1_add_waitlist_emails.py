"""add waitlist_emails

Revision ID: u6p7q8r9s0t1
Revises: t5o6p7q8r9s0
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "u6p7q8r9s0t1"
down_revision = "t5o6p7q8r9s0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waitlist_emails",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
    )
    op.create_index("idx_waitlist_emails_email", "waitlist_emails", ["email"])
    op.create_index("idx_waitlist_emails_created_at", "waitlist_emails", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_waitlist_emails_created_at", table_name="waitlist_emails")
    op.drop_index("idx_waitlist_emails_email", table_name="waitlist_emails")
    op.drop_table("waitlist_emails")

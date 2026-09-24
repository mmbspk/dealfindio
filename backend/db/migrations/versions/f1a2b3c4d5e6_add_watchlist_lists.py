"""add_watchlist_lists

Revision ID: f1a2b3c4d5e6
Revises: b79a2870f343
Create Date: 2026-09-07 17:24:58.926093

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "b79a2870f343"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create watchlist_lists table
    op.create_table(
        "watchlist_lists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("emoji", sa.String(10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_watchlist_lists_user", "watchlist_lists", ["user_id"])

    # 2. Add list_id column to user_watchlist (nullable during migration)
    op.add_column(
        "user_watchlist",
        sa.Column("list_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_watchlist_list_id",
        "user_watchlist", "watchlist_lists",
        ["list_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_index("idx_watchlist_list", "user_watchlist", ["list_id"])

    # 3. Create a default "My Watchlist" list for each user who has items,
    #    then backfill list_id on their existing rows.
    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT INTO watchlist_lists (id, user_id, name, emoji)
        SELECT gen_random_uuid(), user_id, 'My Watchlist', '🛒'
        FROM (SELECT DISTINCT user_id FROM user_watchlist WHERE is_active = true) AS u
    """))
    conn.execute(sa.text("""
        UPDATE user_watchlist uw
        SET list_id = wl.id
        FROM watchlist_lists wl
        WHERE wl.user_id = uw.user_id
          AND uw.list_id IS NULL
    """))

    # 4. Swap the unique constraint: was (user_id, product_id), now (list_id, product_id)
    op.drop_constraint("unique_user_product_watchlist", "user_watchlist", type_="unique")
    op.create_unique_constraint(
        "unique_list_product_watchlist", "user_watchlist", ["list_id", "product_id"]
    )


def downgrade() -> None:
    op.drop_constraint("unique_list_product_watchlist", "user_watchlist", type_="unique")
    op.create_unique_constraint(
        "unique_user_product_watchlist", "user_watchlist", ["user_id", "product_id"]
    )
    op.drop_index("idx_watchlist_list", "user_watchlist")
    op.drop_constraint("fk_user_watchlist_list_id", "user_watchlist", type_="foreignkey")
    op.drop_column("user_watchlist", "list_id")
    op.drop_index("idx_watchlist_lists_user", "watchlist_lists")
    op.drop_table("watchlist_lists")

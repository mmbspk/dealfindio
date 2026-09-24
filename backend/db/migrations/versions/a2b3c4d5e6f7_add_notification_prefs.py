"""add_notification_prefs

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-08 10:00:00.000000

Adds per-channel notification preferences to user_profiles and channel tracking
to notifications_log.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # user_profiles: push preferences (default True — preserve existing behaviour)
    op.add_column("user_profiles", sa.Column("notif_push_price_match", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("user_profiles", sa.Column("notif_push_pct_off", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("user_profiles", sa.Column("notif_push_prediction", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("user_profiles", sa.Column("notif_push_flash_sale", sa.Boolean(), nullable=False, server_default="true"))

    # user_profiles: email preferences (default False — opt-in only)
    op.add_column("user_profiles", sa.Column("notif_email_price_match", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("user_profiles", sa.Column("notif_email_pct_off", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("user_profiles", sa.Column("notif_email_prediction", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("user_profiles", sa.Column("notif_email_weekly_digest", sa.Boolean(), nullable=False, server_default="true"))

    # user_profiles: quiet hours + timezone
    op.add_column("user_profiles", sa.Column("notif_quiet_hours_start", sa.Integer(), nullable=True))
    op.add_column("user_profiles", sa.Column("notif_quiet_hours_end", sa.Integer(), nullable=True))
    op.add_column("user_profiles", sa.Column("notif_timezone", sa.String(50), nullable=False, server_default="Australia/Sydney"))

    # notifications_log: channel + email message id
    op.add_column("notifications_log", sa.Column("channel", sa.String(20), nullable=False, server_default="push"))
    op.add_column("notifications_log", sa.Column("email_message_id", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications_log", "email_message_id")
    op.drop_column("notifications_log", "channel")
    op.drop_column("user_profiles", "notif_timezone")
    op.drop_column("user_profiles", "notif_quiet_hours_end")
    op.drop_column("user_profiles", "notif_quiet_hours_start")
    op.drop_column("user_profiles", "notif_email_weekly_digest")
    op.drop_column("user_profiles", "notif_email_prediction")
    op.drop_column("user_profiles", "notif_email_pct_off")
    op.drop_column("user_profiles", "notif_email_price_match")
    op.drop_column("user_profiles", "notif_push_flash_sale")
    op.drop_column("user_profiles", "notif_push_prediction")
    op.drop_column("user_profiles", "notif_push_pct_off")
    op.drop_column("user_profiles", "notif_push_price_match")

"""
Notification model for user alerts and messaging.

Records notifications sent to users for price alerts, predictions, and watchlist events.
Tracks delivery status and user engagement (opened_at).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Notification(Base):
    """
    Notification record for user alerts.

    Stores notification metadata including trigger type, message content,
    Firebase Cloud Messaging status, and user engagement metrics.

    Attributes:
        user_id: Foreign key to UserProfile
        watchlist_id: Optional foreign key to UserWatchlist that triggered alert
        price_report_id: Optional foreign key to PriceReport that triggered alert
        trigger_type: Type of trigger ('price_match', 'pct_off_match', 'prediction_drop')
        message_title: Notification title
        message_body: Notification body text
        fcm_message_id: Firebase Cloud Messaging message ID
        sent_at: Timestamp when notification was sent
        opened_at: Timestamp when user opened notification (NULL if not opened)
        is_delivered: Whether FCM confirmed delivery
        user: Relationship to UserProfile
        watchlist_item: Relationship to triggering UserWatchlist
        price_report: Relationship to triggering PriceReport
    """

    __tablename__ = "notifications_log"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE")
    )
    watchlist_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_watchlist.id"), nullable=True
    )
    price_report_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("price_reports.id"), nullable=True
    )
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message_title: Mapped[str] = mapped_column(String(255), nullable=False)
    message_body: Mapped[str] = mapped_column(String(1000), nullable=False)
    fcm_message_id: Mapped[str | None] = mapped_column(String(500))
    email_message_id: Mapped[str | None] = mapped_column(String(500))
    channel: Mapped[str] = mapped_column(String(20), default="push")  # push | email | in_app
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_delivered: Mapped[bool] = mapped_column(default=False)

    # Relationships
    user: Mapped["UserProfile"] = relationship(
        back_populates="notifications",
        foreign_keys=[user_id],
    )
    watchlist_item: Mapped["UserWatchlist | None"] = relationship(
        foreign_keys=[watchlist_id],
    )
    price_report: Mapped["PriceReport | None"] = relationship(
        back_populates="notifications",
        foreign_keys=[price_report_id],
    )

    __table_args__ = (Index("idx_notif_user", "user_id", "sent_at"),)

    def __repr__(self) -> str:
        return f"<Notification user_id={self.user_id} trigger_type={self.trigger_type} id={self.id}>"

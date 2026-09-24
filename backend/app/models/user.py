"""
User and user profile models for authentication and preferences.

UserProfile stores Firebase authentication data and user preferences.
User is a lightweight DTO for dependency injection (not mapped to DB).
"""


from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class UserProfile(Base):
    """
    User profile with authentication and preference data.

    Stores Firebase UID for authentication, user preferences (location, search radius),
    and gamification metrics (contribution count, reputation score).
    FCM token used for push notifications on supported devices.

    Attributes:
        firebase_uid: Firebase authentication UID (unique)
        email: User email address
        display_name: User's chosen display name
        fcm_token: Latest Firebase Cloud Messaging device token
        home_lat: Default home latitude for location-based searches
        home_lon: Default home longitude for location-based searches
        default_radius_km: Default search radius in kilometers
        contribution_count: Number of price reports contributed
        reputation_score: Reputation score (0.0-5.0) for weighting contributions
        price_reports: Relationship to price reports from this user
        watchlist: Relationship to user's product watchlist
        notifications: Relationship to user's notifications
    """

    __tablename__ = "user_profiles"

    firebase_uid: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    fcm_token: Mapped[str | None] = mapped_column(String(500))
    home_lat: Mapped[float | None]
    home_lon: Mapped[float | None]
    default_radius_km: Mapped[int] = mapped_column(default=10)
    contribution_count: Mapped[int] = mapped_column(default=0)
    reputation_score: Mapped[float] = mapped_column(default=1.0)
    is_admin: Mapped[bool] = mapped_column(default=False)

    # Notification channel preferences — push
    notif_push_price_match: Mapped[bool] = mapped_column(default=True)
    notif_push_pct_off: Mapped[bool] = mapped_column(default=True)
    notif_push_prediction: Mapped[bool] = mapped_column(default=True)
    notif_push_flash_sale: Mapped[bool] = mapped_column(default=True)
    # Notification channel preferences — email
    notif_email_price_match: Mapped[bool] = mapped_column(default=False)
    notif_email_pct_off: Mapped[bool] = mapped_column(default=False)
    notif_email_prediction: Mapped[bool] = mapped_column(default=False)
    notif_email_weekly_digest: Mapped[bool] = mapped_column(default=True)
    # Quiet hours (local hour 0-23, inclusive range; NULL = disabled)
    notif_quiet_hours_start: Mapped[int | None]
    notif_quiet_hours_end: Mapped[int | None]
    notif_timezone: Mapped[str] = mapped_column(String(50), default="Australia/Sydney")

    # Relationships
    price_reports: Mapped[list["PriceReport"]] = relationship(
        back_populates="reporter",
        cascade="all, delete-orphan",
        foreign_keys="PriceReport.reporter_user_id",
    )
    watchlist: Mapped[list["UserWatchlist"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserWatchlist.user_id",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="Notification.user_id",
    )

    __table_args__ = (Index("idx_user_profiles_firebase_uid", "firebase_uid"),)

    def __repr__(self) -> str:
        return f"<UserProfile firebase_uid={self.firebase_uid} email={self.email} id={self.id}>"


class User:
    """
    Lightweight user DTO for dependency injection.

    Not mapped to database. Used in FastAPI dependencies and request handlers
    to represent the current authenticated user context.

    Attributes:
        firebase_uid: Firebase authentication UID
        email: User email address
        profile: Associated UserProfile from database (optional)
    """

    def __init__(
        self, firebase_uid: str, email: str, user_profile: UserProfile | None = None
    ):
        """
        Initialize User DTO.

        Args:
            firebase_uid: Firebase authentication UID
            email: User email address
            user_profile: Optional associated UserProfile from database
        """
        self.firebase_uid = firebase_uid
        self.email = email
        self.profile = user_profile
        self.is_admin: bool = user_profile.is_admin if user_profile else False

    def __repr__(self) -> str:
        return f"<User firebase_uid={self.firebase_uid} email={self.email}>"

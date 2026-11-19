"""
WaitlistEmail model for pre-launch email capture.

Stores email addresses submitted via the dealfindio.com.au landing page
so the team can notify subscribers at launch.
"""

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class WaitlistEmail(Base):
    """
    Pre-launch waitlist subscriber.

    Stores an email address submitted via the marketing landing page.
    Duplicate submissions are silently ignored at the API layer.
    """

    __tablename__ = "waitlist_emails"

    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True, index=True)

    __table_args__ = (Index("idx_waitlist_emails_created_at", "created_at"),)

    def __repr__(self) -> str:
        return f"<WaitlistEmail email={self.email} id={self.id}>"

"""
WatchlistList model — named collections of watched products.

Each user can have multiple named lists (e.g. "Weekly shop", "Price watch").
UserWatchlist items now belong to a WatchlistList via list_id.
"""

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class WatchlistList(Base):
    """A named list of products the user is tracking."""

    __tablename__ = "watchlist_lists"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    emoji: Mapped[str | None] = mapped_column(String(10))

    items: Mapped[list["UserWatchlist"]] = relationship(  # noqa: F821
        back_populates="list",
        foreign_keys="UserWatchlist.list_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("idx_watchlist_lists_user", "user_id"),)

    def __repr__(self) -> str:
        return f"<WatchlistList id={self.id} name={self.name!r} user_id={self.user_id}>"

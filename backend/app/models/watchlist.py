"""
User watchlist model for price tracking.

Allows users to track products and receive alerts when prices drop to target levels
or when they go on sale. Each item belongs to a named WatchlistList.
"""

from uuid import UUID

from sqlalchemy import (
    ARRAY,
    JSON,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

_PREFERRED_CHAINS_TYPE = ARRAY(String).with_variant(JSON(), "sqlite")


class UserWatchlist(Base):
    """
    User product watchlist for price tracking.

    Each item belongs to a named WatchlistList. The same product can appear
    in multiple lists (unique constraint is now per list+product, not per user+product).
    """

    __tablename__ = "user_watchlist"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"))
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    list_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("watchlist_lists.id", ondelete="CASCADE"), nullable=True
    )
    target_price_cents: Mapped[int | None]
    target_pct_off: Mapped[float | None]
    alert_on_prediction: Mapped[bool] = mapped_column(default=False)
    preferred_chains: Mapped[list[str] | None] = mapped_column(_PREFERRED_CHAINS_TYPE)
    is_active: Mapped[bool] = mapped_column(default=True)

    user: Mapped["UserProfile"] = relationship(  # noqa: F821
        back_populates="watchlist",
        foreign_keys=[user_id],
    )
    product: Mapped["Product"] = relationship(  # noqa: F821
        back_populates="watchlist_items",
        foreign_keys=[product_id],
    )
    list: Mapped["WatchlistList | None"] = relationship(  # noqa: F821
        back_populates="items",
        foreign_keys=[list_id],
    )

    __table_args__ = (
        UniqueConstraint("list_id", "product_id", name="unique_list_product_watchlist"),
        Index("idx_watchlist_user", "user_id"),
        Index("idx_watchlist_product", "product_id"),
        Index("idx_watchlist_list", "list_id"),
    )

    def __repr__(self) -> str:
        return f"<UserWatchlist user_id={self.user_id} product_id={self.product_id} id={self.id}>"

"""
Store model for grocery retailers and their locations.

Models physical store locations for major chains (Coles, Woolworths, Aldi, IGA).
Stores are identified by chain, location coordinates, and place IDs for deduplication.
"""

from uuid import UUID

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Store(Base):
    """
    Physical grocery store location.

    Represents a single store location for a major chain. Includes geographic
    coordinates and Google Places ID for deduplication and mapping integration.

    Attributes:
        chain: Grocery chain name ('coles', 'woolworths', 'aldi', 'iga')
        name: Store display name (e.g., "Coles Bondi")
        address: Street address
        suburb: Suburb/city name
        state: Australian state code ('NSW', 'VIC', 'QLD', etc.)
        postcode: Postal code
        lat: Latitude coordinate for geographic queries
        lon: Longitude coordinate for geographic queries
        place_id: Google Places ID for deduplication and external linking
        is_active: Whether store is currently active/open
        price_reports: Relationship to price reports from this store
    """

    __tablename__ = "stores"

    chain: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    suburb: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(10))
    postcode: Mapped[str | None] = mapped_column(String(10))
    lat: Mapped[float | None]
    lon: Mapped[float | None]
    place_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships
    price_reports: Mapped[list["PriceReport"]] = relationship(
        back_populates="store",
        cascade="all, delete-orphan",
        foreign_keys="PriceReport.store_id",
    )

    __table_args__ = (
        Index("idx_stores_chain", "chain"),
        Index("idx_stores_location", "lat", "lon"),
    )

    def __repr__(self) -> str:
        return f"<Store chain={self.chain} name={self.name} id={self.id}>"

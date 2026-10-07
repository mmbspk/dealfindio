"""
Product model for grocery items.

Models grocery products with identifiers (GTIN), brand, category, and unit information.
Products are shared across stores and price reports.
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Product(Base):
    """
    Grocery product with standard identifiers and metadata.

    Represents a unique grocery item (e.g., "Coca-Cola 600ml"). Products are
    identified by GTIN (EAN-13) and shared across stores. Includes category,
    brand, and unit size information for filtering and normalization.

    Attributes:
        gtin: Global Trade Item Number (EAN-13, 14-digit left-padded)
        name: Product name/description
        brand: Manufacturer/brand name
        category: Product category (e.g., 'dairy', 'bakery', 'produce')
        unit_type: Unit of measurement ('ea', 'kg', 'L', 'g', 'ml')
        unit_size: Quantity per unit (e.g., 2.0 for 2L bottles)
        image_url: Self-hosted image URL written by the OFF enrichment pipeline (NULL until enriched)
        external_image_url: Original retailer CDN URL scraped by catalog crawlers (provenance only)
        image_source: Provenance of image_url ('open_food_facts')
        image_enriched_at: When the image was last (re)sourced by the enricher
        price_reports: Relationship to price reports for this product
        watchlist_items: Relationship to user watchlist entries
        predictions: Relationship to price predictions
    """

    __tablename__ = "products"

    gtin: Mapped[str | None] = mapped_column(String(14), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(100))
    unit_type: Mapped[str | None] = mapped_column(String(20))
    unit_size: Mapped[float | None]
    package_size: Mapped[str | None] = mapped_column(String(100))
    unit_price: Mapped[float | None]  # comparable price per standard unit (e.g. per 100g)
    unit_price_label: Mapped[str | None] = mapped_column(String(50))  # e.g. "100g", "100ml", "ea"
    image_url: Mapped[str | None] = mapped_column(String(500))
    external_image_url: Mapped[str | None] = mapped_column(String(500))
    image_source: Mapped[str | None] = mapped_column(String(50))
    image_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # OFF image review queue — new enrichment results land here as 'pending'
    # and only graduate to image_url once approved (manually or by AI agent).
    off_image_url: Mapped[str | None] = mapped_column(String(500))
    off_image_score: Mapped[float | None]
    off_image_off_code: Mapped[str | None] = mapped_column(String(50))
    off_image_off_name: Mapped[str | None] = mapped_column(String(300))
    off_image_status: Mapped[str | None] = mapped_column(String(20))  # pending|approved|rejected
    off_image_ai_verdict: Mapped[str | None] = mapped_column(
        String(20)
    )  # approved|rejected|skipped
    off_image_ai_reason: Mapped[str | None] = mapped_column(String(300))
    off_image_ai_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set by the full-catalog crawler: when this product was last seen in a
    # catalog crawl, and whether it was in stock at that time.
    catalog_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_online_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # The store's own SKU/stockcode + which store it came from. Together these
    # form a stable unique key for catalog dedup (preferred over fuzzy name match).
    external_id: Mapped[str | None] = mapped_column(String(64))
    external_source: Mapped[str | None] = mapped_column(String(50))
    # 384-dim sentence embedding of the normalised product name (all-MiniLM-L6-v2).
    # NULL until the embedding backfill task runs.
    name_embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)

    # Relationships
    price_reports: Mapped[list["PriceReport"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        foreign_keys="PriceReport.product_id",
    )
    watchlist_items: Mapped[list["UserWatchlist"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        foreign_keys="UserWatchlist.product_id",
    )
    predictions: Mapped[list["PricePrediction"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        foreign_keys="PricePrediction.product_id",
    )
    retailer_listings: Mapped[list["ProductRetailerListing"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        foreign_keys="ProductRetailerListing.product_id",
    )

    __table_args__ = (
        Index("idx_products_gtin", "gtin"),
        Index("idx_products_name", "name"),
        Index("idx_products_off_image_status", "off_image_status"),
    )

    def __repr__(self) -> str:
        return f"<Product name={self.name} gtin={self.gtin} id={self.id}>"

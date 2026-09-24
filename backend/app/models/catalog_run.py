"""
Catalog crawl run model — audit + progress for full/delta catalog crawls.

Parallel to ``FlyScrapeRun`` but for the authenticated full-catalog scraper
(Feature B), with the extra category-level counters the crawl produces.
"""

from datetime import datetime

from sqlalchemy import Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class CatalogCrawlRun(Base):
    """Metadata for one authenticated catalog crawl (full or delta).

    Attributes:
        chain: Chain crawled ('coles', 'woolworths', …)
        mode: 'full' (every category) or 'delta' (changed categories only)
        status: 'running', 'completed', 'completed_with_errors', 'failed'
        categories_found: Categories discovered
        categories_crawled: Categories actually parsed this run
        categories_skipped: Categories skipped in delta mode (unchanged)
        products_found: Total products parsed
        products_new: Products newly created
        products_updated: Existing products whose fields were refreshed
        run_started_at / run_finished_at: Timing (finished NULL while running)
        errors_json: Structured error log
    """

    __tablename__ = "catalog_crawl_runs"

    chain: Mapped[str] = mapped_column(String(50), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="full")
    status: Mapped[str] = mapped_column(String(50), default="running")
    categories_found: Mapped[int] = mapped_column(default=0)
    categories_crawled: Mapped[int] = mapped_column(default=0)
    categories_skipped: Mapped[int] = mapped_column(default=0)
    products_found: Mapped[int] = mapped_column(default=0)
    products_new: Mapped[int] = mapped_column(default=0)
    products_updated: Mapped[int] = mapped_column(default=0)
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    run_started_at: Mapped[datetime] = mapped_column(nullable=False)
    run_finished_at: Mapped[datetime | None]
    errors_json: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        Index("idx_catalog_run_chain", "chain"),
        Index("idx_catalog_run_started", "run_started_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<CatalogCrawlRun chain={self.chain} mode={self.mode} "
            f"status={self.status} id={self.id}>"
        )

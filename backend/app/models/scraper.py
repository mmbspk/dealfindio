"""
Flyer scraper run model for tracking automated price collection.

Records metadata for each automated flyer scraping run (start/end times, status,
error logs). Used to monitor scraper health and troubleshoot collection issues.
"""

from datetime import datetime

from sqlalchemy import Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class FlyScrapeRun(Base):
    """
    Automated flyer scraping run metadata.

    Records when the automated flyer scrapers run, including start/end times,
    status, number of products found, and any errors encountered.

    Used for monitoring scraper health and debugging data collection issues.

    Attributes:
        chain: Chain scraped ('coles', 'woolworths', 'aldi', 'iga')
        run_started_at: When the scrape run started
        run_finished_at: When the scrape run completed (NULL if still running)
        status: Run status ('running', 'success', 'partial', 'failed')
        products_found: Total products found in this run
        products_new: Number of new products added in this run
        errors_json: Structured error log as JSON dict
        raw_source_url: URL of scraped source (if applicable)
    """

    __tablename__ = "flyer_scrape_runs"

    chain: Mapped[str] = mapped_column(String(50), nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    run_started_at: Mapped[datetime] = mapped_column(nullable=False)
    run_finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(50), default="running")
    products_found: Mapped[int] = mapped_column(default=0)
    products_new: Mapped[int] = mapped_column(default=0)
    errors_json: Mapped[dict | None] = mapped_column(JSON)
    raw_source_url: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        Index("idx_scrape_chain", "chain"),
        Index("idx_scrape_started", "run_started_at"),
    )

    def __repr__(self) -> str:
        return f"<FlyScrapeRun chain={self.chain} status={self.status} started={self.run_started_at} id={self.id}>"

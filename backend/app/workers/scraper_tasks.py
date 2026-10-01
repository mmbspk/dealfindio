"""
Scraper worker tasks.

Periodic tasks for expiring stale specials.
All chain crawling is handled by catalog_tasks.run_catalog_crawl.
"""

import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.workers.scraper_tasks.expire_stale_specials",
    queue="scraper",
    max_retries=3,
    default_retry_delay=60,
)
def expire_stale_specials(self, chains: list[str] | None = None) -> dict:
    """
    Mark expired weekly specials as is_on_sale=False.

    Runs just before each chain's new catalogue goes live so customers never
    see last week's deals as current.  Scoped to catalog-sourced PriceReports
    (source IN chains).

    Args:
        chains: Optional list of source names to expire. Defaults to ["catalog_aldi"].

    Returns:
        dict: { chain: rows_expired } for each chain processed.
    """
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.deps import _SessionLocal
    from app.models import PriceReport

    if not _SessionLocal:
        raise RuntimeError("Database not initialized")

    target_chains = chains or ["catalog_aldi"]
    now = datetime.now(timezone.utc)
    result: dict[str, int] = {}

    session = _SessionLocal()
    try:
        for chain in target_chains:
            stmt = (
                update(PriceReport)
                .where(
                    PriceReport.is_on_sale == True,
                    PriceReport.source == chain,
                    PriceReport.valid_to != None,
                    PriceReport.valid_to < now,
                )
                .values(is_on_sale=False)
                .execution_options(synchronize_session=False)
            )
            rows = session.execute(stmt).rowcount
            result[chain] = rows
            logger.info("Expired %d stale specials for %s", rows, chain)

        session.commit()
        logger.info("expire_stale_specials complete: %s", result)
        return result

    except Exception as exc:
        session.rollback()
        logger.error("expire_stale_specials failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
    finally:
        session.close()

"""
Flyer scraper worker tasks.

Handles automated scraping of grocery store flyers (Coles, Woolworths, Aldi, IGA)
to extract product prices and promotions.

These tasks run on the 'scraper' queue with limited concurrency (1 worker)
to avoid overwhelming target servers.

Implements Task #12: Celery task wiring for scraper execution.
"""

import asyncio
import json
import logging
from asyncio import CancelledError
from datetime import datetime, timezone
from typing import TypedDict
from uuid import uuid4

from celery import group

from app.config import get_settings
from app.models import FlyScrapeRun
from app.services.product_dedup import DuplicateError, ingest_scraped_price
from app.workers.celery_app import celery_app
from app.workers.tasks_base import CallbackTask
from scrapers import ALDIScraper, ScraperError

PROGRESS_TTL = 3600  # seconds — key lives 1 hour after task finishes


def _set_progress(redis_client, task_id: str, pct: int, stage: str, message: str) -> None:
    """Write a progress snapshot to Redis so the API can poll it."""
    try:
        redis_client.setex(
            f"scrape_progress:{task_id}",
            PROGRESS_TTL,
            json.dumps({"pct": pct, "stage": stage, "message": message}),
        )
    except Exception:
        pass  # never let progress reporting crash the task


logger = logging.getLogger(__name__)
settings = get_settings()


class ScrapResult(TypedDict):
    """Result dictionary for scraper tasks."""

    products_found: int
    products_new: int
    status: str
    chain: str
    duplicates: int
    errors: list[str]


def get_db_session():
    """Get a synchronous database session for use in Celery tasks."""
    from app.deps import _SessionLocal

    if not _SessionLocal:
        raise RuntimeError("Database not initialized")
    return _SessionLocal()


def get_async_session():
    """Get async session context manager for ingest operations."""
    from app.deps import _AsyncSessionLocal

    if not _AsyncSessionLocal:
        raise RuntimeError("Async DB session not initialized")
    return _AsyncSessionLocal()


def get_redis():
    """Get Redis client for use in Celery tasks."""
    from app.deps import _redis_pool

    if not _redis_pool:
        raise RuntimeError("Redis not initialized")
    return _redis_pool


@celery_app.task(
    bind=True,
    name="app.workers.scraper_tasks.run_flyer_scrape",
    queue="catalog",
    base=CallbackTask,
    time_limit=3600,
    soft_time_limit=3000,
    max_retries=2,
    default_retry_delay=300,
)
def run_flyer_scrape(self, chain: str) -> ScrapResult:
    """
    Run flyer scraping for a single grocery chain.

    This is a periodic task that runs every Sunday evening to scrape the latest
    flyers from Australian grocery stores. Each chain gets scraped sequentially
    to avoid overwhelming their servers.

    Args:
        chain: Grocery chain identifier. Must be one of:
            - 'coles': Coles supermarket
            - 'woolworths': Woolworths supermarket
            - 'aldi': Aldi discount supermarket
            - 'iga': Independent Grocers Australia

    Returns:
        ScrapResult dict with:
            - products_found: Total products in scraped flyer
            - products_new: New products not previously in database
            - status: 'success', 'partial', or 'error'
            - chain: The chain that was scraped
            - duplicates: Number of duplicate/skipped products
            - errors: List of error messages during scraping

    Raises:
        ValueError: If chain is not recognized
        ScraperError: On critical scraper failure (will retry)

    Implementation notes:
        - Uses dedicated scraper queue (1 worker) to avoid overwhelming servers
        - Long timeout (1 hour) for slow flyers
        - Retry on network errors (max 2 times)
        - Integrates with database to store product updates
        - Logs detailed scraping statistics
        - Creates FlyScrapeRun record for audit trail
    """
    logger.info(f"Starting flyer scrape for {chain}", extra={"task_id": self.request.id})

    task_id = self.request.id
    session = None
    redis = None
    scrape_run = None
    errors = []
    products_found = 0
    products_new = 0
    duplicates = 0
    scraper_errors: list[str] = []

    try:
        # Validate chain parameter — only ALDI remains in the flyer pipeline.
        # Coles, Woolworths, IGA, and Drakes now use run_catalog_crawl(mode="specials").
        valid_chains = {"aldi"}
        if chain not in valid_chains:
            raise ValueError(f"Invalid chain: {chain}. Must be one of {valid_chains}")

        # Get connections
        session = get_db_session()
        redis = get_redis()

        _set_progress(redis, task_id, 5, "starting", f"Initialising {chain} scraper…")

        # Guard against re-delivery duplicates (same logic as catalog_tasks).
        existing_run = session.query(FlyScrapeRun).filter_by(celery_task_id=task_id).first()
        if existing_run:
            scrape_run = existing_run
            scrape_run.status = "running"
            session.commit()
        else:
            scrape_run = FlyScrapeRun(
                id=uuid4(),
                chain=chain,
                status="running",
                celery_task_id=task_id,
                run_started_at=datetime.now(timezone.utc),
            )
            session.add(scrape_run)
            session.commit()

        logger.info(f"Created scrape run {scrape_run.id} for {chain}")

        # Only ALDI uses the flyer pipeline.
        scraper_map = {
            "aldi": ALDIScraper,
        }

        ScraperClass = scraper_map.get(chain)
        if not ScraperClass:
            raise ValueError(f"Unknown chain: {chain}")

        _set_progress(redis, task_id, 10, "scraping", f"Opening {chain} flyer page…")

        # Check for cancellation before the long HTTP scrape begins.
        if redis.exists(f"scrape_cancel:{task_id}"):
            raise CancelledError(f"Scrape cancelled before start (chain={chain})")

        # Instantiate and run scraper
        logger.debug(f"Instantiating {ScraperClass.__name__} scraper")
        scraper = ScraperClass(db_session=session, redis_client=redis)

        # Run async scraper with asyncio.run
        logger.debug(f"Running async scraper for {chain}")
        _set_progress(redis, task_id, 15, "scraping", f"Fetching products from {chain}…")
        scraper_result = asyncio.run(scraper.run())

        products_found = scraper_result.get("products_found", 0)
        scraped_products = scraper_result.get("products", [])
        scraper_errors = scraper_result.get("errors", [])

        _set_progress(
            redis,
            task_id,
            50,
            "ingesting",
            f"Scraped {products_found} products — ingesting into database…",
        )

        # Ingest each scraped product using an async session (ingest_scraped_price uses AsyncSession)
        products_new = 0
        duplicates = 0
        total = len(scraped_products)

        async def _ingest_all():
            nonlocal products_new, duplicates
            async with get_async_session() as async_session:
                for i, sp in enumerate(scraped_products):
                    try:
                        result = await asyncio.wait_for(
                            ingest_scraped_price(
                                product_name=sp.product_name,
                                price_cents=sp.price_cents,
                                was_price_cents=sp.was_price_cents,
                                chain=chain,
                                valid_from=sp.valid_from,
                                gtin=sp.gtin,
                                session=async_session,
                                redis=redis,
                                skip_fuzzy=True,
                                store_product_id=sp.store_product_id,
                            ),
                            timeout=30.0,
                        )
                        if result:
                            products_new += 1
                    except asyncio.TimeoutError:
                        errors.append(f"Ingest timed out for {sp.product_name!r} (>30s), skipping")
                        logger.warning("Ingest timed out for %r, skipping", sp.product_name)
                    except DuplicateError:
                        duplicates += 1
                    except Exception as ingest_err:
                        errors.append(f"Price ingestion failed: {ingest_err}")
                        logger.warning(f"Ingest error for {sp.product_name}: {ingest_err}")

                    # Check cancel flag every product (not just every 10) so a
                    # previously-timed-out ingest doesn't delay cancellation.
                    if redis.exists(f"scrape_cancel:{task_id}"):
                        raise CancelledError(f"Scrape cancelled during ingest (chain={chain})")

                    if total > 0 and (i % 10 == 0 or i == total - 1):
                        ingest_pct = 50 + int(45 * (i + 1) / total)
                        _set_progress(
                            redis,
                            task_id,
                            ingest_pct,
                            "ingesting",
                            f"Ingesting {i + 1}/{total} — {products_new} new, {duplicates} duplicates",
                        )
                await async_session.commit()

        asyncio.run(_ingest_all())

        all_errors = scraper_errors + errors

        logger.info(
            f"Scraper {chain} completed",
            extra={
                "products_found": products_found,
                "products_new": products_new,
                "duplicates": duplicates,
                "errors_count": len(all_errors),
            },
        )

        # Update scrape run record
        scrape_run.status = "completed" if not all_errors else "completed_with_errors"
        scrape_run.products_found = products_found
        scrape_run.products_new = products_new
        scrape_run.run_finished_at = datetime.now(timezone.utc)
        if all_errors:
            scrape_run.errors_json = {"errors": all_errors[:50]}
        session.commit()

        final_msg = (
            f"Done — {products_found} found, {products_new} new, {duplicates} duplicates"
            + (f", {len(all_errors)} errors" if all_errors else "")
        )
        _set_progress(redis, task_id, 100, "done", final_msg)
        redis.delete(f"scrape_active:{chain}")

        # Kick off OFF image enrichment for the products this scrape created.
        # Chained (not inline) so the slow, polite OFF crawl runs on its own
        # queue without holding up the scraper. Best-effort — never fail the
        # scrape if queueing the follow-up task hiccups.
        if products_new > 0 and settings.OFT_ENRICH_ENABLED:
            try:
                from app.workers.image_tasks import enrich_product_images

                enrich_product_images.apply_async(args=[chain], queue="image", countdown=60)
                logger.info("Queued image enrichment for %s", chain)
            except Exception as enqueue_err:
                logger.warning("Failed to queue image enrichment: %s", enqueue_err)

        result = {
            "products_found": products_found,
            "products_new": products_new,
            "status": "success" if not all_errors else "partial",
            "chain": chain,
            "duplicates": duplicates,
            "errors": all_errors,
        }

        # Log alerts for critical failures
        if all_errors:
            logger.warning(f"Scraper {chain} had errors", extra={"errors": all_errors})
            if settings.SLACK_WEBHOOK_URL:
                try:
                    import requests

                    requests.post(
                        settings.SLACK_WEBHOOK_URL,
                        json={
                            "text": f"Scraper {chain} completed with errors: {', '.join(all_errors[:3])}"
                        },
                        timeout=5,
                    )
                except Exception as e:
                    logger.error(f"Failed to send Slack alert: {e}")

        return result

    except CancelledError as e:
        logger.info(f"Scrape cancelled for {chain}: {e}")
        cancel_msg = f"Cancelled after ingesting {products_new} new / {products_found} found"
        if redis:
            _set_progress(
                redis,
                task_id,
                100,
                "cancelled",
                f"Cancelled — {products_found} found, {products_new} new so far",
            )
            redis.delete(f"scrape_active:{chain}")
            redis.delete(f"scrape_cancel:{task_id}")
        if scrape_run and session:
            scrape_run.status = "cancelled"
            scrape_run.run_finished_at = datetime.now(timezone.utc)
            scrape_run.products_found = products_found
            scrape_run.products_new = products_new
            all_cancel_errors = (scraper_errors or []) + errors + [cancel_msg]
            scrape_run.errors_json = {"errors": all_cancel_errors[:50]}
            try:
                session.commit()
            except Exception:
                pass
        return {
            "products_found": products_found,
            "products_new": products_new,
            "status": "cancelled",
            "chain": chain,
            "duplicates": duplicates,
            "errors": [cancel_msg],
        }

    except ScraperError as e:
        logger.error(f"Scraper error for {chain}: {e}", exc_info=True)
        if redis:
            _set_progress(redis, task_id, 100, "failed", f"Scraper error: {e}")
            redis.delete(f"scrape_active:{chain}")
        if scrape_run:
            scrape_run.status = "failed"
            scrape_run.errors_json = {"error": str(e)}
            scrape_run.run_finished_at = datetime.now(timezone.utc)
            try:
                session.commit()
            except Exception as commit_error:
                logger.error(f"Failed to update scrape_run: {commit_error}")

        # Retry on scraper errors
        raise self.retry(exc=e, countdown=300, max_retries=2)

    except Exception as e:
        logger.exception(f"Unexpected error during scraping for {chain}: {e}")
        if redis:
            _set_progress(redis, task_id, 100, "failed", f"Unexpected error: {e}")
            redis.delete(f"scrape_active:{chain}")
        if scrape_run:
            scrape_run.status = "failed"
            scrape_run.errors_json = {"error": str(e)}
            scrape_run.run_finished_at = datetime.now(timezone.utc)
            try:
                session.commit()
            except Exception as commit_error:
                logger.error(f"Failed to update scrape_run: {commit_error}")

        # Retry on unexpected errors
        raise self.retry(exc=e, countdown=300, max_retries=2)

    finally:
        if session:
            session.close()


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
    see last week's deals as current.  Scoped to scraper-sourced PriceReports
    only (source IN ('coles','woolworths','aldi','iga')) — community reports
    and catalog prices are intentionally left untouched.

    Args:
        chains: Optional list of chain names to expire. Defaults to all four.
                Pass ['aldi'] to expire only the mid-week ALDI Specialbuys.

    Returns:
        dict: { chain: rows_expired } for each chain processed.
    """
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.deps import _SessionLocal
    from app.models import PriceReport

    if not _SessionLocal:
        raise RuntimeError("Database not initialized")

    # Only ALDI flyer-sourced specials need expiry here.
    # Coles/Woolworths/IGA/Drakes specials are managed by catalog ingest (valid_to set at parse time).
    target_chains = chains or ["aldi"]
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


@celery_app.task(
    bind=True,
    name="app.workers.scraper_tasks.run_all_scrapers",
    queue="scraper",
    base=CallbackTask,
    time_limit=14400,
    soft_time_limit=14100,
    max_retries=1,
)
def run_all_scrapers(self) -> list[ScrapResult]:
    """
    Run all 4 scrapers in parallel using Celery group.

    Called by Beat scheduler on Sunday 11pm AEST. Executes all chain scrapers
    in parallel and collects results.

    Returns:
        list: List of ScrapResult dicts from each scraper

    Raises:
        Exception: If group execution fails (will retry once)

    Implementation notes:
        - Uses Celery group for true parallelism
        - Each scraper runs on dedicated queue
        - Total timeout is 4 hours (enough for all scrapers)
        - Waits for all to complete before returning
        - Logs aggregate statistics
    """
    logger.info("Running all 4 scrapers in parallel", extra={"task_id": self.request.id})

    try:
        # Only ALDI remains in the flyer pipeline.
        # Other chains use run_catalog_crawl(mode="specials") via the catalog queue.
        job = group(
            run_flyer_scrape.s("aldi"),
        )

        # Execute group and wait for all results
        logger.debug("Submitting scraper jobs to group")
        result = job.apply_async()
        logger.info(f"Submitted scraper group job: {result.id}")

        # Wait for all to complete
        scraper_results = result.get(timeout=14100)

        logger.info(
            "All scrapers completed",
            extra={
                "total_products": sum(r.get("products_found", 0) for r in scraper_results),
                "total_new": sum(r.get("products_new", 0) for r in scraper_results),
            },
        )

        return scraper_results

    except Exception as e:
        logger.error(f"Scraper group execution failed: {e}", exc_info=True)
        # Retry once on failure
        raise self.retry(exc=e, countdown=600, max_retries=1)

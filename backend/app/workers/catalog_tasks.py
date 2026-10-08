"""
Catalog crawl worker tasks (Feature B).

``run_catalog_crawl(chain, mode)`` drives a full or delta catalog crawl for one
chain: it creates a ``CatalogCrawlRun`` audit row, runs the registered scraper
with an ingest sink that upserts each category's products as they arrive, then
finalizes the run record.

Runs on the dedicated ``catalog`` queue with a very long time limit — a full
crawl is intentionally paced over hours. ``run_all_catalog_crawls`` fans out a
delta crawl across every enabled chain (used by the optional Beat schedule).
"""

import asyncio
import json
import logging
from asyncio import CancelledError
from datetime import datetime, timezone
from typing import TypedDict
from uuid import uuid4

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from app.config import get_settings
from app.models import CatalogCrawlRun
from app.services.catalog_ingest import IngestCounts, ingest_catalog_products
from app.workers.celery_app import celery_app
from app.workers.tasks_base import CallbackTask

logger = logging.getLogger(__name__)
settings = get_settings()

# Commit to the DB after roughly this many products so a long crawl persists
# progress incrementally rather than in one giant transaction at the end.
_COMMIT_EVERY = 500

# Redis log buffer — keep the last 200 lines per task, expire after 2h.
_LOG_KEY_PREFIX = "catalog_log:"
_LOG_MAX_LINES = 200
_LOG_TTL_S = 7200


def _push_log(redis, task_id: str, level: str, msg: str) -> None:
    key = f"{_LOG_KEY_PREFIX}{task_id}"
    entry = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg})
    redis.rpush(key, entry)
    redis.ltrim(key, -_LOG_MAX_LINES, -1)
    redis.expire(key, _LOG_TTL_S)


class CatalogResult(TypedDict):
    chain: str
    mode: str
    status: str
    categories_found: int
    categories_crawled: int
    categories_skipped: int
    products_found: int
    products_new: int
    products_updated: int
    errors: list[str]


def get_db_session():
    """Get a synchronous database session for use in Celery tasks."""
    from app.deps import _SessionLocal

    if not _SessionLocal:
        raise RuntimeError("Database not initialized")
    return _SessionLocal()


def get_redis():
    """Get Redis client for use in Celery tasks."""
    from app.deps import _redis_pool

    if not _redis_pool:
        raise RuntimeError("Redis not initialized")
    return _redis_pool


@celery_app.task(
    bind=True,
    name="app.workers.catalog_tasks.run_catalog_crawl",
    queue="catalog",
    base=CallbackTask,
    time_limit=28800,  # 8h hard cap — a full crawl is paced over hours
    soft_time_limit=28500,
    max_retries=0,  # crawls are long + idempotent-ish; don't auto-retry
)
def run_catalog_crawl(self, chain: str, mode: str = "full") -> CatalogResult:
    """Run a full or delta catalog crawl for one chain.

    Args:
        chain: chain name registered in the catalog registry.
        mode: 'full' (crawl every category) or 'delta' (changed only).
    """
    from scrapers.catalog_registry import get_catalog_scraper

    if mode not in ("full", "delta", "specials"):
        raise ValueError(f"Invalid mode {mode!r}; must be 'full', 'delta', or 'specials'")

    logger.info("Starting catalog crawl chain=%s mode=%s", chain, mode)
    session = get_db_session()
    redis = get_redis()
    task_id = self.request.id
    _push_log(redis, task_id, "info", f"Starting {mode} crawl for {chain}")

    # Guard against re-delivery: if the worker is killed mid-task,
    # task_acks_late + task_reject_on_worker_lost will re-queue the message
    # and a new worker picks it up — same celery_task_id, new function invocation.
    # Reuse the existing row instead of creating a phantom duplicate.
    existing = session.query(CatalogCrawlRun).filter_by(celery_task_id=self.request.id).first()
    if existing:
        run = existing
        run.status = "running"
        session.commit()
    else:
        run = CatalogCrawlRun(
            id=uuid4(),
            chain=chain,
            mode=mode,
            status="running",
            celery_task_id=self.request.id,
            run_started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()

    totals = {"new": 0, "updated": 0, "since_commit": 0}

    try:
        scraper = get_catalog_scraper(chain, db_session=session, redis_client=redis)

        async def sink(products, category, *, mark_category_done: bool = True):
            # Check if a cancel has been requested via Redis flag.
            cancel_key = f"catalog_cancel:{self.request.id}"
            if redis.exists(cancel_key):
                raise CancelledError(f"Crawl cancelled by user request (chain={chain})")

            counts = IngestCounts()
            if products:
                # Sync DB work inside the async crawl. Fine at our scale; keeps the
                # crawl memory-flat by ingesting per page instead of buffering.
                counts = ingest_catalog_products(products, chain, session)
                totals["new"] += counts.created
                totals["updated"] += counts.updated
                totals["since_commit"] += counts.price_reports

                run.products_found = (run.products_found or 0) + len(products)
                run.products_new = totals["new"]
                run.products_updated = totals["updated"]

            if mark_category_done:
                run.categories_crawled = (run.categories_crawled or 0) + 1

            cat_name = getattr(category, "name", None) or str(category)
            if products:
                if mark_category_done:
                    _push_log(
                        redis,
                        task_id,
                        "info",
                        f"Category '{cat_name}': {len(products)} products "
                        f"(+{counts.created} new, {counts.updated} updated) — "
                        f"total {totals['new']} new",
                    )
                else:
                    _push_log(
                        redis,
                        task_id,
                        "info",
                        f"  → {cat_name}: +{counts.created} new, {counts.updated} updated "
                        f"({len(products)} flushed)",
                    )

            if totals["since_commit"] >= _COMMIT_EVERY:
                session.commit()
                totals["since_commit"] = 0
                logger.info(
                    "Catalog %s: committed progress (%d new, %d updated)",
                    chain,
                    totals["new"],
                    totals["updated"],
                )
                _push_log(
                    redis,
                    task_id,
                    "info",
                    f"Progress saved: {totals['new']} new, {totals['updated']} updated",
                )
            else:
                # Always persist the progress counters even if product threshold
                # hasn't been reached, so the UI reflects the current category.
                session.commit()

        def on_categories_discovered(count: int):
            run.categories_found = count
            session.commit()
            logger.info("Catalog %s: discovered %d categories", chain, count)
            _push_log(redis, task_id, "info", f"Discovered {count} categories to crawl")

        def on_category_start(category):
            cat_name = getattr(category, "name", None) or str(category)
            _push_log(redis, task_id, "info", f"Crawling category '{cat_name}'...")
            try:
                redis.setex(f"catalog_curcat:{task_id}", 3600, cat_name)
            except Exception:
                pass

        if mode == "specials":
            runner = scraper.run_specials
            resume_page: int = 1  # kept for scrapers that still use page-based resume (e.g. Coles)

            async def specials_sink(products, category, *, page: int = 0):
                cancel_key = f"catalog_cancel:{self.request.id}"
                if redis.exists(cancel_key):
                    raise CancelledError(f"Crawl cancelled by user request (chain={chain})")

                counts = ingest_catalog_products(products, chain, session)
                totals["new"] += counts.created
                totals["updated"] += counts.updated
                totals["since_commit"] += counts.price_reports

                run.products_found = (run.products_found or 0) + len(products)
                run.products_new = totals["new"]
                run.products_updated = totals["updated"]
                run.categories_crawled = (run.categories_crawled or 0) + 1

                cat_name = getattr(category, "name", None) or str(category)
                _push_log(
                    redis,
                    task_id,
                    "info",
                    f"Category '{cat_name}': {len(products)} specials "
                    f"(+{counts.created} new, {counts.updated} updated) — "
                    f"total {totals['new']} new",
                )

                if totals["since_commit"] >= _COMMIT_EVERY:
                    session.commit()
                    totals["since_commit"] = 0
                else:
                    session.commit()

            summary = asyncio.run(runner(product_sink=specials_sink, resume_page=resume_page))
        elif mode == "full":
            runner = scraper.run_full
            summary = asyncio.run(
                runner(
                    product_sink=sink,
                    on_categories_discovered=on_categories_discovered,
                    on_category_start=on_category_start,
                )
            )
        else:
            runner = scraper.run_delta
            summary = asyncio.run(
                runner(
                    product_sink=sink,
                    on_categories_discovered=on_categories_discovered,
                    on_category_start=on_category_start,
                )
            )
        session.commit()  # flush any remaining un-committed products

        run.status = summary["status"] if not summary["errors"] else "completed_with_errors"
        run.categories_found = summary["categories_found"]
        run.categories_crawled = summary["categories_crawled"]
        run.categories_skipped = summary["categories_skipped"]
        run.products_found = summary["products_found"]
        run.products_new = totals["new"]
        run.products_updated = totals["updated"]
        run.run_finished_at = datetime.now(timezone.utc)
        if summary["errors"]:
            run.errors_json = {"errors": summary["errors"][:50]}
        session.commit()

        # Mark successful specials run so the trigger endpoint can warn the admin
        # if they try to re-run within 24 hours.
        if mode == "specials" and run.status in ("completed", "completed_with_errors"):
            redis.setex(
                f"specials_last_success:{chain}",
                90000,  # 25 hours
                run.run_finished_at.isoformat(),
            )

        logger.info(
            "Catalog crawl done chain=%s mode=%s: %d found, %d new, %d updated",
            chain,
            mode,
            summary["products_found"],
            totals["new"],
            totals["updated"],
        )
        _push_log(
            redis,
            task_id,
            "info",
            f"Done: {summary['products_found']} found, {totals['new']} new, "
            f"{totals['updated']} updated. Status: {run.status}",
        )
        if summary["errors"]:
            for err in summary["errors"][:5]:
                _push_log(redis, task_id, "error", f"Error: {err}")

        # Enqueue image enrichment for any newly-ingested products missing a
        # self-hosted image.  Fire-and-forget: never block or fail the crawl.
        if run.status in ("completed", "completed_with_errors") and totals["new"] > 0:
            try:
                from app.workers.image_tasks import enrich_product_images

                enrich_product_images.apply_async(kwargs={"chain": chain})
                logger.info("Enqueued image enrichment for chain=%s", chain)
            except Exception as exc:
                logger.warning("Failed to enqueue image enrichment for %s: %s", chain, exc)

        return CatalogResult(
            chain=chain,
            mode=mode,
            status=run.status,
            categories_found=summary["categories_found"],
            categories_crawled=summary["categories_crawled"],
            categories_skipped=summary["categories_skipped"],
            products_found=summary["products_found"],
            products_new=totals["new"],
            products_updated=totals["updated"],
            errors=summary["errors"],
        )
    except CancelledError as exc:
        _push_log(redis, task_id, "warn", f"Crawl cancelled: {exc}")
        session.rollback()
        try:
            run.status = "cancelled"
            run.run_finished_at = datetime.now(timezone.utc)
            session.commit()
        except Exception as commit_err:
            logger.error("Failed to record crawl cancellation: %s", commit_err)
        # Clean up the cancel flag
        try:
            redis.delete(f"catalog_cancel:{self.request.id}")
        except Exception:
            pass
        return CatalogResult(
            chain=chain,
            mode=mode,
            status="cancelled",
            categories_found=run.categories_found or 0,
            categories_crawled=run.categories_crawled or 0,
            categories_skipped=run.categories_skipped or 0,
            products_found=run.products_found or 0,
            products_new=totals["new"],
            products_updated=totals["updated"],
            errors=[str(exc)],
        )
    except SoftTimeLimitExceeded:
        _push_log(redis, task_id, "error", "Crawl timed out (soft time limit exceeded)")
        logger.warning("Catalog crawl timed out chain=%s after %ds", chain, self.soft_time_limit)
        session.rollback()
        try:
            run.status = "failed"
            run.errors_json = {"error": "Soft time limit exceeded"}
            run.run_finished_at = datetime.now(timezone.utc)
            session.commit()
        except Exception as commit_err:
            logger.error("Failed to record crawl timeout: %s", commit_err)
        raise
    except Exception as exc:
        _push_log(redis, task_id, "error", f"Crawl failed: {exc}")
        logger.exception("Catalog crawl failed chain=%s: %s", chain, exc)
        session.rollback()
        try:
            run.status = "failed"
            run.errors_json = {"error": str(exc)}
            run.run_finished_at = datetime.now(timezone.utc)
            session.commit()
        except Exception as commit_err:
            logger.error("Failed to record crawl failure: %s", commit_err)
        raise
    finally:
        session.close()


@celery_app.task(
    bind=True,
    name="app.workers.catalog_tasks.run_all_catalog_crawls",
    queue="catalog",
    base=CallbackTask,
    time_limit=28800,
    max_retries=0,
)
def run_all_catalog_crawls(self, mode: str = "delta") -> list[str]:
    """Queue a crawl for every enabled chain. Used by the delta Beat schedule.

    Returns the list of queued task ids. Chains are dispatched as separate
    tasks (not a blocking group) so one slow store doesn't hold the others.
    """
    from scrapers.catalog_registry import available_chains

    queued: list[str] = []
    for chain in available_chains():
        if not getattr(settings, f"CATALOG_{chain.upper()}_ENABLED", False):
            continue
        task = run_catalog_crawl.apply_async(args=[chain, mode], queue="catalog")
        queued.append(task.id)
        logger.info("Queued %s catalog %s crawl: %s", chain, mode, task.id)
    return queued

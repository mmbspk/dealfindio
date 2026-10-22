"""
Celery application factory with Redis broker and result backend.

Configures:
- Redis connection pooling for broker and result backend
- Task routing to dedicated queues (scraper, ml, notification, default)
- Task serialization (JSON), retry logic, and time limits
- Error handling and monitoring integration
- Beat schedule for periodic tasks
"""

import logging

from celery import Celery
from celery.signals import task_failure, task_retry, task_success, worker_process_init
from kombu import Exchange, Queue

from app.config import get_settings

logger = logging.getLogger(__name__)

# Get settings from environment
settings = get_settings()

# Create Celery app instance
celery_app = Celery(
    settings.PROJECT_NAME,
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# ============================================================================
# BROKER & RESULT BACKEND CONFIGURATION
# ============================================================================

celery_app.conf.update(
    # Broker settings
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=10,
    # Connection pooling for Redis broker
    broker_pool_limit=10,
    broker_heartbeat=30,
    broker_transport_options={
        "retry_on_timeout": True,
    },
    # Result backend settings
    result_backend_transport_options={
        "retry_on_timeout": True,
    },
    result_expires=3600,  # Task results expire after 1 hour
)

# ============================================================================
# TASK SERIALIZATION & CONTENT TYPE
# ============================================================================

celery_app.conf.update(
    task_serializer=settings.CELERY_TASK_SERIALIZER,  # JSON
    result_serializer=settings.CELERY_RESULT_SERIALIZER,  # JSON
    accept_content=settings.CELERY_ACCEPT_CONTENT,  # ["json"]
    timezone=settings.CELERY_TIMEZONE,  # UTC
    enable_utc=settings.CELERY_ENABLE_UTC,
)

# ============================================================================
# TASK TRACKING & TIME LIMITS
# ============================================================================

celery_app.conf.update(
    task_track_started=settings.CELERY_TASK_TRACK_STARTED,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,  # Hard limit: 30 min
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,  # Soft limit: 25 min
)

# ============================================================================
# DEFAULT TASK RETRY LOGIC
# ============================================================================

celery_app.conf.update(
    task_acks_late=True,  # Acknowledge task after execution
    task_reject_on_worker_lost=True,  # Reject if worker dies unexpectedly
)

# ============================================================================
# TASK ROUTING TO QUEUES
# ============================================================================

# Define exchanges and queues
default_exchange = Exchange("celery", type="direct", durable=True)
scraper_exchange = Exchange("scraper", type="direct", durable=True)
ml_exchange = Exchange("ml", type="direct", durable=True)
notification_exchange = Exchange("notification", type="direct", durable=True)
image_exchange = Exchange("image", type="direct", durable=True)
catalog_exchange = Exchange("catalog", type="direct", durable=True)

celery_app.conf.task_queues = (
    # Default queue: for fast, immediate tasks
    Queue(
        "default",
        exchange=default_exchange,
        routing_key="default",
        durable=True,
    ),
    # Scraper queue: for slow, sequential flyer scraping tasks
    # One worker at a time, long timeout
    Queue(
        "scraper",
        exchange=scraper_exchange,
        routing_key="scraper",
        durable=True,
    ),
    # ML queue: for CPU-intensive model training and prediction
    # Limited concurrency (2-4 workers)
    Queue(
        "ml",
        exchange=ml_exchange,
        routing_key="ml",
        durable=True,
    ),
    # Notification queue: for high-priority push notifications
    # Many workers, fast execution
    Queue(
        "notification",
        exchange=notification_exchange,
        routing_key="notification",
        durable=True,
    ),
    # Image queue: for slow, I/O-bound OFF image enrichment.
    # Deliberately isolated so its polite per-request delays never block
    # the scraper or notification queues.
    Queue(
        "image",
        exchange=image_exchange,
        routing_key="image",
        durable=True,
    ),
    # Catalog queue: for very long authenticated full/delta catalog crawls,
    # paced over hours. Isolated so a multi-hour crawl never blocks anything.
    Queue(
        "catalog",
        exchange=catalog_exchange,
        routing_key="catalog",
        durable=True,
    ),
)

# Route tasks to appropriate queues based on task name
celery_app.conf.task_routes = {
    "app.workers.scraper_tasks.expire_stale_specials": {"queue": "scraper"},
    "app.workers.ml_tasks.refit_and_predict_all": {"queue": "ml"},
    "app.workers.notification_tasks.check_watchlist_for_price": {"queue": "notification"},
    "app.workers.notification_tasks.notify_prediction_drops": {"queue": "notification"},
    "app.workers.notification_tasks.send_weekly_digest": {"queue": "notification"},
    "app.workers.report_validator.validate_community_report": {"queue": "default"},
    "app.workers.image_tasks.enrich_product_images": {"queue": "image"},
    "app.workers.catalog_tasks.run_catalog_crawl": {"queue": "catalog"},
    "app.workers.catalog_tasks.run_all_catalog_crawls": {"queue": "catalog"},
    "app.workers.embedding_tasks.embed_product": {"queue": "ml"},
    "app.workers.embedding_tasks.backfill_product_embeddings": {"queue": "ml"},
}

# ============================================================================
# WORKER SETTINGS
# ============================================================================

celery_app.conf.update(
    worker_prefetch_multiplier=1,  # One task per worker slot — critical for long-running catalog crawls
    worker_max_tasks_per_child=50,  # Restart worker after 50 tasks (catalog crawls are memory-heavy)
    # Skip cron tasks that are more than 10 minutes overdue on beat restart.
    # Without this, restarting beat after days offline fires every missed
    # weekly crawl immediately (catch-up behaviour).
    beat_cron_starting_deadline=600,
)

# ============================================================================
# ERROR HANDLING & MONITORING
# ============================================================================


@worker_process_init.connect
def init_worker(**kwargs):
    """Initialise DB and Redis connections in each worker process."""
    # Install uvloop as the default event loop policy so asyncio.run() inside
    # Celery tasks uses libuv instead of the macOS SelectorEventLoop, which
    # spins at 100% CPU during long async crawls on Python 3.12/macOS.
    try:
        import uvloop

        uvloop.install()
        logger.info("uvloop installed as event loop policy")
    except ImportError:
        logger.warning("uvloop not available; using default SelectorEventLoop")

    from app.deps import init_database, init_redis

    init_database(
        settings.DATABASE_URL,
        settings.DATABASE_POOL_SIZE,
        settings.DATABASE_ECHO,
    )
    init_redis(
        settings.REDIS_URL,
        settings.REDIS_SOCKET_CONNECT_TIMEOUT,
    )
    logger.info("Worker process initialised DB and Redis")


@task_success.connect
def task_success_handler(sender=None, result=None, **kwargs):
    """Log successful task execution."""
    logger.info(f"Task {sender.name} completed successfully", extra={"result": str(result)[:100]})


@task_retry.connect
def task_retry_handler(sender=None, reason=None, einfo=None, **kwargs):
    """Log task retry."""
    logger.warning(
        f"Task {sender.name} retrying",
        extra={"reason": str(reason), "exception": str(einfo)[:100]},
    )


@task_failure.connect
def task_failure_handler(
    sender=None, task_id=None, exception=None, args=None, kwargs=None, traceback=None, **kw
):
    """
    Log task failure and send alert if webhook configured.
    """
    error_msg = f"Task {sender.name} (ID: {task_id}) failed: {exception}"
    logger.error(
        error_msg,
        extra={
            "task_name": sender.name,
            "task_id": task_id,
            "task_args": str(args)[:100],
            "task_kwargs": str(kwargs)[:100],
        },
    )

    # Send to Slack if webhook configured
    if settings.SLACK_WEBHOOK_URL:
        try:
            import requests

            requests.post(
                settings.SLACK_WEBHOOK_URL,
                json={
                    "text": f"*Celery Task Failure*\nTask: {sender.name}\nError: {str(exception)[:200]}",
                    "color": "danger",
                },
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")


# ============================================================================
# IMPORT TASK MODULES AT APP LOAD
# ============================================================================


def load_tasks():
    """Import task modules to register them with Celery."""
    try:
        # Import all task modules to register them
        from app.workers import (  # noqa: F401
            catalog_tasks,
            embedding_tasks,
            image_tasks,
            ml_tasks,
            notification_tasks,
            report_validator,
            scraper_tasks,
        )

        logger.info("Successfully loaded all task modules")
    except ImportError as e:
        logger.error(f"Failed to import task modules: {e}")


# Auto-load tasks
load_tasks()

# ============================================================================
# BEAT SCHEDULE (for periodic tasks)
# ============================================================================

from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    # ── Specials expiry ───────────────────────────────────────────────────────
    #
    # ALDI: Wed–Tue cycle. Expire Tuesday 13:30 UTC (= 23:30 AEST).
    # Coles/Woolworths/IGA/Drakes specials are set with valid_to at ingest time
    # (catalog pipeline) so no separate expiry task is needed for them.
    "expire-stale-specials-aldi-wed": {
        "task": "app.workers.scraper_tasks.expire_stale_specials",
        "schedule": crontab(day_of_week=2, hour=13, minute=30),
        "args": (["catalog_aldi"],),
    },
    # ALDI Saturday Specialbuys expire Friday 20:30 UTC (= 06:30 AEST Sat).
    "expire-stale-specials-aldi-sat": {
        "task": "app.workers.scraper_tasks.expire_stale_specials",
        "schedule": crontab(day_of_week=5, hour=20, minute=30),
        "args": (["catalog_aldi"],),
    },
    # ── Weekly specials scrapes ───────────────────────────────────────────────
    #
    # All chains now use catalog_tasks.run_catalog_crawl with mode="specials".
    # ALDI uses ALDICatalogScraper.crawl_specials() which hits the three
    # promotional categories (Lower Prices, Super Savers, Limited Time Only).
    #
    # All times UTC. AEST = UTC+10. New specials go live Wed 00:01 AEST = Tue 14:01 UTC.
    # Stagger by 10 min to avoid hammering servers simultaneously.
    #
    # Coles: Wed 00:01 AEST live → scrape Wed 20:03 UTC
    "specials-catalog-coles": {
        "task": "app.workers.catalog_tasks.run_catalog_crawl",
        "schedule": crontab(day_of_week=2, hour=20, minute=3),
        "args": ("coles", "specials"),
    },
    # Woolworths: Wed 00:01 AEST live → scrape Wed 20:13 UTC
    "specials-catalog-woolworths": {
        "task": "app.workers.catalog_tasks.run_catalog_crawl",
        "schedule": crontab(day_of_week=2, hour=20, minute=13),
        "args": ("woolworths", "specials"),
    },
    # IGA: Wed 00:01 AEST live → scrape Wed 20:23 UTC
    "specials-catalog-iga": {
        "task": "app.workers.catalog_tasks.run_catalog_crawl",
        "schedule": crontab(day_of_week=2, hour=20, minute=23),
        "args": ("iga", "specials"),
    },
    # Drakes: Wed 00:01 AEST live → scrape Wed 20:33 UTC
    "specials-catalog-drakes": {
        "task": "app.workers.catalog_tasks.run_catalog_crawl",
        "schedule": crontab(day_of_week=2, hour=20, minute=33),
        "args": ("drakes", "specials"),
    },
    # ALDI Wednesday grocery specials: live Wed 00:01 AEST → scrape Wed 20:43 UTC
    "specials-catalog-aldi-wed": {
        "task": "app.workers.catalog_tasks.run_catalog_crawl",
        "schedule": crontab(day_of_week=2, hour=20, minute=43),
        "args": ("aldi", "specials"),
    },
    # ALDI Saturday Specialbuys: live Sat 00:01 AEST → scrape Sat 20:53 UTC (Fri night UTC)
    "specials-catalog-aldi-sat": {
        "task": "app.workers.catalog_tasks.run_catalog_crawl",
        "schedule": crontab(day_of_week=6, hour=20, minute=53),
        "args": ("aldi", "specials"),
    },
    # ── ML & embeddings ───────────────────────────────────────────────────────
    #
    # Refit after Wednesday scrapes have landed and been ingested.
    # Wednesday 23:00 UTC (= Thursday 09:00 AEST) — safe window.
    "ml-refit-predictions": {
        "task": "app.workers.ml_tasks.refit_and_predict_all",
        "schedule": crontab(day_of_week=3, hour=23, minute=0),
        "args": (),
    },
    # Embedding backfill: Thursday 23:30 UTC — after ML refit.
    "backfill-embeddings": {
        "task": "app.workers.embedding_tasks.backfill_product_embeddings",
        "schedule": crontab(day_of_week=3, hour=23, minute=30),
        "args": (),
    },
    # ── Image enrichment ─────────────────────────────────────────────────────
    #
    # Backstop sweep Thursday 01:07 UTC — catches anything that slipped
    # through the per-scrape enrichment chain earlier that morning.
    "enrich-images-backstop": {
        "task": "app.workers.image_tasks.enrich_product_images",
        "schedule": crontab(day_of_week=3, hour=1, minute=7),
        "args": (),
    },
    # ── Weekly digest email ───────────────────────────────────────────────────
    #
    # Wednesday 22:00 UTC (= Thursday 08:00 AEST) — after new specials are
    # scraped and ingested, so the digest reflects this week's actual deals.
    "send-weekly-digest": {
        "task": "app.workers.notification_tasks.send_weekly_digest",
        "schedule": crontab(day_of_week=2, hour=22, minute=0),
        "args": (),
    },
}


def _build_catalog_beat_entries(settings) -> dict:
    """Build DELTA catalog-crawl Beat entries from CATALOG_{CHAIN}_CRON env vars.

    Full crawls are NEVER scheduled (on-demand only via the admin panel). A
    chain is scheduled only when it is enabled AND has a non-empty 5-field cron
    string. Adding/scheduling a new chain needs no source change here — just set
    its env vars and restart Beat.

    A malformed cron string is logged and skipped rather than crashing Beat.
    """
    entries: dict = {}
    # Discover chains from the registry so new stores are picked up automatically.
    try:
        from scrapers.catalog_registry import available_chains

        chains = available_chains()
    except Exception:
        chains = ["coles", "woolworths", "aldi", "iga", "drakes"]

    for chain in chains:
        upper = chain.upper()
        if not getattr(settings, f"CATALOG_{upper}_ENABLED", False):
            continue
        cron_str = (getattr(settings, f"CATALOG_{upper}_CRON", "") or "").strip()
        if not cron_str:
            continue
        parts = cron_str.split()
        if len(parts) != 5:
            logger.warning("Ignoring invalid CATALOG_%s_CRON %r (need 5 fields)", upper, cron_str)
            continue
        minute, hour, dom, month, dow = parts
        entries[f"catalog-delta-{chain}"] = {
            "task": "app.workers.catalog_tasks.run_catalog_crawl",
            "schedule": crontab(
                minute=minute,
                hour=hour,
                day_of_month=dom,
                month_of_year=month,
                day_of_week=dow,
            ),
            "args": (chain, "delta"),
        }
        logger.info("Scheduled delta catalog crawl for %s: %s", chain, cron_str)
    return entries


celery_app.conf.beat_schedule.update(_build_catalog_beat_entries(settings))

# ============================================================================
# CELERY CONFIGURATION SUMMARY
# ============================================================================

logger.info(
    "Celery app configured",
    extra={
        "broker": settings.CELERY_BROKER_URL,
        "backend": settings.CELERY_RESULT_BACKEND,
        "serializer": settings.CELERY_TASK_SERIALIZER,
        "timezone": settings.CELERY_TIMEZONE,
    },
)

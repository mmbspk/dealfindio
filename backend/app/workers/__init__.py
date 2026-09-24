"""
Celery task workers and Beat scheduler configuration.

Exports:
- celery_app: Celery application instance
- beat_schedule: Periodic task schedules for Celery Beat
"""

from celery.schedules import crontab

from app.workers.celery_app import celery_app

# Re-export celery_app for convenience
__all__ = ["celery_app", "beat_schedule"]

# ============================================================================
# BEAT SCHEDULE
# ============================================================================

beat_schedule = {
    # ========================================================================
    # FLYER SCRAPING TASKS
    # Scrape grocery store flyers every Sunday at 11pm AEST
    # UTC times: Sunday 15:00 (summer) or 16:00 (winter, DST)
    # Using 23:00 UTC to cover both seasons
    # ========================================================================
    "scrape-flyer-coles": {
        "task": "app.workers.scraper_tasks.run_flyer_scrape",
        "schedule": crontab(day_of_week=6, hour=23, minute=0),
        "args": ("coles",),
        "options": {
            "expires": 3600,  # Task expires after 1 hour if not executed
        },
    },
    "scrape-flyer-woolworths": {
        "task": "app.workers.scraper_tasks.run_flyer_scrape",
        "schedule": crontab(day_of_week=6, hour=23, minute=10),
        "args": ("woolworths",),
        "options": {
            "expires": 3600,
        },
    },
    "scrape-flyer-aldi": {
        "task": "app.workers.scraper_tasks.run_flyer_scrape",
        "schedule": crontab(day_of_week=6, hour=23, minute=20),
        "args": ("aldi",),
        "options": {
            "expires": 3600,
        },
    },
    "scrape-flyer-iga": {
        "task": "app.workers.scraper_tasks.run_flyer_scrape",
        "schedule": crontab(day_of_week=6, hour=23, minute=30),
        "args": ("iga",),
        "options": {
            "expires": 3600,
        },
    },
    # ========================================================================
    # MACHINE LEARNING TASKS
    # Refit models and generate predictions every Monday at 2am AEST
    # UTC time: Sunday 16:00 (summer) or 17:00 (winter, DST)
    # Using 02:00 UTC with day_of_week=0 (Monday) to cover both seasons
    # ========================================================================
    "ml-refit-predictions": {
        "task": "app.workers.ml_tasks.refit_and_predict_all",
        "schedule": crontab(day_of_week=0, hour=2, minute=0),
        "args": (),
        "options": {
            "expires": 10800,  # Expires after 3 hours if not executed
        },
    },
    # ========================================================================
    # REPORTER REPUTATION RECALCULATION
    # Run weekly to update community reporter reputation scores
    # Tuesday at 1am AEST (Monday 15:00 UTC or 16:00 UTC)
    # ========================================================================
    # Note: Individual user reputation recalculation is triggered by
    # report_validator.recalculate_reporter_reputation() when needed
    # This could be extended with a bulk recalculation task if desired
}

# ============================================================================
# CELERY CONFIGURATION (loaded from settings)
# ============================================================================

# The Celery app applies this beat_schedule automatically via:
# celery_app.conf.beat_schedule = beat_schedule
# (This is done in celery_app.py during initialization)

# ============================================================================
# RUNNING CELERY WORKERS AND BEAT
# ============================================================================

# See RUNNING_WORKERS.md for complete command documentation

# Quick reference:

# Single worker (all queues):
#   celery -A app.workers.celery_app worker --loglevel=info

# Specialized workers by queue:
#   celery -A app.workers.celery_app worker --queues=scraper --concurrency=1 --loglevel=info
#   celery -A app.workers.celery_app worker --queues=ml --concurrency=2 --loglevel=info
#   celery -A app.workers.celery_app worker --queues=notification --concurrency=10 --loglevel=info

# Beat scheduler (for periodic tasks):
#   celery -A app.workers.celery_app beat --loglevel=info

# ============================================================================
# TASK ROUTING REFERENCE
# ============================================================================

# Tasks are automatically routed to the correct queue based on their name.
# Queue routing is configured in celery_app.py

# Current routing:
# - app.workers.scraper_tasks.* → scraper queue
# - app.workers.ml_tasks.* → ml queue
# - app.workers.notification_tasks.* → notification queue
# - app.workers.report_validator.* → default queue
# - Everything else → default queue

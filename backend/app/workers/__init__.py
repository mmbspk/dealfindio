"""
Celery task workers and Beat scheduler configuration.

Exports:
- celery_app: Celery application instance
"""

from app.workers.celery_app import celery_app

__all__ = ["celery_app"]

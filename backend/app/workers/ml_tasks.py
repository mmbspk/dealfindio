"""
Machine learning worker tasks.

Handles model training, refitting, and price prediction generation for
the dealfindio Prophet-based forecasting system.

These tasks run on the 'ml' queue with limited concurrency (2-4 workers)
for CPU-intensive work.
"""

import logging
import time
from typing import TypedDict

from app.workers.celery_app import celery_app
from app.workers.tasks_base import CallbackTask

logger = logging.getLogger(__name__)


class MLResult(TypedDict):
    """Result dictionary for ML tasks."""
    products_refitted: int
    predictions_generated: int
    status: str
    duration_seconds: float


@celery_app.task(
    bind=True,
    name='app.workers.ml_tasks.refit_and_predict_all',
    queue='ml',
    base=CallbackTask,
    time_limit=7200,
    soft_time_limit=6900,
    max_retries=1,
    default_retry_delay=600,
)
def refit_and_predict_all(self) -> MLResult:
    """
    Weekly task to refit Prophet models and generate price predictions.

    Runs every Monday at 2am AEST to process the previous week's price data,
    refit time series models for all products, and generate predictions for
    the upcoming 1-2 weeks.

    Scheduled: Every Monday at 2:00 AM AEST (UTC Sunday 16:00) via Celery Beat.
    """
    start_time = time.time()
    logger.info("Starting weekly ML refit and prediction generation", extra={"task_id": self.request.id})

    session = None
    products_refitted = 0
    predictions_generated = 0
    errors = 0

    try:
        from app.deps import _SessionLocal
        from app.models import Product
        from ml.predictor import run_predictions_for_product

        session = _SessionLocal()
        products = session.query(Product).all()
        total = len(products)
        logger.info(f"Processing {total} products")

        for i, product in enumerate(products, 1):
            try:
                results = run_predictions_for_product(session, product.id)
                ok = [r for r in results if r.get("status") == "ok"]
                if ok:
                    products_refitted += 1
                    predictions_generated += sum(r.get("predictions_written", 0) for r in ok)
                if i % 100 == 0:
                    logger.info(f"Progress: {i}/{total} products processed")
            except Exception:
                logger.exception(f"Failed to process product {product.id}")
                errors += 1

        duration = time.time() - start_time
        status = "success" if errors == 0 else ("partial" if products_refitted > 0 else "error")

        logger.info(
            f"ML refit complete: {products_refitted} products, "
            f"{predictions_generated} predictions in {duration:.1f}s ({errors} errors)"
        )

        # Trigger prediction-drop notifications after a successful run
        if predictions_generated > 0:
            from app.workers.notification_tasks import notify_prediction_drops
            notify_prediction_drops.delay()

        return {
            "products_refitted": products_refitted,
            "predictions_generated": predictions_generated,
            "status": status,
            "duration_seconds": round(duration, 2),
        }

    except Exception as exc:
        duration = time.time() - start_time
        logger.exception(f"ML refit task failed after {duration:.1f}s: {exc}")
        raise self.retry(exc=exc, countdown=600, max_retries=1)

    finally:
        if session:
            session.close()

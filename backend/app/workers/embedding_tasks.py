"""
Embedding worker tasks for semantic product search (Phase B).

Two tasks:
  - embed_product: encode a single product after ingest (triggered by product_dedup)
  - backfill_product_embeddings: periodic task that processes all products missing embeddings
"""

import logging
import time

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.workers.embedding_tasks.embed_product",
    queue="ml",
    max_retries=3,
    default_retry_delay=60,
    ignore_result=False,
)
def embed_product(self, product_id: str) -> dict:
    """Encode a single product's name and persist the embedding."""
    from uuid import UUID
    from sqlalchemy import select
    from app.deps import _SessionLocal
    from app.models import Product
    from app.services.embeddings import embed_text

    start = time.time()
    try:
        with _SessionLocal() as session:
            product = session.execute(
                select(Product).where(Product.id == UUID(product_id))
            ).scalar_one_or_none()

            if product is None:
                return {"status": "not_found", "product_id": product_id}

            product.name_embedding = embed_text(product.name)
            session.commit()

        return {
            "status": "ok",
            "product_id": product_id,
            "duration_seconds": round(time.time() - start, 3),
        }
    except Exception as exc:
        logger.exception("embed_product failed for %s", product_id)
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    name="app.workers.embedding_tasks.backfill_product_embeddings",
    queue="ml",
    max_retries=1,
    ignore_result=False,
)
def backfill_product_embeddings(self, batch_size: int = 512) -> dict:
    """
    Encode all products that are missing a name_embedding.

    Safe to run multiple times — skips products that already have an embedding.
    Scheduled weekly via Celery Beat (or run manually after a large catalog import).
    """
    from sqlalchemy import select
    from app.deps import _SessionLocal
    from app.models import Product
    from app.services.embeddings import embed_batch

    start = time.time()
    total = 0

    try:
        with _SessionLocal() as session:
            while True:
                products = session.execute(
                    select(Product)
                    .where(Product.name_embedding == None)  # noqa: E711
                    .limit(batch_size)
                ).scalars().all()

                if not products:
                    break

                names = [p.name for p in products]
                embeddings = embed_batch(names)

                for product, vec in zip(products, embeddings):
                    product.name_embedding = vec

                session.commit()
                total += len(products)
                logger.info("backfill_product_embeddings: %d products embedded so far", total)

        return {
            "status": "ok",
            "products_embedded": total,
            "duration_seconds": round(time.time() - start, 2),
        }
    except Exception as exc:
        logger.exception("backfill_product_embeddings failed")
        raise self.retry(exc=exc)

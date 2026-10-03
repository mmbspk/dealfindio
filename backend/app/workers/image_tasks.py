"""
Image enrichment worker tasks (Feature A).

After a scrape lands new products, ``enrich_product_images`` finds products
that still lack an image, searches Open Food Facts by name, downloads the best
match, re-hosts it on our object store (R2/S3/local), and writes the resulting
CDN URL back to ``products.image_url``.

Runs on the ``image`` queue. It is I/O-bound and deliberately slow (a polite
2–5s gap per OFF request), so it gets a generous time limit and never blocks
the scraper or notification queues.
"""

import asyncio
import logging
from typing import Optional, TypedDict
from uuid import UUID

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PriceReport, Product
from app.services.object_store import get_object_store, product_image_key
from app.services.oft_image_enricher import OFTImageEnricher
from app.workers.celery_app import celery_app
from app.workers.tasks_base import CallbackTask

logger = logging.getLogger(__name__)
settings = get_settings()

VALID_CHAINS = {"coles", "woolworths", "aldi", "iga", "drakes"}


class EnrichResult(TypedDict):
    """Result dictionary for the image enrichment task."""

    chain: Optional[str]
    candidates: int
    enriched: int
    skipped: int
    status: str


def get_db_session() -> Session:
    """Get a synchronous database session for use in Celery tasks."""
    from app.deps import _SessionLocal

    if not _SessionLocal:
        raise RuntimeError("Database not initialized")
    return _SessionLocal()


def _find_products_missing_images(
    session: Session, chain: Optional[str], limit: int
) -> list[Product]:
    """Return up to ``limit`` products that need OFF enrichment.

    Excludes products that already have a live image_url OR a pending/approved
    OFF candidate (off_image_status IN ('pending', 'approved')) — don't
    overwrite an existing candidate that hasn't been reviewed yet.
    """
    stmt = select(Product).where(
        Product.image_url.is_(None),
        or_(
            Product.off_image_status.is_(None),
            Product.off_image_status.notin_(["pending", "approved"]),
        ),
    )
    if chain:
        stmt = (
            stmt.join(PriceReport, PriceReport.product_id == Product.id)
            .where(PriceReport.source == f"catalog_{chain}")
            .distinct()
        )
    stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


@celery_app.task(
    bind=True,
    name="app.workers.image_tasks.enrich_product_images",
    queue="image",
    base=CallbackTask,
    time_limit=3600,
    soft_time_limit=3300,
    max_retries=1,
    default_retry_delay=600,
)
def enrich_product_images(
    self, chain: Optional[str] = None, limit: Optional[int] = None
) -> EnrichResult:
    """Enrich product images from Open Food Facts.

    Args:
        chain: Restrict to one chain ('coles'|'woolworths'|'aldi'|'iga'), or
            ``None`` to sweep every chain (weekly backstop).
        limit: Max products to process this run (defaults to
            ``settings.OFT_ENRICH_LIMIT_PER_RUN``).

    Returns:
        EnrichResult with candidate/enriched/skipped counts and a status.
    """
    if chain is not None and chain not in VALID_CHAINS:
        raise ValueError(f"Invalid chain: {chain}. Must be one of {VALID_CHAINS}")

    run_limit = limit or settings.OFT_ENRICH_LIMIT_PER_RUN
    logger.info("Starting image enrichment (chain=%s, limit=%d)", chain or "all", run_limit)

    session = get_db_session()
    enriched = 0
    skipped = 0
    try:
        products = _find_products_missing_images(session, chain, run_limit)
        candidates = len(products)
        if not candidates:
            return EnrichResult(chain=chain, candidates=0, enriched=0, skipped=0, status="success")

        # Snapshot (id, name, gtin) tuples so the async loop doesn't hold
        # ORM instances across await points / session state changes.
        targets = [(p.id, p.name, p.gtin) for p in products]

        results = asyncio.run(_enrich_all(targets))

        store = get_object_store()
        for product_id, enriched_image in results:
            if enriched_image is None:
                skipped += 1
                continue
            try:
                key = product_image_key(product_id, enriched_image.content_type)
                public_url = store.put_object(
                    key, enriched_image.image_bytes, enriched_image.content_type
                )
            except Exception as exc:
                logger.warning("Failed to store image for %s: %s", product_id, exc)
                skipped += 1
                continue

            product = session.get(Product, product_id)
            if product is None:
                skipped += 1
                continue
            # Write to the review queue, not directly to image_url.
            # Admins (or the AI agent) approve candidates → only then does
            # image_url get populated.
            product.off_image_url = public_url
            product.off_image_score = enriched_image.match_score
            product.off_image_off_code = enriched_image.off_code
            product.off_image_off_name = enriched_image.off_product_name
            product.off_image_status = "pending"
            logger.info(
                "  ⏳ %s (score=%.0f) → pending review",
                product.name,
                enriched_image.match_score,
            )
            enriched += 1

        session.commit()
        logger.info(
            "Image enrichment done: %d enriched, %d skipped of %d candidates",
            enriched,
            skipped,
            candidates,
        )
        return EnrichResult(
            chain=chain,
            candidates=candidates,
            enriched=enriched,
            skipped=skipped,
            status="success",
        )
    except Exception as exc:
        logger.exception("Image enrichment failed: %s", exc)
        session.rollback()
        raise
    finally:
        session.close()


async def _enrich_all(
    targets: list[tuple[UUID, str, Optional[str]]],
) -> list[tuple[UUID, object]]:
    """Look up + download images for each (product_id, name, gtin). Best-effort.

    Returns a list of (product_id, EnrichedImage|None), one per input, in order.
    A single shared httpx client is reused so connections are pooled.
    """
    enricher = OFTImageEnricher(settings)
    out: list[tuple[UUID, object]] = []
    async with httpx.AsyncClient() as client:
        for product_id, name, gtin in targets:
            try:
                result = await enricher.enrich(name, client, gtin=gtin)
            except Exception as exc:  # never let one product kill the batch
                logger.warning("Enrich error for %s (%s): %s", product_id, name, exc)
                result = None
            out.append((product_id, result))
    return out

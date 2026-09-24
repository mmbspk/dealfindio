"""
Admin router — scraper management and ops visibility.

All endpoints require the authenticated user to have is_admin=True.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Iterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.deps import get_db, get_redis, require_admin, require_admin_sse
from app.models import FlyScrapeRun, PriceReport, Product

router = APIRouter()

VALID_CHAINS = {"coles", "woolworths", "aldi", "iga", "drakes"}


# ── Schemas ────────────────────────────────────────────────────────────────────


class ScrapeRunResponse(BaseModel):
    id: str
    chain: str
    status: str
    products_found: int
    products_new: int
    run_started_at: datetime
    run_finished_at: datetime | None
    errors_json: dict | None

    class Config:
        from_attributes = True


class TriggerResponse(BaseModel):
    task_id: str
    chain: str
    message: str


class StatsResponse(BaseModel):
    total_products: int
    total_price_reports: int
    products_by_chain: dict[str, int]
    last_scrape_by_chain: dict[str, datetime | None]


class ProgressResponse(BaseModel):
    task_id: str
    pct: int
    stage: str
    message: str


ACTIVE_TASK_TTL = 7200  # 2 hours — safety expiry if task crashes without cleanup


class ActiveTasksResponse(BaseModel):
    active: dict[str, str]  # chain -> task_id


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post("/scrape/all", response_model=list[TriggerResponse])
def trigger_all_scrapers(
    redis=Depends(get_redis), _admin=Depends(require_admin)
) -> list[TriggerResponse]:
    """Trigger all 4 scrapers in parallel immediately via Celery."""
    from app.workers.scraper_tasks import run_flyer_scrape

    results = []
    for chain in sorted(VALID_CHAINS):
        task = run_flyer_scrape.apply_async(args=[chain], queue="scraper")
        redis.setex(f"scrape_active:{chain}", ACTIVE_TASK_TTL, task.id)
        results.append(
            TriggerResponse(
                task_id=task.id,
                chain=chain,
                message=f"Scraper for '{chain}' queued. Task ID: {task.id}",
            )
        )
    return results


@router.post("/scrape/{chain}", response_model=TriggerResponse)
def trigger_scrape(
    chain: str,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    _admin=Depends(require_admin),
) -> TriggerResponse:
    """Trigger a scraper for a single chain immediately via Celery."""
    if chain not in VALID_CHAINS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid chain '{chain}'. Must be one of: {sorted(VALID_CHAINS)}",
        )

    from app.workers.scraper_tasks import run_flyer_scrape

    task = run_flyer_scrape.apply_async(args=[chain], queue="scraper")
    redis.setex(f"scrape_active:{chain}", ACTIVE_TASK_TTL, task.id)

    return TriggerResponse(
        task_id=task.id,
        chain=chain,
        message=f"Scraper for '{chain}' queued. Task ID: {task.id}",
    )


@router.get("/scrape/active", response_model=ActiveTasksResponse)
def get_active_tasks(
    redis=Depends(get_redis), _admin=Depends(require_admin)
) -> ActiveTasksResponse:
    """Return task IDs for chains currently running a scrape.

    Auto-clears stale keys where the progress entry no longer exists
    (e.g. after a worker crash before the key was deleted).
    """
    active = {}
    for chain in VALID_CHAINS:
        val = redis.get(f"scrape_active:{chain}")
        if not val:
            continue
        task_id = val.decode() if isinstance(val, bytes) else val
        # Only report as active if the task has a progress entry AND isn't done/failed
        progress_raw = redis.get(f"scrape_progress:{task_id}")
        if progress_raw:
            progress = json.loads(progress_raw)
            if progress.get("stage") not in ("done", "failed"):
                active[chain] = task_id
                continue
        # Stale key — clean it up
        redis.delete(f"scrape_active:{chain}")
    return ActiveTasksResponse(active=active)


@router.get("/scrape/runs", response_model=list[ScrapeRunResponse])
def list_scrape_runs(
    limit: int = 50,
    chain: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[ScrapeRunResponse]:
    """Return recent scrape run history, newest first."""
    q = db.query(FlyScrapeRun).order_by(FlyScrapeRun.run_started_at.desc())
    if chain:
        if chain not in VALID_CHAINS:
            raise HTTPException(status_code=422, detail=f"Invalid chain '{chain}'")
        q = q.filter(FlyScrapeRun.chain == chain)
    runs = q.limit(max(1, min(limit, 200))).all()
    return [
        ScrapeRunResponse(
            id=str(r.id),
            chain=r.chain,
            status=r.status,
            products_found=r.products_found or 0,
            products_new=r.products_new or 0,
            run_started_at=r.run_started_at,
            run_finished_at=r.run_finished_at,
            errors_json=r.errors_json,
        )
        for r in runs
    ]


@router.get("/scrape/progress/{task_id}", response_model=ProgressResponse)
def get_scrape_progress(
    task_id: str,
    redis=Depends(get_redis),
    _admin=Depends(require_admin),
) -> ProgressResponse:
    """Return the latest progress snapshot for a running (or recently finished) scrape task."""
    raw = redis.get(f"scrape_progress:{task_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="No progress data for this task ID")
    data = json.loads(raw)
    return ProgressResponse(
        task_id=task_id,
        pct=data.get("pct", 0),
        stage=data.get("stage", "unknown"),
        message=data.get("message", ""),
    )


@router.delete("/scrape/{chain}", status_code=200)
def cancel_scrape(
    chain: str,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    _admin=Depends(require_admin),
) -> dict:
    """Cancel an in-progress flyer scrape for a chain.

    Sets a Redis flag checked by the worker, revokes the Celery task if still
    queued, and marks the run record as cancelled.
    """
    if chain not in VALID_CHAINS:
        raise HTTPException(status_code=422, detail=f"Unknown chain '{chain}'")

    from app.models import FlyScrapeRun as FSR
    from app.workers.celery_app import celery_app

    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    run = (
        db.query(FSR)
        .filter(FSR.chain == chain, FSR.status == "running", FSR.run_started_at >= cutoff)
        .order_by(FSR.run_started_at.desc())
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail=f"No active scrape found for '{chain}'.")

    if run.celery_task_id:
        redis.setex(f"scrape_cancel:{run.celery_task_id}", 3600, "1")
        celery_app.control.revoke(run.celery_task_id, terminate=False)

    run.status = "cancelled"
    run.run_finished_at = datetime.now(timezone.utc)
    redis.delete(f"scrape_active:{chain}")
    db.commit()

    return {"chain": chain, "status": "cancelled", "task_id": run.celery_task_id}


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db), _admin=Depends(require_admin)) -> StatsResponse:
    """Return product and price report counts."""
    total_products = db.query(func.count(Product.id)).scalar() or 0
    total_price_reports = db.query(func.count(PriceReport.id)).scalar() or 0

    # Products ingested per chain (via price_reports.source)
    chain_counts = (
        db.query(PriceReport.source, func.count(PriceReport.id)).group_by(PriceReport.source).all()
    )
    products_by_chain = {row[0]: row[1] for row in chain_counts}

    # Last run per chain (any terminal status — used for "Last run: X ago" on cards)
    last_scrapes: dict[str, datetime | None] = {}
    for chain in VALID_CHAINS:
        last = (
            db.query(FlyScrapeRun.run_finished_at)
            .filter(
                FlyScrapeRun.chain == chain,
                FlyScrapeRun.status.in_(
                    ["completed", "completed_with_errors", "cancelled", "failed"]
                ),
            )
            .order_by(FlyScrapeRun.run_finished_at.desc())
            .first()
        )
        last_scrapes[chain] = last[0] if last else None

    return StatsResponse(
        total_products=total_products,
        total_price_reports=total_price_reports,
        products_by_chain=products_by_chain,
        last_scrape_by_chain=last_scrapes,
    )


@router.get("/scrape/weekly-counts")
def get_scrape_weekly_counts(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict[str, int]:
    """Return products_found from the latest run per chain (any terminal status)."""
    from sqlalchemy import text as sa_text

    rows = db.execute(
        sa_text(
            """
            SELECT DISTINCT ON (chain)
                chain, products_found
            FROM flyer_scrape_runs
            WHERE status IN ('completed', 'completed_with_errors', 'cancelled', 'failed')
            ORDER BY chain, run_started_at DESC
        """
        )
    ).fetchall()
    return {chain: products_found for chain, products_found in rows}


class DedupOverlapRow(BaseModel):
    chains: list[str]
    product_count: int


class DedupSampleProduct(BaseModel):
    product_id: str
    name: str
    chains: list[str]
    prices: dict[str, float]


class DedupStatsResponse(BaseModel):
    total_products: int
    products_in_1_chain: int
    products_in_2plus_chains: int
    products_in_3plus_chains: int
    overlap_by_combo: list[DedupOverlapRow]
    sample_multi_chain: list[DedupSampleProduct]


@router.get("/dedup/stats", response_model=DedupStatsResponse)
def get_dedup_stats(
    db: Session = Depends(get_db), _admin=Depends(require_admin)
) -> DedupStatsResponse:
    """Cross-chain product deduplication statistics."""
    rows = db.execute(
        text(
            """
        WITH chain_coverage AS (
            SELECT
                pr.product_id,
                array_agg(DISTINCT s.chain ORDER BY s.chain) AS chains,
                COUNT(DISTINCT s.chain) AS chain_count
            FROM price_reports pr
            JOIN stores s ON s.id = pr.store_id
            GROUP BY pr.product_id
        )
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE chain_count = 1)  AS one_chain,
            COUNT(*) FILTER (WHERE chain_count >= 2) AS two_plus,
            COUNT(*) FILTER (WHERE chain_count >= 3) AS three_plus
        FROM chain_coverage
    """
        )
    ).fetchone()

    total, one_chain, two_plus, three_plus = rows

    # Overlap breakdown by chain combination
    combo_rows = db.execute(
        text(
            """
        WITH chain_coverage AS (
            SELECT
                pr.product_id,
                array_agg(DISTINCT s.chain ORDER BY s.chain) AS chains
            FROM price_reports pr
            JOIN stores s ON s.id = pr.store_id
            GROUP BY pr.product_id
            HAVING COUNT(DISTINCT s.chain) >= 2
        )
        SELECT chains, COUNT(*) AS cnt
        FROM chain_coverage
        GROUP BY chains
        ORDER BY cnt DESC
        LIMIT 10
    """
        )
    ).fetchall()

    overlap = [DedupOverlapRow(chains=list(r[0]), product_count=r[1]) for r in combo_rows]

    # Sample products with prices from multiple chains
    sample_rows = db.execute(
        text(
            """
        WITH multi_chain AS (
            SELECT pr.product_id
            FROM price_reports pr
            JOIN stores s ON s.id = pr.store_id
            GROUP BY pr.product_id
            HAVING COUNT(DISTINCT s.chain) >= 2
            LIMIT 20
        ),
        latest_prices AS (
            SELECT DISTINCT ON (pr.product_id, s.chain)
                pr.product_id,
                s.chain,
                pr.price_cents
            FROM price_reports pr
            JOIN stores s ON s.id = pr.store_id
            WHERE pr.product_id IN (SELECT product_id FROM multi_chain)
            ORDER BY pr.product_id, s.chain, pr.created_at DESC
        )
        SELECT
            p.id,
            p.name,
            array_agg(DISTINCT lp.chain ORDER BY lp.chain) AS chains,
            json_object_agg(lp.chain, lp.price_cents) AS prices_json
        FROM products p
        JOIN latest_prices lp ON lp.product_id = p.id
        GROUP BY p.id, p.name
        ORDER BY array_length(array_agg(DISTINCT lp.chain), 1) DESC, p.name
        LIMIT 20
    """
        )
    ).fetchall()

    samples = [
        DedupSampleProduct(
            product_id=str(r[0]),
            name=r[1],
            chains=list(r[2]),
            prices={k: v / 100 for k, v in r[3].items()},
        )
        for r in sample_rows
    ]

    return DedupStatsResponse(
        total_products=total or 0,
        products_in_1_chain=one_chain or 0,
        products_in_2plus_chains=two_plus or 0,
        products_in_3plus_chains=three_plus or 0,
        overlap_by_combo=overlap,
        sample_multi_chain=samples,
    )


# ── Image enrichment (Feature A) ─────────────────────────────────────────────


class EnrichTriggerResponse(BaseModel):
    task_id: str
    chain: str | None
    message: str


class EnrichProgressResponse(BaseModel):
    task_id: str
    state: str  # Celery task state: PENDING, STARTED, SUCCESS, FAILURE, …
    result: dict | None = None


@router.post("/enrich/all", response_model=EnrichTriggerResponse)
def trigger_enrich_all(_admin=Depends(require_admin)) -> EnrichTriggerResponse:
    """Trigger an all-chain OFF image enrichment sweep via Celery."""
    from app.workers.image_tasks import enrich_product_images

    task = enrich_product_images.apply_async(args=[None], queue="image")
    return EnrichTriggerResponse(
        task_id=task.id,
        chain=None,
        message=f"Image enrichment (all chains) queued. Task ID: {task.id}",
    )


@router.post("/enrich/{chain}", response_model=EnrichTriggerResponse)
def trigger_enrich(chain: str, _admin=Depends(require_admin)) -> EnrichTriggerResponse:
    """Trigger OFF image enrichment for a single chain via Celery."""
    if chain not in VALID_CHAINS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid chain '{chain}'. Must be one of: {sorted(VALID_CHAINS)}",
        )
    from app.workers.image_tasks import enrich_product_images

    task = enrich_product_images.apply_async(args=[chain], queue="image")
    return EnrichTriggerResponse(
        task_id=task.id,
        chain=chain,
        message=f"Image enrichment for '{chain}' queued. Task ID: {task.id}",
    )


@router.get("/enrich/progress/{task_id}", response_model=EnrichProgressResponse)
def get_enrich_progress(task_id: str, _admin=Depends(require_admin)) -> EnrichProgressResponse:
    """Return Celery task state (and result when finished) for an enrich run."""
    from app.workers.celery_app import celery_app

    async_result = celery_app.AsyncResult(task_id)
    result = None
    if async_result.successful():
        raw = async_result.result
        if isinstance(raw, dict):
            result = raw
    return EnrichProgressResponse(
        task_id=task_id,
        state=async_result.state,
        result=result,
    )


# ── Catalog crawl (Feature B) ────────────────────────────────────────────────


class CatalogTriggerResponse(BaseModel):
    task_id: str
    chain: str
    mode: str
    message: str


class CatalogRunResponse(BaseModel):
    id: str
    chain: str
    mode: str
    status: str
    celery_task_id: str | None
    categories_found: int
    categories_crawled: int
    categories_skipped: int
    products_found: int
    products_new: int
    products_updated: int
    run_started_at: datetime
    run_finished_at: datetime | None
    errors_json: dict | None

    class Config:
        from_attributes = True


class CatalogConfigResponse(BaseModel):
    available_chains: list[str]
    enabled_chains: list[str]
    schedules: dict[str, str]  # chain -> delta cron ('' = on-demand only)


class CatalogProgressResponse(BaseModel):
    task_id: str
    state: str
    result: dict | None = None


def _catalog_chains() -> list[str]:
    from scrapers.catalog_registry import available_chains

    return available_chains()


@router.get("/catalog/config", response_model=CatalogConfigResponse)
def get_catalog_config(_admin=Depends(require_admin)) -> CatalogConfigResponse:
    """Report which chains have a catalog scraper, which are enabled, and their
    delta schedules (full crawls are always on-demand)."""
    from app.config import get_settings

    settings = get_settings()
    chains = _catalog_chains()
    enabled = [c for c in chains if getattr(settings, f"CATALOG_{c.upper()}_ENABLED", False)]
    schedules = {c: (getattr(settings, f"CATALOG_{c.upper()}_CRON", "") or "") for c in chains}
    return CatalogConfigResponse(
        available_chains=chains, enabled_chains=enabled, schedules=schedules
    )


@router.post("/catalog/all", response_model=list[CatalogTriggerResponse])
def trigger_catalog_all(
    mode: str = "delta", _admin=Depends(require_admin)
) -> list[CatalogTriggerResponse]:
    """Queue a crawl for every enabled chain (delta by default)."""
    if mode not in ("full", "delta", "specials"):
        raise HTTPException(status_code=422, detail="mode must be 'full', 'delta', or 'specials'")
    from app.config import get_settings
    from app.workers.catalog_tasks import run_catalog_crawl

    settings = get_settings()
    out: list[CatalogTriggerResponse] = []
    for chain in _catalog_chains():
        if not getattr(settings, f"CATALOG_{chain.upper()}_ENABLED", False):
            continue
        task = run_catalog_crawl.apply_async(args=[chain, mode], queue="catalog")
        out.append(
            CatalogTriggerResponse(
                task_id=task.id,
                chain=chain,
                mode=mode,
                message=f"Catalog {mode} crawl for '{chain}' queued. Task ID: {task.id}",
            )
        )
    return out


@router.post("/catalog/{chain}", response_model=CatalogTriggerResponse)
def trigger_catalog(
    chain: str,
    mode: str = "full",
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> CatalogTriggerResponse:
    """Trigger a catalog crawl for one chain. Returns 409 if one is already in progress."""
    if mode not in ("full", "delta", "specials"):
        raise HTTPException(status_code=422, detail="mode must be 'full', 'delta', or 'specials'")
    if chain not in _catalog_chains():
        raise HTTPException(
            status_code=422,
            detail=f"No catalog scraper for '{chain}'. Available: {_catalog_chains()}",
        )

    # Guard: reject if a run for this chain is already active in DB
    from app.models import CatalogCrawlRun as CCR

    active_statuses = ("running", "in_progress", "started", "pending")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    active = (
        db.query(CCR)
        .filter(CCR.chain == chain, CCR.status.in_(active_statuses), CCR.run_started_at >= cutoff)
        .first()
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"A {chain} catalog crawl is already running (started {active.run_started_at.isoformat()}).",
        )

    from app.workers.catalog_tasks import run_catalog_crawl

    task = run_catalog_crawl.apply_async(args=[chain, mode], queue="catalog")
    return CatalogTriggerResponse(
        task_id=task.id,
        chain=chain,
        mode=mode,
        message=f"Catalog {mode} crawl for '{chain}' queued. Task ID: {task.id}",
    )


class CatalogActiveResponse(BaseModel):
    active: dict[str, str]  # chain -> run_id (UUID string)


@router.get("/catalog/active", response_model=CatalogActiveResponse)
def get_catalog_active(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> CatalogActiveResponse:
    """Return chains that currently have an in-progress catalog crawl (started within 4h)."""
    from app.models import CatalogCrawlRun as CCR

    active_statuses = ("running", "in_progress", "started", "pending")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    rows = (
        db.query(CCR)
        .filter(CCR.status.in_(active_statuses), CCR.run_started_at >= cutoff)
        .order_by(CCR.run_started_at.desc())
        .all()
    )
    # One entry per chain — keep the most recent
    seen: dict[str, str] = {}
    for row in rows:
        if row.chain not in seen:
            seen[row.chain] = row.celery_task_id or str(row.id)
    return CatalogActiveResponse(active=seen)


@router.get("/catalog/runs", response_model=list[CatalogRunResponse])
def list_catalog_runs(
    limit: int = 50,
    chain: str | None = None,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[CatalogRunResponse]:
    """Return recent catalog crawl runs, newest first."""
    from app.models import CatalogCrawlRun

    q = db.query(CatalogCrawlRun).order_by(CatalogCrawlRun.run_started_at.desc())
    if chain:
        q = q.filter(CatalogCrawlRun.chain == chain)
    runs = q.limit(max(1, min(limit, 200))).all()
    return [
        CatalogRunResponse(
            id=str(r.id),
            chain=r.chain,
            mode=r.mode,
            status=r.status,
            celery_task_id=r.celery_task_id,
            categories_found=r.categories_found,
            categories_crawled=r.categories_crawled,
            categories_skipped=r.categories_skipped,
            products_found=r.products_found,
            products_new=r.products_new,
            products_updated=r.products_updated,
            run_started_at=r.run_started_at,
            run_finished_at=r.run_finished_at,
            errors_json=r.errors_json,
        )
        for r in runs
    ]


@router.get("/catalog/product-counts")
def get_catalog_product_counts(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict[str, int]:
    """Return total product count per chain (keyed by chain name, not external_source)."""
    from sqlalchemy import text as sa_text

    rows = db.execute(
        sa_text(
            "SELECT external_source, COUNT(*) FROM products "
            "WHERE external_source LIKE 'catalog_%' "
            "GROUP BY external_source"
        )
    ).fetchall()
    return {src.replace("catalog_", ""): cnt for src, cnt in rows}


@router.get("/catalog/progress/{task_id}", response_model=CatalogProgressResponse)
def get_catalog_progress(task_id: str, _admin=Depends(require_admin)) -> CatalogProgressResponse:
    """Return Celery task state (and result when finished) for a catalog crawl."""
    from app.workers.celery_app import celery_app

    async_result = celery_app.AsyncResult(task_id)
    result = None
    if async_result.successful() and isinstance(async_result.result, dict):
        result = async_result.result
    return CatalogProgressResponse(task_id=task_id, state=async_result.state, result=result)


class CatalogCoverageRow(BaseModel):
    chain: str
    total_products: int
    brand_pct: float
    image_pct: float
    package_size_pct: float
    unit_price_pct: float
    unit_label_pct: float
    category_pct: float
    gtin_pct: float
    last_crawled: datetime | None
    categories_crawled: int
    categories_total: int


@router.get("/catalog/coverage", response_model=list[CatalogCoverageRow])
def get_catalog_coverage(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[CatalogCoverageRow]:
    """Per-chain field coverage stats — total products, % non-null for key fields, last crawl."""
    rows = db.execute(
        text(
            """
        SELECT
            external_source,
            COUNT(*)                                             AS total,
            ROUND(COUNT(brand)       * 100.0 / COUNT(*), 1)     AS brand_pct,
            ROUND(COUNT(image_url)   * 100.0 / COUNT(*), 1)     AS image_pct,
            ROUND(COUNT(package_size)* 100.0 / COUNT(*), 1)     AS pkg_pct,
            ROUND(COUNT(unit_price)  * 100.0 / COUNT(*), 1)     AS unit_price_pct,
            ROUND(COUNT(unit_price_label)* 100.0 / COUNT(*), 1) AS unit_label_pct,
            ROUND(COUNT(category)    * 100.0 / COUNT(*), 1)     AS category_pct,
            ROUND(COUNT(gtin)        * 100.0 / COUNT(*), 1)     AS gtin_pct,
            MAX(catalog_last_seen_at)                            AS last_crawled
        FROM products
        WHERE external_source LIKE 'catalog_%'
        GROUP BY external_source
        ORDER BY total DESC
    """
        )
    ).fetchall()

    # Latest completed run per chain for category counts
    cat_rows = db.execute(
        text(
            """
        SELECT DISTINCT ON (chain)
            chain, categories_crawled, categories_found
        FROM catalog_crawl_runs
        WHERE status IN ('completed', 'completed_with_errors', 'partial')
        ORDER BY chain, run_started_at DESC
    """
        )
    ).fetchall()
    cat_map = {r[0]: (r[1] or 0, r[2] or 0) for r in cat_rows}

    out = []
    for r in rows:
        chain = r[0].replace("catalog_", "")
        cats_crawled, cats_total = cat_map.get(chain, (0, 0))
        out.append(
            CatalogCoverageRow(
                chain=chain,
                total_products=r[1],
                brand_pct=float(r[2]),
                image_pct=float(r[3]),
                package_size_pct=float(r[4]),
                unit_price_pct=float(r[5]),
                unit_label_pct=float(r[6]),
                category_pct=float(r[7]),
                gtin_pct=float(r[8]),
                last_crawled=r[9],
                categories_crawled=cats_crawled,
                categories_total=cats_total,
            )
        )
    return out


@router.delete("/catalog/{chain}", status_code=200)
def cancel_catalog_crawl(
    chain: str,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    _admin=Depends(require_admin),
) -> dict:
    """Cancel an in-progress catalog crawl for a chain.

    Sets a Redis flag that the worker checks between categories, then revokes
    the Celery task so it can't be picked up if still queued.
    """
    from app.models import CatalogCrawlRun as CCR
    from app.workers.celery_app import celery_app

    active_statuses = ("running", "in_progress", "started", "pending")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    run = (
        db.query(CCR)
        .filter(CCR.chain == chain, CCR.status.in_(active_statuses), CCR.run_started_at >= cutoff)
        .order_by(CCR.run_started_at.desc())
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail=f"No active crawl found for '{chain}'.")

    # Set the cancel flag so the worker stops after the current category.
    if run.celery_task_id:
        redis.setex(f"catalog_cancel:{run.celery_task_id}", 3600, "1")
        # Also revoke via Celery in case the task hasn't started yet.
        celery_app.control.revoke(run.celery_task_id, terminate=False)

    # Mark cancelled in DB immediately so the UI reflects the state.
    run.status = "cancelled"
    run.run_finished_at = datetime.now(timezone.utc)
    db.commit()

    return {"chain": chain, "status": "cancelled", "task_id": run.celery_task_id}


class ConfigEntry(BaseModel):
    key: str
    label: str
    description: str
    group: str
    is_secret: bool
    is_set: bool
    value: str | None  # plaintext for non-secrets; None for secrets
    updated_at: str | None
    # Typing metadata for the admin UI (default => a plain text field).
    type: str = "string"
    options: list[str] | None = None
    min_value: float | None = None
    max_value: float | None = None
    default: str | None = None


class SetConfigRequest(BaseModel):
    value: str
    description: str | None = None


class SetConfigResponse(BaseModel):
    key: str
    message: str


@router.get("/config", response_model=list[ConfigEntry])
def list_config(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[ConfigEntry]:
    """List all known config keys with set/unset status. Secret values are never returned."""
    from app.services.config_store import list_configs

    return [ConfigEntry(**entry) for entry in list_configs(db)]


@router.put("/config/{key}", response_model=SetConfigResponse)
def set_config(
    key: str,
    body: SetConfigRequest,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> SetConfigResponse:
    """Create or update a config value. Secrets are Fernet-encrypted before storage."""
    from app.services.config_store import (
        KNOWN_KEYS,
        validate_value,
    )
    from app.services.config_store import (
        set_config as _set,
    )

    valid_keys = {k["key"] for k in KNOWN_KEYS}
    if key not in valid_keys:
        raise HTTPException(status_code=422, detail=f"Unknown config key '{key}'")
    if not body.value.strip():
        raise HTTPException(status_code=422, detail="value must not be empty")

    # Typed keys (bool/float/int/enum) are validated + normalized here; plain
    # strings pass through unchanged.
    try:
        normalized = validate_value(key, body.value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    _set(key, normalized, db, description=body.description)
    db.commit()
    return SetConfigResponse(key=key, message=f"Config key '{key}' saved successfully")


@router.delete("/config/{key}", status_code=204)
def delete_config(
    key: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> None:
    """Remove a config key from the database (service will fall back to env var)."""
    from app.services.config_store import delete_config as _del

    deleted = _del(key, db)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Config key '{key}' not found")
    db.commit()


# ── Dedup Review ────────────────────────────────────────────────────────────────


class MatchProductDetail(BaseModel):
    id: str
    name: str
    brand: str | None
    category: str | None
    image_url: str | None
    chain: str | None
    price_aud: float | None
    store_url: str | None = None
    package_size: str | None = None
    unit_price: float | None = None


class MatchCandidateResponse(BaseModel):
    id: str
    score: float
    status: str
    proposed_by: str
    product_a: MatchProductDetail
    product_b: MatchProductDetail
    created_at: datetime
    ai_verdict: str | None = None
    ai_confidence: float | None = None
    ai_keep: str | None = None
    ai_reason: str | None = None

    class Config:
        from_attributes = True


class CandidatePage(BaseModel):
    items: list[MatchCandidateResponse]
    total: int


class MatchDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected", "skipped"]
    keep_id: str | None = None  # only needed for "approved"
    drop_id: str | None = None


class ManualMatchRequest(BaseModel):
    product_a_id: str
    product_b_id: str


class MatchSearchResult(BaseModel):
    id: str
    name: str
    brand: str | None
    category: str | None
    image_url: str | None
    chain: str | None
    price_aud: float | None


def _latest_price_map(session: Session, product_ids: list) -> dict:
    """Return {product_id: latest price_cents} for the given product ids."""
    if not product_ids:
        return {}

    # Cast text array to uuid[] so psycopg2 doesn't produce 'uuid = text' errors.
    rows = session.execute(
        text(
            """
            SELECT DISTINCT ON (product_id)
                product_id, price_cents
            FROM price_reports
            WHERE product_id = ANY(CAST(:ids AS uuid[]))
            ORDER BY product_id, created_at DESC
        """
        ),
        {"ids": "{" + ",".join(str(pid) for pid in product_ids) + "}"},
    ).fetchall()
    return {str(row.product_id): row.price_cents / 100.0 for row in rows}


def _product_to_detail(p: "Product", price_aud: float | None) -> MatchProductDetail:
    from app.services.ai_dedup import _build_store_url

    chain = None
    if p.external_source and p.external_source.startswith("catalog_"):
        chain = p.external_source.replace("catalog_", "")
    return MatchProductDetail(
        id=str(p.id),
        name=p.name,
        brand=p.brand,
        category=p.category,
        image_url=p.image_url,
        chain=chain,
        price_aud=price_aud,
        store_url=_build_store_url(chain, p.external_id, p.name),
        package_size=p.package_size,
        unit_price=p.unit_price,
    )


def _merge_products(session: Session, keep_id: str, drop_id: str) -> None:
    """Merge drop_id into keep_id: re-point price_reports + watchlist, delete drop."""
    session.execute(
        text("UPDATE price_reports SET product_id = :keep WHERE product_id = :drop"),
        {"keep": keep_id, "drop": drop_id},
    )
    session.execute(
        text(
            """
            UPDATE user_watchlist SET product_id = :keep
            WHERE product_id = :drop
              AND NOT EXISTS (
                SELECT 1 FROM user_watchlist u2
                WHERE u2.product_id = :keep
                  AND u2.user_id = user_watchlist.user_id
                  AND (u2.list_id = user_watchlist.list_id
                       OR (u2.list_id IS NULL AND user_watchlist.list_id IS NULL))
              )
        """
        ),
        {"keep": keep_id, "drop": drop_id},
    )
    # Remove any duplicates that couldn't be migrated
    session.execute(
        text("DELETE FROM user_watchlist WHERE product_id = :drop"),
        {"drop": drop_id},
    )
    # Invalidate other pending candidates referencing the dropped product
    session.execute(
        text(
            """
            UPDATE product_match_candidates
            SET status = 'rejected', reviewed_at = now()
            WHERE status = 'pending'
              AND (product_a_id = :drop OR product_b_id = :drop)
        """
        ),
        {"drop": drop_id},
    )
    # Recover a real barcode: if the survivor has no GTIN and the dropped
    # record carries a valid EAN-13 that no other product owns, copy it before
    # the row is deleted. Never propagates a Stockcode/checksum-invalid code.
    from app.services.ai_dedup import propagate_gtin_on_merge

    propagate_gtin_on_merge(session, keep_id, drop_id)
    session.execute(
        text("DELETE FROM products WHERE id = :drop"),
        {"drop": drop_id},
    )
    session.flush()


@router.get("/dedup/candidates", response_model=CandidatePage)
def list_dedup_candidates(
    status: str = "pending",
    min_score: float = 92.0,
    max_score: float = 100.0,
    chain: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> CandidatePage:
    """Return paginated match candidates with rich product detail."""
    from sqlalchemy import select as sa_select

    from app.models.product_match import ProductMatchCandidate

    q = (
        sa_select(ProductMatchCandidate)
        .where(
            ProductMatchCandidate.status == status,
            ProductMatchCandidate.score >= min_score,
            ProductMatchCandidate.score <= max_score,
        )
        .order_by(ProductMatchCandidate.score.desc())
    )
    total = db.execute(sa_select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.offset(offset).limit(limit)).scalars().all()

    product_ids = []
    for r in rows:
        product_ids.extend([r.product_a_id, r.product_b_id])
    price_map = _latest_price_map(db, product_ids)

    items = []
    for r in rows:
        pa = r.product_a
        pb = r.product_b
        if chain and not (
            (pa.external_source or "").endswith(chain) or (pb.external_source or "").endswith(chain)
        ):
            continue
        items.append(
            MatchCandidateResponse(
                id=str(r.id),
                score=r.score,
                status=r.status,
                proposed_by=r.proposed_by,
                product_a=_product_to_detail(pa, price_map.get(str(pa.id))),
                product_b=_product_to_detail(pb, price_map.get(str(pb.id))),
                created_at=r.created_at,
                ai_verdict=r.ai_verdict,
                ai_confidence=r.ai_confidence,
                ai_keep=r.ai_keep,
                ai_reason=r.ai_reason,
            )
        )
    return CandidatePage(items=items, total=total)


@router.post("/dedup/candidates/{candidate_id}/decide", response_model=MatchCandidateResponse)
def decide_candidate(
    candidate_id: str,
    body: MatchDecisionRequest,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> MatchCandidateResponse:
    """Approve, reject, or skip a match candidate."""
    import uuid as _uuid

    from sqlalchemy import select as sa_select

    from app.models.product_match import ProductMatchCandidate

    cid = _uuid.UUID(candidate_id)
    row = db.execute(
        sa_select(ProductMatchCandidate).where(ProductMatchCandidate.id == cid)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    keep_id = str(row.product_a_id)
    drop_id = str(row.product_b_id)

    # Capture identifying fields before any merge — on approve the candidate row
    # itself is cascade-deleted (product_match_candidates FKs use ON DELETE CASCADE),
    # so we can't read these back afterward.
    pre_score = row.score
    pre_proposed_by = row.proposed_by
    pre_created_at = row.created_at
    pre_ai_verdict = row.ai_verdict
    pre_ai_confidence = row.ai_confidence
    pre_ai_keep = row.ai_keep
    pre_ai_reason = row.ai_reason

    if body.decision == "approved":
        if body.keep_id:
            keep_id = body.keep_id
        if body.drop_id:
            drop_id = body.drop_id
        if keep_id == drop_id:
            raise HTTPException(status_code=422, detail="keep_id and drop_id must differ")
        # Run merge BEFORE any ORM flush to avoid StaleDataError.
        # _merge_products raw-SQL updates product_match_candidates (invalidating
        # siblings), so we must not have any pending ORM dirty state for this row.
        db.expire(row)
        _merge_products(db, keep_id, drop_id)

    # Update status via raw SQL to avoid ORM version-check on the row.
    db.execute(
        text(
            """
            UPDATE product_match_candidates
            SET status = :status,
                reviewed_by = :reviewed_by,
                reviewed_at = now(),
                updated_at = now()
            WHERE id = :id
        """
        ),
        {"status": body.decision, "reviewed_by": admin.email, "id": str(cid)},
    )
    db.commit()

    # Re-fetch fresh row (product_a may have been deleted on approve, so
    # for approved we return a synthetic response from the keep product only).
    row = db.execute(
        sa_select(ProductMatchCandidate).where(ProductMatchCandidate.id == cid)
    ).scalar_one_or_none()

    def safe_detail(product, pid, price_map):
        if product is None:
            return MatchProductDetail(
                id=str(pid),
                name="(merged/deleted)",
                brand=None,
                category=None,
                image_url=None,
                chain=None,
                price_aud=None,
            )
        return _product_to_detail(product, price_map.get(str(pid)))

    if row is None:
        # Expected on approve: merging deletes the dropped product, which
        # cascade-deletes this candidate row. This is success, not an error —
        # return a synthetic 200 built from the values captured pre-merge.
        keep_product = db.execute(
            sa_select(Product).where(Product.id == _uuid.UUID(keep_id))
        ).scalar_one_or_none()
        price_map = _latest_price_map(db, [keep_id])
        return MatchCandidateResponse(
            id=str(cid),
            score=pre_score,
            status="approved",
            proposed_by=pre_proposed_by,
            product_a=safe_detail(keep_product, keep_id, price_map),
            product_b=safe_detail(None, drop_id, price_map),
            created_at=pre_created_at,
            ai_verdict=pre_ai_verdict,
            ai_confidence=pre_ai_confidence,
            ai_keep=pre_ai_keep,
            ai_reason=pre_ai_reason,
        )

    product_ids = []
    if row.product_a_id:
        product_ids.append(row.product_a_id)
    if row.product_b_id:
        product_ids.append(row.product_b_id)
    price_map = _latest_price_map(db, product_ids)

    return MatchCandidateResponse(
        id=str(row.id),
        score=row.score,
        status=row.status,
        proposed_by=row.proposed_by,
        product_a=safe_detail(row.product_a, row.product_a_id, price_map),
        product_b=safe_detail(row.product_b, row.product_b_id, price_map),
        created_at=row.created_at,
        ai_verdict=row.ai_verdict,
        ai_confidence=row.ai_confidence,
        ai_keep=row.ai_keep,
        ai_reason=row.ai_reason,
    )


@router.get("/dedup/search", response_model=list[MatchSearchResult])
def search_products_for_dedup(
    q: str,
    limit: int = 20,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[MatchSearchResult]:
    """Search products by name for manual match linking."""
    from sqlalchemy import select as sa_select

    if not q or len(q.strip()) < 2:
        return []

    rows = (
        db.execute(
            sa_select(Product)
            .where(Product.name.ilike(f"%{q.strip()}%"))
            .order_by(Product.name)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    product_ids = [r.id for r in rows]
    price_map = _latest_price_map(db, product_ids)

    return [
        MatchSearchResult(
            id=str(p.id),
            name=p.name,
            brand=p.brand,
            category=p.category,
            image_url=p.image_url,
            chain=(p.external_source or "").replace("catalog_", "") or None,
            price_aud=price_map.get(str(p.id)),
        )
        for p in rows
    ]


@router.post("/dedup/propose", response_model=MatchCandidateResponse)
def propose_manual_match(
    body: ManualMatchRequest,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
) -> MatchCandidateResponse:
    """Manually propose two products as a match for review."""
    import uuid

    from sqlalchemy import select as sa_select

    from app.models.product_match import ProductMatchCandidate

    a_id = uuid.UUID(body.product_a_id)
    b_id = uuid.UUID(body.product_b_id)
    if a_id == b_id:
        raise HTTPException(status_code=422, detail="product_a_id and product_b_id must differ")

    canon_a, canon_b = sorted([a_id, b_id])

    existing = db.execute(
        sa_select(ProductMatchCandidate).where(
            ProductMatchCandidate.product_a_id == canon_a,
            ProductMatchCandidate.product_b_id == canon_b,
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise HTTPException(
            status_code=409, detail="A match candidate already exists for this pair"
        )

    row = ProductMatchCandidate(
        product_a_id=canon_a,
        product_b_id=canon_b,
        score=100.0,
        status="pending",
        proposed_by=f"admin:{admin.email}",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    product_ids = [row.product_a_id, row.product_b_id]
    price_map = _latest_price_map(db, product_ids)
    return MatchCandidateResponse(
        id=str(row.id),
        score=row.score,
        status=row.status,
        proposed_by=row.proposed_by,
        product_a=_product_to_detail(row.product_a, price_map.get(str(row.product_a_id))),
        product_b=_product_to_detail(row.product_b, price_map.get(str(row.product_b_id))),
        created_at=row.created_at,
    )


# ─── AI Dedup: SSE run endpoint ───────────────────────────────────────────────


def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _run_ai_dedup_stream(
    db: Session,
    limit: int,
    min_fuzzy: float,
    auto_merge: bool,
    dry_run: bool,
) -> Iterator[str]:
    """
    Generator that runs the AI dedup judge loop and yields SSE frames.
    Streams one 'pair' event per judged pair, then a 'done' event with totals.
    """
    from itertools import combinations

    from rapidfuzz.process import extractOne

    from app.models import Product
    from app.models.product_match import ProductMatchCandidate
    from app.services.ai_dedup import (
        AIVerdict,
        DedupDecision,
        ProductFacts,
        choose_survivor,
        judge_pair,
    )
    from app.services.config_store import get_runtime_typed
    from app.services.product_dedup import FuzzyMatcher, brands_conflict

    MIN_CANDIDATE_LEN = 15

    if not get_runtime_typed("AI_DEDUP_ENABLED", db):
        yield _sse(
            "error", {"message": "AI_DEDUP_ENABLED is false — enable it in Admin → Configs first."}
        )
        return

    model = get_runtime_typed("AI_DEDUP_MODEL", db)
    conf_min = get_runtime_typed("AI_DEDUP_CONFIDENCE_MIN", db)
    max_tokens = get_runtime_typed("AI_DEDUP_MAX_TOKENS", db)

    yield _sse(
        "config",
        {
            "model": model,
            "conf_min": conf_min,
            "max_tokens": max_tokens,
            "limit": limit,
            "min_fuzzy": min_fuzzy,
            "auto_merge": auto_merge,
            "dry_run": dry_run,
        },
    )

    # Build a fresh Anthropic client for this run, reading both the API key and
    # base URL from the DB config store first (Admin UI values), falling back to
    # environment variables. This means changes in the Admin UI take effect
    # immediately without a backend restart.
    try:
        from anthropic import Anthropic

        from app.config import get_settings as _get_settings
        from app.services.config_store import get_runtime_value

        _settings = _get_settings()
        api_key = get_runtime_value("ANTHROPIC_API_KEY", db) or _settings.ANTHROPIC_API_KEY
        base_url = get_runtime_value("ANTHROPIC_BASE_URL", db) or _settings.ANTHROPIC_BASE_URL

        if not api_key:
            yield _sse(
                "error",
                {"message": "ANTHROPIC_API_KEY is not set — add it in Admin → Configs → AI."},
            )
            return

        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        client = Anthropic(**client_kwargs)
    except Exception as exc:
        yield _sse("error", {"message": f"Failed to initialise Anthropic client: {exc}"})
        return
        return

    matcher = FuzzyMatcher()

    def _chain_of(p: Product) -> str | None:
        src = p.external_source or ""
        return src[len("catalog_") :] if src.startswith("catalog_") else None

    def _norm(p: Product) -> str:
        return matcher.normalize_name(p.name)

    def _decision_str(verdict) -> str:
        if isinstance(verdict, DedupDecision):
            return verdict.value
        return DedupDecision.SAME.value if verdict.same_product else DedupDecision.DIFFERENT.value

    products = list(
        db.execute(sa_select(Product).where(Product.external_source.like("catalog_%")))
        .scalars()
        .all()
    )

    by_chain: dict[str, list[Product]] = {}
    for p in products:
        chain = _chain_of(p)
        if chain is None:
            continue
        n = _norm(p)
        if not n or len(n) < MIN_CANDIDATE_LEN:
            continue
        by_chain.setdefault(chain, []).append(p)

    chains = sorted(by_chain)
    yield _sse(
        "status",
        {
            "message": f"Loaded {len(products)} products across {len(chains)} chain(s): "
            + ", ".join(f"{c}={len(by_chain[c])}" for c in chains)
        },
    )

    existing: dict[tuple[str, str], ProductMatchCandidate] = {}
    for row in db.execute(sa_select(ProductMatchCandidate)).scalars().all():
        existing[(str(row.product_a_id), str(row.product_b_id))] = row

    judged = same = different = vetoed = merged = 0

    for chain_a, chain_b in combinations(chains, 2):
        if limit and judged >= limit:
            break
        list_a = by_chain[chain_a]
        list_b = by_chain[chain_b]
        names_b = [_norm(p) for p in list_b]

        for pa in list_a:
            if limit and judged >= limit:
                break

            norm_a = _norm(pa)
            best = extractOne(norm_a, names_b, score_cutoff=min_fuzzy)
            if best is None:
                continue
            _name, score, idx = best
            pb = list_b[idx]

            if brands_conflict(pa.brand, pb.brand):
                vetoed += 1
                continue

            a_id, b_id = sorted([str(pa.id), str(pb.id)])
            if db.get(Product, pa.id) is None or db.get(Product, pb.id) is None:
                continue

            # Skip pairs already judged by AI (avoids re-spending API calls on reruns).
            existing_row = existing.get((a_id, b_id))
            if existing_row is not None and existing_row.ai_verdict is not None:
                continue

            facts_a = ProductFacts.from_product(pa)
            facts_b = ProductFacts.from_product(pb)

            verdict, judge_error = judge_pair(
                facts_a, facts_b, client=client, model=model, max_tokens=max_tokens
            )
            judged += 1
            decision = _decision_str(verdict)

            conf = verdict.confidence if isinstance(verdict, AIVerdict) else None
            if isinstance(verdict, AIVerdict):
                reason = verdict.reason
            elif verdict == DedupDecision.SKIPPED:
                if judged == 1:
                    yield _sse(
                        "status",
                        {
                            "message": f"⚠ AI call failed — check ANTHROPIC_API_KEY and backend logs. Pairs will show as 'skipped'. Error: {judge_error}"
                        },
                    )
                reason = f"ai call failed: {judge_error}"
            else:
                reason = f"deterministic: {decision}"

            if decision == DedupDecision.SAME.value:
                same += 1
            elif decision == DedupDecision.DIFFERENT.value:
                different += 1

            yield _sse(
                "pair",
                {
                    "judged": judged,
                    "score": round(score, 1),
                    "decision": decision,
                    "confidence": conf,
                    "reason": reason,
                    "chain_a": chain_a,
                    "chain_b": chain_b,
                    "name_a": pa.name,
                    "name_b": pb.name,
                    "dry_run": dry_run,
                },
            )

            if dry_run:
                continue

            row = existing_row
            if row is None:
                row = ProductMatchCandidate(
                    product_a_id=a_id,
                    product_b_id=b_id,
                    score=score,
                    status="pending",
                    proposed_by="ai",
                )
                db.add(row)
                existing[(a_id, b_id)] = row

            row.ai_verdict = decision
            row.ai_confidence = conf
            row.ai_keep = verdict.keep if isinstance(verdict, AIVerdict) else None
            row.ai_reason = reason[:300] if reason else None
            row.ai_model = model
            row.ai_reviewed_at = datetime.now(timezone.utc)

            # Auto-resolve confident DIFFERENT verdicts so they leave the pending queue.
            if (
                isinstance(verdict, AIVerdict)
                and not verdict.same_product
                and verdict.confidence >= conf_min
            ):
                row.status = "rejected"

            if (
                auto_merge
                and isinstance(verdict, AIVerdict)
                and verdict.same_product
                and verdict.confidence >= conf_min
            ):
                keep_id, drop_id = choose_survivor(facts_a, facts_b, verdict)
                row.status = "approved"
                db.flush()
                db.expire(row)
                _merge_products(db, str(keep_id), str(drop_id))
                merged += 1
                for lst in by_chain.values():
                    lst[:] = [p for p in lst if str(p.id) != str(drop_id)]

            if not dry_run:
                db.commit()
            elif judged % 25 == 0:
                db.flush()

    yield _sse(
        "done",
        {
            "judged": judged,
            "same": same,
            "different": different,
            "vetoed": vetoed,
            "merged": merged,
            "dry_run": dry_run,
        },
    )


@router.get("/dedup/run")
def run_ai_dedup_sse(
    limit: int = Query(default=0, ge=0, description="Max pairs to judge (0 = unlimited)"),
    min_fuzzy: float = Query(default=85.0, ge=0.0, le=100.0),
    auto_merge: bool = Query(default=False),
    dry_run: bool = Query(default=True),
    token: str = Query(default=None, include_in_schema=False),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_sse),
):
    """
    Stream AI dedup results as Server-Sent Events.
    Events: config | status | pair | done | error
    """

    def generate():
        try:
            yield from _run_ai_dedup_stream(db, limit, min_fuzzy, auto_merge, dry_run)
        except Exception as exc:
            yield _sse("error", {"message": f"Unexpected error: {exc}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

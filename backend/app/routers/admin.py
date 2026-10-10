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
from app.models import PriceReport, Product, ProductRetailerListing

router = APIRouter()

VALID_CHAINS = {"coles", "woolworths", "aldi", "iga", "drakes"}


# ── Schemas ────────────────────────────────────────────────────────────────────


class StatsResponse(BaseModel):
    total_products: int
    total_price_reports: int
    products_by_chain: dict[str, int]


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

    return StatsResponse(
        total_products=total_products,
        total_price_reports=total_price_reports,
        products_by_chain=products_by_chain,
    )


class SpecialsFreshnessItem(BaseModel):
    chain: str
    label: str
    last_run_at: datetime | None
    last_refresh_expected_at: datetime | None
    hours_since_run: float | None
    hours_overdue: float | None
    status: str  # "fresh" | "stale" | "overdue" | "never"
    refresh_days_labels: list[str]


@router.get("/scrape/freshness", response_model=list[SpecialsFreshnessItem])
def get_specials_freshness(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[SpecialsFreshnessItem]:
    """Return per-chain specials freshness based on last successful run and known refresh schedule."""
    from app.models import CatalogCrawlRun
    from app.services.specials_schedule import all_chain_freshness

    last_runs: dict[str, datetime | None] = {}
    for chain in VALID_CHAINS:
        last = (
            db.query(CatalogCrawlRun.run_finished_at)
            .filter(
                CatalogCrawlRun.chain == chain,
                CatalogCrawlRun.mode == "specials",
                CatalogCrawlRun.status.in_(["success", "completed", "completed_with_errors"]),
            )
            .order_by(CatalogCrawlRun.run_finished_at.desc())
            .first()
        )
        last_runs[chain] = last[0] if last else None

    results = all_chain_freshness(last_runs)
    return [
        SpecialsFreshnessItem(
            chain=f.chain,
            label=f.label,
            last_run_at=f.last_run_at,
            last_refresh_expected_at=f.last_refresh_expected_at,
            hours_since_run=f.hours_since_run,
            hours_overdue=f.hours_overdue,
            status=f.status,
            refresh_days_labels=f.refresh_days_labels,
        )
        for f in results
    ]


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
    current_category: str | None = None


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
    force: bool = False,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    _admin=Depends(require_admin),
) -> CatalogTriggerResponse:
    """Trigger a catalog crawl for one chain. Returns 409 if one is already in progress.

    For specials mode, also returns 409 if a successful specials run completed within
    the last 25 hours. Pass force=true to bypass that cooldown.
    """
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

    # Guard: warn if specials ran successfully within the last 25 hours.
    if mode == "specials" and not force:
        last_success = redis.get(f"specials_last_success:{chain}")
        if last_success:
            last_ts = last_success if isinstance(last_success, str) else last_success.decode()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "already_ran",
                    "message": f"Specials for '{chain}' were already scraped successfully within the last 25 hours.",
                    "last_run_at": last_ts,
                },
            )

    from app.workers.catalog_tasks import run_catalog_crawl

    task = run_catalog_crawl.apply_async(args=[chain, mode], queue="catalog")
    return CatalogTriggerResponse(
        task_id=task.id,
        chain=chain,
        mode=mode,
        message=f"Catalog {mode} crawl for '{chain}' queued. Task ID: {task.id}",
    )


class CatalogActiveEntry(BaseModel):
    task_id: str
    mode: str


class CatalogActiveResponse(BaseModel):
    active: dict[str, CatalogActiveEntry]  # chain -> {task_id, mode}


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
    seen: dict[str, CatalogActiveEntry] = {}
    for row in rows:
        if row.chain not in seen:
            seen[row.chain] = CatalogActiveEntry(
                task_id=row.celery_task_id or str(row.id),
                mode=row.mode or "full",
            )
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
    """Return distinct product count per chain (from ProductRetailerListing)."""
    rows = db.execute(
        sa_select(
            ProductRetailerListing.chain,
            func.count(ProductRetailerListing.product_id.distinct()).label("cnt"),
        ).group_by(ProductRetailerListing.chain)
    ).all()
    return {chain: cnt for chain, cnt in rows}


@router.get("/catalog/progress/{task_id}", response_model=CatalogProgressResponse)
def get_catalog_progress(
    task_id: str,
    redis=Depends(get_redis),
    _admin=Depends(require_admin),
) -> CatalogProgressResponse:
    """Return Celery task state (and result when finished) for a catalog crawl."""
    from app.workers.celery_app import celery_app

    async_result = celery_app.AsyncResult(task_id)
    result = None
    if async_result.successful() and isinstance(async_result.result, dict):
        result = async_result.result
    cur_cat_bytes = redis.get(f"catalog_curcat:{task_id}") if redis else None
    current_category = cur_cat_bytes.decode() if cur_cat_bytes else None
    return CatalogProgressResponse(
        task_id=task_id,
        state=async_result.state,
        result=result,
        current_category=current_category,
    )


class CatalogLogEntry(BaseModel):
    ts: str
    level: str
    msg: str


@router.get("/catalog/logs/{task_id}", response_model=list[CatalogLogEntry])
def get_catalog_logs(
    task_id: str,
    last_n: int = 100,
    redis=Depends(get_redis),
    _admin=Depends(require_admin),
) -> list[CatalogLogEntry]:
    """Return the last N log lines for a catalog crawl task from the Redis buffer."""
    import json as _json

    key = f"catalog_log:{task_id}"
    raw_lines = redis.lrange(key, -max(1, min(last_n, 200)), -1)
    out: list[CatalogLogEntry] = []
    for raw in raw_lines:
        try:
            data = _json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            out.append(CatalogLogEntry(ts=data["ts"], level=data["level"], msg=data["msg"]))
        except Exception:
            continue
    return out


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
    """Per-chain field coverage stats from ProductRetailerListing."""
    rows = db.execute(
        text(
            """
        SELECT
            chain,
            COUNT(*)                                                              AS total,
            ROUND(COUNT(external_brand)          * 100.0 / COUNT(*), 1)          AS brand_pct,
            ROUND(COUNT(external_image_url)      * 100.0 / COUNT(*), 1)          AS image_pct,
            ROUND(COUNT(package_size_raw)        * 100.0 / COUNT(*), 1)          AS pkg_pct,
            ROUND(COUNT(p.unit_price)            * 100.0 / COUNT(*), 1)          AS unit_price_pct,
            ROUND(COUNT(p.unit_price_label)      * 100.0 / COUNT(*), 1)          AS unit_label_pct,
            ROUND(COUNT(external_category)       * 100.0 / COUNT(*), 1)          AS category_pct,
            ROUND(COUNT(p.gtin)                  * 100.0 / COUNT(*), 1)          AS gtin_pct,
            MAX(catalog_last_seen_at)                                             AS last_crawled
        FROM product_retailer_listings prl
        JOIN products p ON p.id = prl.product_id
        GROUP BY chain
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
        cats_crawled, cats_total = cat_map.get(r[0], (0, 0))
        out.append(
            CatalogCoverageRow(
                chain=r[0],
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


class UnmappedCategoryRow(BaseModel):
    chain: str
    raw_category: str
    count: int


@router.get("/catalog/unmapped-categories", response_model=list[UnmappedCategoryRow])
def get_unmapped_categories(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> list[UnmappedCategoryRow]:
    """Return external_category values that are genuinely unknown to CATEGORY_MAP.

    Groups by (chain, external_category) and returns only rows where the category
    is not an exact key in CATEGORY_MAP and its root is not a key either.
    Known-but-intentionally-None entries (e.g. "Halloween", "Specials") are
    excluded — they are recognised as promo/layout labels and deliberately
    discarded.  Only categories the normaliser has no knowledge of at all are
    returned, ordered by count descending.
    Use this to detect new or mis-labelled retailer categories before they
    silently affect Product.category.
    """
    from app.services.category_normaliser import is_unmapped_category

    rows = db.execute(
        text(
            """
            SELECT chain, external_category, COUNT(*) AS cnt
            FROM product_retailer_listings
            WHERE external_category IS NOT NULL
            GROUP BY chain, external_category
            ORDER BY cnt DESC
            """
        )
    ).fetchall()

    return [
        UnmappedCategoryRow(chain=r[0], raw_category=r[1], count=r[2])
        for r in rows
        if is_unmapped_category(r[1])
    ]


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


def _product_to_detail(p: "Product", price_aud: float | None, db: Session) -> MatchProductDetail:
    from app.services.ai_dedup import _build_store_url

    listing = db.execute(
        sa_select(ProductRetailerListing)
        .where(ProductRetailerListing.product_id == p.id)
        .order_by(ProductRetailerListing.catalog_last_seen_at.desc().nulls_last())
        .limit(1)
    ).scalar_one_or_none()

    chain = listing.chain if listing else None
    external_id = listing.external_id if listing else None
    image = p.image_url or (listing.external_image_url if listing else None)
    package_size = listing.package_size_raw if listing else None

    return MatchProductDetail(
        id=str(p.id),
        name=p.name,
        brand=p.brand,
        category=p.category,
        image_url=image,
        chain=chain,
        price_aud=price_aud,
        store_url=_build_store_url(chain, external_id, p.name),
        package_size=package_size,
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
        if chain:
            pa_chains = {l.chain for l in pa.retailer_listings}
            pb_chains = {l.chain for l in pb.retailer_listings}
            if chain not in pa_chains and chain not in pb_chains:
                continue
        items.append(
            MatchCandidateResponse(
                id=str(r.id),
                score=r.score,
                status=r.status,
                proposed_by=r.proposed_by,
                product_a=_product_to_detail(pa, price_map.get(str(pa.id)), db),
                product_b=_product_to_detail(pb, price_map.get(str(pb.id)), db),
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
        return _product_to_detail(product, price_map.get(str(pid)), db)

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
            image_url=p.image_url or p.external_image_url,
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
        product_a=_product_to_detail(row.product_a, price_map.get(str(row.product_a_id)), db),
        product_b=_product_to_detail(row.product_b, price_map.get(str(row.product_b_id)), db),
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
        if p.retailer_listings:
            return p.retailer_listings[0].chain
        return None

    def _norm(p: Product) -> str:
        return matcher.normalize_name(p.name)

    def _decision_str(verdict) -> str:
        if isinstance(verdict, DedupDecision):
            return verdict.value
        return DedupDecision.SAME.value if verdict.same_product else DedupDecision.DIFFERENT.value

    has_listing = (
        sa_select(ProductRetailerListing.id)
        .where(ProductRetailerListing.product_id == Product.id)
        .exists()
    )
    products = list(db.execute(sa_select(Product).where(has_listing)).scalars().all())

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


# ── Image enrichment status ───────────────────────────────────────────────────


class ImageEnrichmentStatus(BaseModel):
    total_products: int
    with_self_hosted_image: int
    with_any_image: int
    coverage_pct: float
    by_chain: dict[str, float]


@router.get("/image-enrichment/status", response_model=ImageEnrichmentStatus)
def get_image_enrichment_status(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> ImageEnrichmentStatus:
    """Image enrichment coverage: total products, self-hosted %, by-chain breakdown."""
    row = db.execute(
        text(
            "SELECT COUNT(*) AS total, "
            "COUNT(image_url) AS with_self_hosted, "
            "COUNT(CASE WHEN image_url IS NOT NULL OR EXISTS("
            "  SELECT 1 FROM product_retailer_listings prl"
            "  WHERE prl.product_id = products.id AND prl.external_image_url IS NOT NULL"
            ") THEN 1 END) AS with_any "
            "FROM products"
        )
    ).one()
    total = row.total or 0
    with_self_hosted = row.with_self_hosted or 0
    with_any = row.with_any or 0
    coverage = round(with_self_hosted * 100.0 / total, 1) if total else 0.0

    chain_rows = db.execute(
        text(
            "SELECT "
            "  REPLACE(pr.source, 'catalog_', '') AS chain, "
            "  COUNT(DISTINCT p.id)               AS total_in_chain, "
            "  COUNT(DISTINCT p.id) FILTER (WHERE p.image_url IS NOT NULL) AS enriched "
            "FROM products p "
            "JOIN price_reports pr ON pr.product_id = p.id "
            "WHERE pr.source LIKE 'catalog_%' "
            "GROUP BY 1"
        )
    ).fetchall()

    by_chain: dict[str, float] = {}
    for r in chain_rows:
        pct = round(r.enriched * 100.0 / r.total_in_chain, 1) if r.total_in_chain else 0.0
        by_chain[r.chain] = pct

    return ImageEnrichmentStatus(
        total_products=total,
        with_self_hosted_image=with_self_hosted,
        with_any_image=with_any,
        coverage_pct=coverage,
        by_chain=by_chain,
    )


# ─── Image Review: schemas ─────────────────────────────────────────────────────


class ImageCandidateItem(BaseModel):
    product_id: str
    name: str
    brand: str | None = None
    chain: str | None = None
    retailer_image_url: str | None = None
    off_image_url: str | None = None
    off_image_score: float | None = None
    off_image_off_code: str | None = None
    off_image_off_name: str | None = None
    off_image_status: str | None = None
    off_image_ai_verdict: str | None = None
    off_image_ai_reason: str | None = None


class ImageCandidatePage(BaseModel):
    items: list[ImageCandidateItem]
    total: int


class ImageDecideRequest(BaseModel):
    decision: Literal["approved", "rejected"]


# ─── Image Review: list candidates ────────────────────────────────────────────


@router.get("/image-review/candidates", response_model=ImageCandidatePage)
def list_image_candidates(
    status: str = "pending",
    chain: str | None = None,
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> ImageCandidatePage:
    """Paginated list of OFF image candidates for review."""
    stmt = sa_select(Product).where(Product.off_image_status == status)
    if min_score > 0:
        stmt = stmt.where(Product.off_image_score >= min_score)
    if chain:
        stmt = (
            stmt.join(PriceReport, PriceReport.product_id == Product.id)
            .where(PriceReport.source == f"catalog_{chain}")
            .distinct()
        )

    total_stmt = sa_select(func.count()).select_from(stmt.subquery())
    total = db.execute(total_stmt).scalar() or 0

    products = (
        db.execute(
            stmt.order_by(Product.off_image_score.desc().nullslast()).offset(offset).limit(limit)
        )
        .scalars()
        .all()
    )

    items: list[ImageCandidateItem] = []
    for p in products:
        chain_row = db.execute(
            text(
                "SELECT REPLACE(source, 'catalog_', '') AS chain "
                "FROM price_reports WHERE product_id = :pid AND source LIKE 'catalog_%' LIMIT 1"
            ),
            {"pid": str(p.id)},
        ).fetchone()
        chain_name = chain_row.chain if chain_row else None
        listing_image = None
        if chain_name:
            lr = db.execute(
                sa_select(ProductRetailerListing.external_image_url)
                .where(
                    ProductRetailerListing.product_id == p.id,
                    ProductRetailerListing.chain == chain_name,
                )
                .order_by(ProductRetailerListing.catalog_last_seen_at.desc().nulls_last())
                .limit(1)
            ).scalar_one_or_none()
            listing_image = lr
        items.append(
            ImageCandidateItem(
                product_id=str(p.id),
                name=p.name,
                brand=p.brand,
                chain=chain_name,
                retailer_image_url=listing_image,
                off_image_url=p.off_image_url,
                off_image_score=p.off_image_score,
                off_image_off_code=p.off_image_off_code,
                off_image_off_name=p.off_image_off_name,
                off_image_status=p.off_image_status,
                off_image_ai_verdict=p.off_image_ai_verdict,
                off_image_ai_reason=p.off_image_ai_reason,
            )
        )
    return ImageCandidatePage(items=items, total=total)


# ─── Image Review: approve / reject ────────────────────────────────────────────


@router.post("/image-review/{product_id}/decide")
def decide_image_candidate(
    product_id: str,
    body: ImageDecideRequest,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    """Approve or reject an OFF image candidate.

    Approved: copies off_image_url → image_url (goes live).
    Rejected: clears the pending candidate (product keeps retailer CDN image).
    """
    from uuid import UUID

    try:
        pk = UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid product_id")
    product = db.get(Product, pk)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if not product.off_image_url:
        raise HTTPException(
            status_code=400, detail="No pending OFF image candidate for this product"
        )

    if body.decision == "approved":
        product.image_url = product.off_image_url
        product.image_source = "open_food_facts"
        product.image_enriched_at = datetime.now(timezone.utc)
        product.off_image_status = "approved"
    else:
        product.off_image_status = "rejected"
        product.off_image_url = None
        product.off_image_score = None
        product.off_image_off_code = None
        product.off_image_off_name = None

    db.commit()
    return {"status": "ok", "decision": body.decision, "product_id": product_id}


# ─── Image Review: AI vision run (SSE) ────────────────────────────────────────


def _run_image_ai_review_stream(
    db: Session,
    limit: int,
    min_score: float,
    auto_approve: bool,
    min_confidence: float,
    dry_run: bool,
) -> Iterator[str]:
    """Generator that runs the AI vision image review loop and yields SSE frames.

    Downloads both the retailer CDN image and the OFF candidate image server-side
    (base64-encodes them), then asks Claude vision whether the OFF image is a
    correct substitute for the product. Works on localhost — no public CDN needed.
    """
    import base64

    import httpx

    from app.config import get_settings as _get_settings
    from app.services.config_store import get_runtime_value

    _settings = _get_settings()
    api_key = get_runtime_value("ANTHROPIC_API_KEY", db) or _settings.ANTHROPIC_API_KEY
    base_url = get_runtime_value("ANTHROPIC_BASE_URL", db) or _settings.ANTHROPIC_BASE_URL

    if not api_key:
        yield _sse(
            "error", {"message": "ANTHROPIC_API_KEY is not set — add it in Admin → Configs → AI."}
        )
        return

    try:
        from anthropic import Anthropic

        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = Anthropic(**client_kwargs)
    except Exception as exc:
        yield _sse("error", {"message": f"Failed to initialise Anthropic client: {exc}"})
        return

    stmt = sa_select(Product).where(Product.off_image_status == "pending")
    if min_score > 0:
        stmt = stmt.where(Product.off_image_score >= min_score)
    candidates = (
        db.execute(stmt.order_by(Product.off_image_score.desc().nullslast()).limit(limit))
        .scalars()
        .all()
    )

    yield _sse(
        "config",
        {
            "limit": limit,
            "min_score": min_score,
            "auto_approve": auto_approve,
            "min_confidence": min_confidence,
            "dry_run": dry_run,
            "candidates": len(candidates),
        },
    )
    yield _sse("status", {"message": f"Loaded {len(candidates)} pending candidates"})

    approved = rejected = skipped = 0

    def _fetch_image(url: str | None) -> tuple[bytes, str] | None:
        if not url:
            return None
        try:
            r = httpx.get(
                url,
                timeout=15,
                follow_redirects=True,
                headers={"User-Agent": "dealfindio-admin/1.0"},
            )
            r.raise_for_status()
            ct = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            return r.content, ct
        except Exception:
            return None

    system_prompt = (
        "You are an image quality-control agent for a grocery price-comparison app. "
        "You will be shown two product images: the LEFT image is from the retailer's website, "
        "the RIGHT image is a candidate from Open Food Facts. "
        "Decide whether the RIGHT image is an acceptable substitute for displaying on the product page. "
        "Criteria: same product identity (not just similar category), presentable quality, no obvious mismatch. "
        'Reply with JSON only — no markdown, no explanation outside the JSON: {"approved": true|false, "confidence": 0.0–1.0, "reason": "≤100 chars"}'
    )

    for product in candidates:
        retailer_image_url = db.execute(
            sa_select(ProductRetailerListing.external_image_url)
            .where(ProductRetailerListing.product_id == product.id)
            .where(ProductRetailerListing.external_image_url.isnot(None))
            .order_by(ProductRetailerListing.catalog_last_seen_at.desc().nulls_last())
            .limit(1)
        ).scalar_one_or_none()
        retailer_data = _fetch_image(retailer_image_url)
        off_data = _fetch_image(product.off_image_url)

        if not retailer_data or not off_data:
            skipped += 1
            yield _sse(
                "item",
                {
                    "product_id": str(product.id),
                    "name": product.name,
                    "verdict": "skipped",
                    "reason": "could not fetch one or both images",
                    "confidence": None,
                },
            )
            if not dry_run:
                product.off_image_ai_verdict = "skipped"
                product.off_image_ai_reason = "image fetch failed"
                product.off_image_ai_reviewed_at = datetime.now(timezone.utc)
            continue

        retailer_bytes, retailer_ct = retailer_data
        off_bytes, off_ct = off_data
        user_content = [
            {
                "type": "text",
                "text": (
                    f"Product name: {product.name}\n"
                    f"Brand: {product.brand or 'unknown'}\n"
                    f"Match score: {product.off_image_score:.0f}/100\n"
                    f"OFF product name: {product.off_image_off_name or 'unknown'}\n\n"
                    "LEFT image = retailer (currently live). RIGHT image = OFF candidate."
                ),
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": retailer_ct,
                    "data": base64.b64encode(retailer_bytes).decode(),
                },
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": off_ct,
                    "data": base64.b64encode(off_bytes).decode(),
                },
            },
        ]

        try:
            response = client.messages.create(
                model=_settings.AI_DEDUP_MODEL
                if hasattr(_settings, "AI_DEDUP_MODEL")
                else "claude-sonnet-4-6",
                max_tokens=256,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = response.content[0].text.strip()
            # Strip JSON fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            import json as _json

            verdict_data = _json.loads(raw)
            ai_approved: bool = bool(verdict_data.get("approved", False))
            confidence: float = float(verdict_data.get("confidence", 0.5))
            reason: str = str(verdict_data.get("reason", ""))[:300]
        except Exception as exc:
            skipped += 1
            yield _sse(
                "item",
                {
                    "product_id": str(product.id),
                    "name": product.name,
                    "verdict": "skipped",
                    "reason": f"AI error: {exc}",
                    "confidence": None,
                },
            )
            continue

        verdict_str = "approved" if ai_approved else "rejected"
        high_confidence = confidence >= min_confidence

        if not dry_run:
            product.off_image_ai_verdict = verdict_str
            product.off_image_ai_reason = reason
            product.off_image_ai_reviewed_at = datetime.now(timezone.utc)

            if auto_approve and high_confidence:
                if ai_approved:
                    product.image_url = product.off_image_url
                    product.image_source = "open_food_facts"
                    product.image_enriched_at = datetime.now(timezone.utc)
                    product.off_image_status = "approved"
                    approved += 1
                else:
                    product.off_image_status = "rejected"
                    product.off_image_url = None
                    product.off_image_score = None
                    product.off_image_off_code = None
                    product.off_image_off_name = None
                    rejected += 1
            else:
                if ai_approved:
                    approved += 1
                else:
                    rejected += 1
        else:
            if ai_approved:
                approved += 1
            else:
                rejected += 1

        yield _sse(
            "item",
            {
                "product_id": str(product.id),
                "name": product.name,
                "off_name": product.off_image_off_name,
                "score": product.off_image_score,
                "verdict": verdict_str,
                "confidence": confidence,
                "reason": reason,
                "auto_actioned": auto_approve and high_confidence and not dry_run,
            },
        )

    if not dry_run:
        db.commit()

    yield _sse(
        "done",
        {
            "total": len(candidates),
            "approved": approved,
            "rejected": rejected,
            "skipped": skipped,
            "dry_run": dry_run,
        },
    )


@router.get("/image-review/run")
def run_image_ai_review(
    limit: int = Query(20, ge=1, le=500),
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    auto_approve: bool = False,
    min_confidence: float = Query(0.8, ge=0.0, le=1.0),
    dry_run: bool = True,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_sse),
) -> StreamingResponse:
    """SSE stream: run AI vision review over pending OFF image candidates."""
    return StreamingResponse(
        _run_image_ai_review_stream(db, limit, min_score, auto_approve, min_confidence, dry_run),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

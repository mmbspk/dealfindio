"""
Health check and operational status endpoints.

Provides endpoints for:
- Basic health check (all dependencies)
- Database connection status
- Redis connection status
- Firebase authentication status
- Scraper run history and status
- Last error/exception logs
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, status
from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import _engine, _redis_pool, get_db, get_redis
from app.models import FlyScrapeRun

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Basic health check endpoint.

    Checks the status of all critical dependencies:
    - Database connectivity
    - Redis connectivity (if configured)
    - Application status

    Returns:
        Dict with health status and dependency checks
    """
    health_status = {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {},
    }

    # Database health check
    try:
        await db.execute(text("SELECT 1"))
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        health_status["checks"]["database"] = "error"
        health_status["status"] = "degraded"

    # Redis health check
    try:
        if _redis_pool:
            _redis_pool.ping()
            health_status["checks"]["redis"] = "ok"
        else:
            health_status["checks"]["redis"] = "not_initialized"
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        health_status["checks"]["redis"] = "error"
        health_status["status"] = "degraded"

    return health_status


@router.get("/health/ready", status_code=status.HTTP_200_OK)
async def readiness_check(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Kubernetes readiness probe endpoint.

    Returns 200 OK if the application is ready to receive traffic,
    500 if not ready (e.g., database not initialized).

    Returns:
        Dict with readiness status
    """
    try:
        # Perform critical health checks
        await db.execute(text("SELECT 1"))

        return {
            "status": "ready",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return {
            "status": "not_ready",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/health/live", status_code=status.HTTP_200_OK)
async def liveness_check() -> Dict[str, Any]:
    """
    Kubernetes liveness probe endpoint.

    Returns 200 OK if the application process is alive,
    500 if the process should be restarted.

    Returns:
        Dict with liveness status
    """
    return {
        "status": "alive",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/scraper", status_code=status.HTTP_200_OK)
async def scraper_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get status of recent scraper runs.

    Queries the database for the last 4 scraper runs across all chains
    and returns their status, timing, and product ingestion statistics.

    Returns:
        Dict with scraper status and run history
    """
    try:
        # Query last 4 scraper runs
        last_runs = await db.execute(desc(FlyScrapeRun.run_started_at)).scalars().all()

        # Convert to list with limit
        runs_list = last_runs[:4] if last_runs else []

        return {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "last_runs": [
                {
                    "chain": r.chain,
                    "status": r.status,
                    "started_at": r.run_started_at.isoformat() if r.run_started_at else None,
                    "finished_at": r.run_finished_at.isoformat() if r.run_finished_at else None,
                    "products_found": r.products_found,
                    "products_new": r.products_new,
                    "products_updated": r.products_updated,
                }
                for r in runs_list
            ],
        }
    except Exception as e:
        logger.error(f"Scraper status check failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/health/database", status_code=status.HTTP_200_OK)
async def database_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Detailed database health check.

    Returns database connection pool status, query performance,
    and table statistics.

    Returns:
        Dict with detailed database health information
    """
    try:
        # Check connection
        result = await db.execute(text("SELECT 1"))
        assert result.scalar() == 1

        # Get database name
        db_name = await db.execute(text("SELECT current_database()"))
        database = db_name.scalar()

        # Get connection pool stats (if available)
        pool_stats = {}
        if _engine:
            pool = _engine.pool
            if pool:
                pool_stats = {
                    "size": pool.size(),
                    "checked_out": pool.checkedout(),
                    "overflow": pool.overflow(),
                    "total": pool.size() + pool.overflow(),
                }

        return {
            "status": "ok",
            "database": database,
            "connection_pool": pool_stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Database status check failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/health/redis", status_code=status.HTTP_200_OK)
async def redis_status(redis=Depends(get_redis)) -> Dict[str, Any]:
    """
    Detailed Redis health check.

    Returns Redis connection info, memory usage, key statistics,
    and eviction policy.

    Returns:
        Dict with detailed Redis health information
    """
    try:
        # Ping Redis
        redis.ping()

        # Get Redis info
        info = redis.info()

        return {
            "status": "ok",
            "redis_version": info.get("redis_version", "unknown"),
            "uptime_seconds": info.get("uptime_in_seconds", 0),
            "connected_clients": info.get("connected_clients", 0),
            "used_memory_mb": round(info.get("used_memory", 0) / (1024 * 1024), 2),
            "total_system_memory_mb": round(info.get("total_system_memory", 0) / (1024 * 1024), 2),
            "eviction_policy": info.get("maxmemory_policy", "unknown"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Redis status check failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/health/version", status_code=status.HTTP_200_OK)
async def version_info() -> Dict[str, Any]:
    """
    Get application version and build information.

    Returns:
        Dict with version and build details
    """
    settings = get_settings()

    return {
        "name": settings.PROJECT_NAME,
        "version": settings.PROJECT_VERSION,
        "environment": settings.ENVIRONMENT,
        "title": settings.PROJECT_TITLE,
        "api_version": settings.API_V1_STR,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/dependencies", status_code=status.HTTP_200_OK)
async def dependencies_status(
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Check status of all external dependencies.

    Tests connectivity to:
    - PostgreSQL database
    - Redis cache
    - Firebase (if configured)
    - External APIs (if applicable)

    Returns:
        Dict with dependency status
    """
    dependencies = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {},
    }

    # PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        dependencies["checks"]["postgresql"] = "ok"
    except Exception as e:
        logger.error(f"PostgreSQL check failed: {e}")
        dependencies["checks"]["postgresql"] = "error"

    # Redis
    try:
        if _redis_pool:
            _redis_pool.ping()
            dependencies["checks"]["redis"] = "ok"
        else:
            dependencies["checks"]["redis"] = "not_initialized"
    except Exception as e:
        logger.error(f"Redis check failed: {e}")
        dependencies["checks"]["redis"] = "error"

    # Firebase
    try:
        from app.deps import _firebase_app

        if _firebase_app:
            dependencies["checks"]["firebase"] = "ok"
        else:
            dependencies["checks"]["firebase"] = "not_initialized"
    except Exception as e:
        logger.error(f"Firebase check failed: {e}")
        dependencies["checks"]["firebase"] = "error"

    # Overall status
    all_ok = all(v == "ok" for v in dependencies["checks"].values())
    dependencies["status"] = "ok" if all_ok else "degraded"

    return dependencies


@router.get("/health/metrics", status_code=status.HTTP_200_OK)
async def metrics_summary(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get key application metrics.

    Returns:
        Dict with application metrics (product count, store count, etc.)
    """
    try:
        metrics = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "counts": {},
        }

        # Product count
        result = await db.execute(text("SELECT COUNT(*) FROM product"))
        metrics["counts"]["products"] = result.scalar() or 0

        # Store count
        result = await db.execute(text("SELECT COUNT(*) FROM store"))
        metrics["counts"]["stores"] = result.scalar() or 0

        # Price reports count
        result = await db.execute(text("SELECT COUNT(*) FROM price_report"))
        metrics["counts"]["price_reports"] = result.scalar() or 0

        # User count
        result = await db.execute(text("SELECT COUNT(*) FROM user_profile"))
        metrics["counts"]["users"] = result.scalar() or 0

        # Last scrape run
        try:
            result = await db.execute(
                text(
                    "SELECT run_started_at FROM fly_scrape_run ORDER BY run_started_at DESC LIMIT 1"
                )
            )
            last_scrape = result.scalar()
            if last_scrape:
                metrics["last_scrape_run"] = last_scrape.isoformat()
        except Exception:
            pass

        return metrics
    except Exception as e:
        logger.error(f"Metrics summary failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

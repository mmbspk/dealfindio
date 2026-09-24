"""
FastAPI application factory and initialization.

Creates and configures the FastAPI application with middleware stack,
error handling, dependency injection, and startup/shutdown lifespan.
"""

import json
import logging
import logging.config
import sys
from contextlib import asynccontextmanager
from typing import Any, Optional

import sentry_sdk
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger

from app.config import get_settings
from app.deps import (
    DatabaseException,
    InvalidTokenException,
    close_connections,
    init_database,
    init_firebase,
    init_redis,
)

logger = logging.getLogger(__name__)


def setup_logging(settings) -> None:
    """
    Configure logging based on environment.

    In production, uses JSON format for structured logging.
    In development, uses colorized console output.

    Args:
        settings: Application settings
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    if settings.ENVIRONMENT == "production" and settings.LOG_FORMAT == "json":
        # JSON logging for production
        handler = logging.StreamHandler(sys.stdout)
        formatter = jsonlogger.JsonFormatter()
        handler.setFormatter(formatter)

        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        root_logger.addHandler(handler)

        logger.info("JSON logging configured for production")
    else:
        # Colorized console logging for development
        logging.basicConfig(
            level=log_level,
            format=(
                "%(asctime)s - %(name)s - %(levelname)s - "
                "[%(filename)s:%(lineno)d] - %(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        logger.info("Console logging configured for development")


def setup_sentry(settings) -> None:
    """
    Initialize Sentry for error tracking if DSN is configured.

    Args:
        settings: Application settings
    """
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=0.1,
            profiles_sample_rate=0.1,
        )
        logger.info(f"Sentry initialized for environment: {settings.ENVIRONMENT}")
    else:
        logger.debug("Sentry DSN not configured, skipping initialization")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager for startup and shutdown.

    Initializes database, Redis, and Firebase on startup.
    Gracefully closes all connections on shutdown.

    Args:
        app: FastAPI application instance
    """
    logger.info("Starting up application...")

    try:
        settings = get_settings()

        # Initialize database
        try:
            init_database(
                settings.DATABASE_URL,
                settings.DATABASE_POOL_SIZE,
                settings.DATABASE_ECHO,
            )
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

        # Initialize Redis
        try:
            init_redis(
                settings.REDIS_URL,
                settings.REDIS_SOCKET_CONNECT_TIMEOUT,
            )
            logger.info("Redis initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Redis: {e}")
            # Don't fail startup if Redis is unavailable

        # Initialize Firebase
        if settings.FIREBASE_PROJECT_ID:
            try:
                init_firebase(
                    settings.FIREBASE_PROJECT_ID,
                    settings.FIREBASE_SERVICE_ACCOUNT_JSON,
                )
                logger.info("Firebase initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Firebase: {e}")
                raise
        else:
            logger.warning("Firebase not configured (FIREBASE_PROJECT_ID not set)")

        logger.info("All services initialized successfully")

    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise

    yield  # Application runs here

    logger.info("Shutting down application...")

    try:
        close_connections()
        logger.info("All connections closed successfully")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured FastAPI application instance
    """
    settings = get_settings()

    # Setup logging
    setup_logging(settings)

    # Setup Sentry
    setup_sentry(settings)

    logger.info(f"Creating FastAPI application (environment: {settings.ENVIRONMENT})")

    # Create FastAPI app
    app = FastAPI(
        title=settings.PROJECT_TITLE,
        description=settings.PROJECT_DESCRIPTION,
        version=settings.PROJECT_VERSION,
        openapi_url=settings.OPENAPI_URL,
        docs_url=settings.OPENAPI_DOCS_URL,
        redoc_url=settings.OPENAPI_REDOC_URL,
        lifespan=lifespan,
    )

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count", "X-Page", "X-Per-Page"],
    )
    logger.debug(f"CORS middleware configured for origins: {settings.cors_origins}")

    # Trusted Host Middleware
    # Trusted host middleware. In development/testing we allow any host so the
    # test client ("testserver") and local tooling work; in production we
    # restrict to the known dealfindio hosts.
    if settings.ENVIRONMENT in ("development", "testing"):
        trusted_hosts = ["*"]
    else:
        trusted_hosts = [
            "localhost",
            "127.0.0.1",
            "*.dealfindio.com.au",
            "dealfindio.com.au",
        ]
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=trusted_hosts,
    )
    logger.debug("Trusted host middleware configured")

    # Request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Log incoming requests and responses."""
        logger.info(f"{request.method} {request.url.path}")

        try:
            response = await call_next(request)
            logger.info(
                f"{request.method} {request.url.path} - {response.status_code}"
            )
            return response
        except Exception as e:
            logger.error(f"Request error: {request.method} {request.url.path} - {e}")
            raise

    # Exception handlers
    @app.exception_handler(InvalidTokenException)
    async def invalid_token_exception_handler(
        request: Request, exc: InvalidTokenException
    ):
        """Handle invalid token exceptions."""
        logger.warning(f"Invalid token for {request.method} {request.url.path}")
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "type": "authentication_error"},
        )

    @app.exception_handler(DatabaseException)
    async def database_exception_handler(request: Request, exc: DatabaseException):
        """Handle database exceptions."""
        logger.error(
            f"Database error for {request.method} {request.url.path}: {exc.detail}"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "type": "database_error"},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle HTTP exceptions."""
        logger.warning(
            f"HTTP error {exc.status_code} for {request.method} {request.url.path}"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle request validation errors."""
        logger.warning(
            f"Validation error for {request.method} {request.url.path}: {exc.errors()}"
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Request validation failed",
                "errors": exc.errors(),
                "type": "validation_error",
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle all unhandled exceptions."""
        logger.error(
            f"Unhandled exception for {request.method} {request.url.path}: {exc}",
            exc_info=True,
        )

        if settings.ENVIRONMENT == "development":
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": str(exc),
                    "type": "internal_error",
                },
            )
        else:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "Internal server error",
                    "type": "internal_error",
                },
            )

    # Root route
    @app.get("/", tags=["Health"])
    async def root():
        """Root endpoint."""
        return {
            "status": "ok",
            "version": settings.PROJECT_VERSION,
            "name": settings.PROJECT_NAME,
        }

    # Health check endpoint
    @app.get("/health", tags=["Health"])
    async def health_check(db=None, redis_client=None):
        """
        Health check endpoint.

        Returns status of the application and all dependencies.
        """
        health_status = {
            "status": "ok",
            "version": settings.PROJECT_VERSION,
            "checks": {},
        }

        # Database health check
        try:
            from app.deps import _engine as db_engine

            if db_engine:
                with db_engine.connect() as conn:
                    conn.execute(__import__("sqlalchemy").text("SELECT 1"))
                health_status["checks"]["database"] = "ok"
            else:
                health_status["checks"]["database"] = "not_initialized"
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            health_status["checks"]["database"] = "error"
            health_status["status"] = "degraded"

        # Redis health check
        try:
            from app.deps import _redis_pool

            if _redis_pool:
                _redis_pool.ping()
                health_status["checks"]["redis"] = "ok"
            else:
                health_status["checks"]["redis"] = "not_initialized"
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            health_status["checks"]["redis"] = "error"
            health_status["status"] = "degraded"

        # Firebase health check
        try:
            from app.deps import _firebase_app

            if _firebase_app:
                health_status["checks"]["firebase"] = "ok"
            else:
                health_status["checks"]["firebase"] = "not_initialized"
        except Exception as e:
            logger.error(f"Firebase health check failed: {e}")
            health_status["checks"]["firebase"] = "error"

        return health_status

    # Health and operations routers
    logger.info("Registering health check routers...")

    try:
        from app.routers import health

        app.include_router(health.router)
        logger.info("Health check routers registered successfully")
    except ImportError as e:
        logger.warning(f"Health router not yet implemented: {e}")

    # API v1 routers (empty stubs, will be filled later)
    logger.info("Registering API v1 routers...")

    try:
        from app.routers import (
            deals,
            notifications,
            predictions,
            price_reports,
            products,
            receipts,
            stores,
            users,
            watchlist,
        )

        app.include_router(
            products.router, prefix=f"{settings.API_V1_STR}/products", tags=["Products"]
        )
        app.include_router(
            stores.router, prefix=f"{settings.API_V1_STR}/stores", tags=["Stores"]
        )
        app.include_router(
            price_reports.router,
            prefix=f"{settings.API_V1_STR}/price-reports",
            tags=["Price Reports"],
        )
        app.include_router(
            users.router, prefix=f"{settings.API_V1_STR}/users", tags=["Users"]
        )
        app.include_router(
            watchlist.router,
            prefix=f"{settings.API_V1_STR}/watchlist",
            tags=["Watchlist"],
        )
        app.include_router(
            notifications.router,
            prefix=f"{settings.API_V1_STR}/notifications",
            tags=["Notifications"],
        )
        app.include_router(
            predictions.router,
            prefix=f"{settings.API_V1_STR}/predictions",
            tags=["Predictions"],
        )
        app.include_router(
            deals.router, prefix=f"{settings.API_V1_STR}/deals", tags=["Deals"]
        )

        app.include_router(
            receipts.router,
            prefix=f"{settings.API_V1_STR}/receipts",
            tags=["Receipts"],
        )

        from app.routers import admin
        app.include_router(
            admin.router,
            prefix=f"{settings.API_V1_STR}/admin",
            tags=["Admin"],
        )

        logger.info("All API v1 routers registered successfully")
    except ImportError as e:
        logger.warning(f"Some routers not yet implemented: {e}")

    # When the local object-storage backend is active, serve stored media
    # (product images, etc.) via StaticFiles so the app can reference them by
    # URL exactly as it would a CDN. Cloud backends (r2/s3) serve their own.
    if settings.OBJECT_STORAGE_BACKEND.lower() == "local":
        try:
            import os

            from fastapi.staticfiles import StaticFiles

            os.makedirs(settings.OBJECT_STORAGE_LOCAL_ROOT, exist_ok=True)
            app.mount(
                settings.OBJECT_STORAGE_LOCAL_BASE_URL,
                StaticFiles(directory=settings.OBJECT_STORAGE_LOCAL_ROOT),
                name="media",
            )
            logger.info(
                "Mounted local media at %s -> %s",
                settings.OBJECT_STORAGE_LOCAL_BASE_URL,
                settings.OBJECT_STORAGE_LOCAL_ROOT,
            )
        except Exception as e:
            logger.warning(f"Failed to mount local media StaticFiles: {e}")

    logger.info("FastAPI application created successfully")

    return app


# Create the FastAPI app instance
app = create_app()

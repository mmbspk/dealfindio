"""
Application configuration using Pydantic BaseSettings.

This module defines all environment variables and configuration settings for the
dealfindio backend API. Supports separate development and production configurations.
"""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Supports .env files for local development and environment variables for production.
    """

    # Environment
    ENVIRONMENT: str = Field(
        default="development",
        description="Application environment: development, staging, or production",
    )
    DEBUG: bool = Field(default=True, description="Enable debug mode")
    SECRET_KEY: str = Field(
        default="dev-secret-key-change-in-production",
        description="Secret key for session management and security",
    )

    # API Configuration
    API_V1_STR: str = Field(default="/api/v1", description="API v1 endpoint prefix")
    PROJECT_NAME: str = Field(default="dealfindio", description="Project name")
    PROJECT_TITLE: str = Field(default="dealfindio API", description="API title for OpenAPI docs")
    PROJECT_DESCRIPTION: str = Field(
        default="Australian grocery deal finder", description="API description for OpenAPI"
    )
    PROJECT_VERSION: str = Field(default="0.1.0", description="API version")

    # Database Configuration
    DATABASE_URL: str = Field(
        default="postgresql://user:password@localhost:5432/dealfindio",
        description="PostgreSQL database connection URL",
    )
    DATABASE_POOL_SIZE: int = Field(default=20, description="SQLAlchemy connection pool size")
    DATABASE_POOL_PRE_PING: bool = Field(
        default=True, description="Enable connection pool pre-ping (verify connection before use)"
    )
    DATABASE_ECHO: bool = Field(default=False, description="Enable SQLAlchemy SQL query logging")

    # Redis Configuration
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for caching and session management",
    )
    REDIS_SOCKET_CONNECT_TIMEOUT: int = Field(
        default=5, description="Redis socket connection timeout in seconds"
    )

    # Firebase Configuration
    FIREBASE_PROJECT_ID: str = Field(
        default="", description="Firebase project ID for authentication"
    )
    FIREBASE_SERVICE_ACCOUNT_JSON: Optional[str] = Field(
        default=None,
        description="Path to Firebase service account JSON file (optional, for admin SDK)",
    )

    # Anthropic API Configuration
    ANTHROPIC_API_KEY: str = Field(
        default="", description="Anthropic API key for Claude AI integration"
    )
    ANTHROPIC_BASE_URL: str = Field(
        default="",
        description="Optional base URL for the Anthropic API (e.g. a corporate proxy). "
        "Empty = use the SDK default (https://api.anthropic.com).",
    )

    # Logging Configuration
    LOG_LEVEL: str = Field(
        default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL"
    )
    LOG_FORMAT: str = Field(
        default="json", description="Log format: json (production) or text (development)"
    )

    # CORS Configuration
    # Stored as a raw string (comma-separated) so pydantic-settings' dotenv
    # source doesn't attempt JSON decoding. Use the `cors_origins` property to
    # get the parsed list.
    ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3000,http://localhost:8010",
        description="Comma-separated allowed origins for CORS",
    )

    @property
    def cors_origins(self) -> list[str]:
        """Parse ALLOWED_ORIGINS (comma-separated or JSON array) into a list."""
        raw = self.ALLOWED_ORIGINS.strip()
        if not raw:
            return []
        if raw.startswith("["):
            import json

            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(o).strip() for o in parsed]
            except json.JSONDecodeError:
                pass
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    # External APIs Configuration
    OPEN_FOOD_FACTS_USER_AGENT: str = Field(
        default="dealfindio/0.1.0",
        description="User agent for Open Food Facts API requests",
    )

    # ────────────────────────────────────────────────────────────────────
    # Object storage (Cloudflare R2 / S3 / local) — shared by product
    # images, receipts, and catalog assets. R2 is S3-compatible: same boto3
    # client, custom endpoint URL, and $0 egress. `local` writes to disk and
    # serves via FastAPI StaticFiles (dev fallback, no cloud creds needed).
    # ────────────────────────────────────────────────────────────────────
    OBJECT_STORAGE_BACKEND: str = Field(
        default="local",
        description="Object storage backend: 'r2', 's3', or 'local'",
    )
    # R2 / S3 credentials (used when backend is 'r2' or 's3')
    R2_ACCOUNT_ID: Optional[str] = Field(
        default=None, description="Cloudflare R2 account ID (for endpoint URL construction)"
    )
    R2_BUCKET: str = Field(default="dealfindio-media", description="Object storage bucket name")
    R2_ACCESS_KEY_ID: Optional[str] = Field(default=None, description="R2/S3 access key ID")
    R2_SECRET_ACCESS_KEY: Optional[str] = Field(default=None, description="R2/S3 secret access key")
    R2_ENDPOINT_URL: Optional[str] = Field(
        default=None,
        description="S3-compatible endpoint URL (e.g. https://<acct>.r2.cloudflarestorage.com)",
    )
    R2_REGION: str = Field(
        default="auto", description="Region ('auto' for R2, e.g. 'ap-southeast-2' for AWS S3)"
    )
    R2_PUBLIC_BASE_URL: Optional[str] = Field(
        default=None,
        description="Public CDN base URL fronting the bucket (custom domain or r2.dev)",
    )
    # Local backend (dev fallback)
    OBJECT_STORAGE_LOCAL_ROOT: str = Field(
        default="/tmp/dealfindio-media",
        description="Filesystem root for the local storage backend",
    )
    OBJECT_STORAGE_LOCAL_BASE_URL: str = Field(
        default="/media",
        description="URL prefix the local backend is served under (FastAPI StaticFiles)",
    )

    # ────────────────────────────────────────────────────────────────────
    # Open Food Facts image enrichment (Feature A)
    # ────────────────────────────────────────────────────────────────────
    OFT_ENRICH_ENABLED: bool = Field(
        default=True, description="Enable post-scrape OFT image enrichment"
    )
    OFT_SEARCH_MIN_DELAY_S: float = Field(
        default=2.0, description="Minimum delay between OFT requests (human-like jitter)"
    )
    OFT_SEARCH_MAX_DELAY_S: float = Field(
        default=5.0, description="Maximum delay between OFT requests"
    )
    OFT_SEARCH_TIMEOUT_S: float = Field(
        default=10.0, description="HTTP timeout for OFT requests in seconds"
    )
    OFT_ENRICH_LIMIT_PER_RUN: int = Field(
        default=500, description="Max products to enrich per task run"
    )

    # ────────────────────────────────────────────────────────────────────
    # AI-powered cross-store dedup judge (Claude)
    # A judge layered on top of the fuzzy pre-filter: it decides whether two
    # cross-chain products are the same item and which record to keep. Flags
    # default OFF so imports/tests never trigger a paid API call.
    # ────────────────────────────────────────────────────────────────────
    AI_DEDUP_ENABLED: bool = Field(
        default=False, description="Enable the Claude dedup judge (paid API calls)"
    )
    AI_DEDUP_MODEL: str = Field(
        default="claude-sonnet-4-6",
        description="Claude model for the dedup judge",
    )
    AI_DEDUP_CONFIDENCE_MIN: float = Field(
        default=0.80,
        description="Minimum AI confidence to auto-merge; below this stays pending",
    )
    AI_DEDUP_MAX_TOKENS: int = Field(
        default=512, description="Max output tokens for the compact JSON verdict"
    )

    # ────────────────────────────────────────────────────────────────────
    # Open Food Facts GTIN recovery (name+brand → real barcode)
    # Recovers only real, checksum-valid EAN-13 codes OFF actually serves —
    # never fabricates. Reuses the OFT search primitives + rate limiter.
    # ────────────────────────────────────────────────────────────────────
    OFF_GTIN_BACKFILL_ENABLED: bool = Field(
        default=False, description="Enable OFF name+brand → GTIN recovery backfill"
    )
    OFF_GTIN_BACKFILL_LIMIT_PER_RUN: int = Field(
        default=200, description="Max products to attempt GTIN recovery for per run"
    )

    # ────────────────────────────────────────────────────────────────────
    # Authenticated full-catalog scraper (Feature B)
    # ────────────────────────────────────────────────────────────────────
    # Per-store credentials — kept in .env ONLY, never persisted to Redis/DB.
    # Optional: catalog browse may be anonymous; creds are for endpoints that
    # need them (personalized pricing, etc.).
    COLES_EMAIL: Optional[str] = Field(default=None, description="Coles account email")
    COLES_PASSWORD: Optional[str] = Field(default=None, description="Coles account password")
    WOOLWORTHS_EMAIL: Optional[str] = Field(default=None, description="Woolworths account email")
    WOOLWORTHS_PASSWORD: Optional[str] = Field(
        default=None, description="Woolworths account password"
    )
    ALDI_EMAIL: Optional[str] = Field(default=None, description="ALDI account email")
    ALDI_PASSWORD: Optional[str] = Field(default=None, description="ALDI account password")
    IGA_EMAIL: Optional[str] = Field(default=None, description="IGA account email")
    IGA_PASSWORD: Optional[str] = Field(default=None, description="IGA account password")
    DAN_MURPHYS_EMAIL: Optional[str] = Field(default=None, description="Dan Murphy's account email")
    DAN_MURPHYS_PASSWORD: Optional[str] = Field(
        default=None, description="Dan Murphy's account password"
    )

    # Human-like pacing defaults (shared across catalog scrapers).
    # These are tuned for Coles' Imperva bot-protection — Imperva tracks request
    # velocity over a ~60s window and flags sustained sub-5s rates. Wider jitter
    # + smaller burst = more random, harder to distinguish from a human browser.
    CATALOG_MIN_DELAY_S: float = Field(
        default=5.0, description="Minimum delay between catalog requests"
    )
    CATALOG_MAX_DELAY_S: float = Field(
        default=12.0, description="Maximum delay between catalog requests"
    )
    CATALOG_BURST_LIMIT: int = Field(
        default=3, description="Requests allowed back-to-back before pacing kicks in"
    )
    CATALOG_PAGE_SIZE: int = Field(
        default=48, description="Products requested per catalog API page"
    )
    CATALOG_MAX_PAGES_PER_CATEGORY: int = Field(
        default=200, description="Safety cap on pages fetched per category"
    )

    # Per-store enable flags (full crawl is on-demand regardless; these gate
    # whether a store participates in the all-chains sweep + delta schedule).
    CATALOG_COLES_ENABLED: bool = Field(default=False)
    CATALOG_WOOLWORTHS_ENABLED: bool = Field(default=False)
    CATALOG_ALDI_ENABLED: bool = Field(default=False)
    CATALOG_IGA_ENABLED: bool = Field(default=False)
    CATALOG_DAN_MURPHYS_ENABLED: bool = Field(default=False)
    CATALOG_DRAKES_ENABLED: bool = Field(default=False)

    # Per-store DELTA cron (empty string = no scheduled delta; on-demand only).
    # Full crawls are never scheduled — always triggered from the admin panel.
    CATALOG_COLES_CRON: str = Field(default="")
    CATALOG_WOOLWORTHS_CRON: str = Field(default="")
    CATALOG_ALDI_CRON: str = Field(default="")
    CATALOG_IGA_CRON: str = Field(default="")
    CATALOG_DAN_MURPHYS_CRON: str = Field(default="")
    CATALOG_DRAKES_CRON: str = Field(default="")

    # ────────────────────────────────────────────────────────────────────
    # Deals feed
    # ────────────────────────────────────────────────────────────────────
    DEALS_FEED_DAYS: int = Field(
        default=7,
        description="How many days back to include on-sale price reports in the deals feed",
    )

    # Monitoring & Error Tracking
    SENTRY_DSN: Optional[str] = Field(
        default=None, description="Sentry DSN for error tracking (optional)"
    )
    SLACK_WEBHOOK_URL: Optional[str] = Field(
        default=None, description="Slack webhook URL for alerts (optional)"
    )

    # ── Scraper proxy (Coles & Woolworths) ────────────────────────────────────
    # Set SCRAPER_PROXY_URL to route Coles and Woolworths requests through a
    # residential proxy. Required when running on a cloud server (AWS/GCP/Azure)
    # because datacenter IPs are blocked by Imperva / Cloudflare.
    #
    # Format: http://user:pass@proxy-host:port
    #         socks5://user:pass@proxy-host:port
    #
    # Providers (residential proxies):
    #   Brightdata:  http://USERNAME:PASSWORD@brd.superproxy.io:22225
    #   Oxylabs:     http://USERNAME_country-AU:PASSWORD@pr.oxylabs.io:7777
    #   Smartproxy:  http://user:pass@gate.smartproxy.com:7000
    #   ScraperAPI:  http://scraperapi:YOUR_API_KEY@proxy-server.scraperapi.com:8001
    #
    # Leave blank (default) to connect directly — fine on a local/residential
    # machine, will likely be blocked on a cloud VM.
    SCRAPER_PROXY_URL: Optional[str] = Field(
        default=None,
        description=(
            "HTTP/SOCKS5 proxy URL for Coles and Woolworths scrapers "
            "(format: http://user:pass@host:port). Leave blank for direct connections."
        ),
    )

    # Admin config store — Fernet key for encrypting API keys at rest.
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    CONFIG_ENCRYPTION_KEY: str = Field(
        default="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        description="Fernet encryption key for admin config store (change in production!)",
    )

    # Email notifications via Resend (https://resend.com)
    # Leave RESEND_API_KEY empty to disable email sending entirely.
    RESEND_API_KEY: str = Field(default="", description="Resend API key for transactional email")
    EMAIL_FROM_ADDRESS: str = Field(
        default="alerts@dealfindio.com.au", description="From address for notification emails"
    )
    EMAIL_FROM_NAME: str = Field(
        default="DealFindio Alerts", description="From name for notification emails"
    )
    APP_BASE_URL: str = Field(
        default="http://localhost:5173", description="Public app base URL (used in email links)"
    )

    # OpenAPI Configuration
    OPENAPI_URL: str = Field(default="/openapi.json", description="OpenAPI schema URL")
    OPENAPI_DOCS_URL: str = Field(default="/docs", description="Swagger UI docs URL")
    OPENAPI_REDOC_URL: str = Field(default="/redoc", description="ReDoc docs URL")

    # Request Configuration
    REQUEST_TIMEOUT: int = Field(default=30, description="HTTP request timeout in seconds")

    # Celery Configuration
    CELERY_BROKER_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Celery broker URL (Redis)",
    )
    CELERY_RESULT_BACKEND: str = Field(
        default="redis://localhost:6379/1",
        description="Celery result backend URL (Redis)",
    )
    CELERY_TASK_SERIALIZER: str = Field(
        default="json",
        description="Task serialization format",
    )
    CELERY_RESULT_SERIALIZER: str = Field(
        default="json",
        description="Result serialization format",
    )
    CELERY_ACCEPT_CONTENT: list = Field(
        default=["json"],
        description="Accepted content types for tasks",
    )
    CELERY_TIMEZONE: str = Field(
        default="UTC",
        description="Timezone for Celery and Beat scheduler",
    )
    CELERY_ENABLE_UTC: bool = Field(
        default=True,
        description="Enable UTC for Celery",
    )
    CELERY_TASK_TRACK_STARTED: bool = Field(
        default=True,
        description="Track task start state",
    )
    CELERY_TASK_TIME_LIMIT: int = Field(
        default=30 * 60,
        description="Hard time limit for tasks in seconds (30 minutes default)",
    )
    CELERY_TASK_SOFT_TIME_LIMIT: int = Field(
        default=25 * 60,
        description="Soft time limit for tasks in seconds (25 minutes default)",
    )
    CELERY_TASK_MAX_RETRIES: int = Field(
        default=3,
        description="Maximum retries for failed tasks",
    )

    class Config:
        """Pydantic config."""

        # Absolute path to backend/.env so settings load regardless of the
        # current working directory (e.g. when Alembic runs from db/migrations).
        env_file = str(Path(__file__).resolve().parent.parent / ".env")
        env_file_encoding = "utf-8"
        case_sensitive = True


def get_settings() -> Settings:
    """
    Get application settings.

    Returns:
        Settings: Loaded settings from environment variables.
    """
    return Settings()

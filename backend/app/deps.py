"""
Dependency injection module for FastAPI.

Provides database connections, Redis connections, Firebase JWT verification,
and user authentication with proper error handling.
"""

import logging
from typing import Generator, Optional

import firebase_admin
import httpx
from fastapi import Depends, Header, HTTPException, status
from firebase_admin import credentials
from jose import JWTError, jwt
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


# Database setup
_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None
_AsyncSessionLocal: Optional[async_sessionmaker] = None
_redis_pool: Optional[Redis] = None
_firebase_app: Optional[firebase_admin.App] = None
_firebase_public_keys: dict = {}


class InvalidTokenException(HTTPException):
    """Raised when JWT token is invalid or expired."""

    def __init__(self, detail: str = "Invalid authentication credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class UserNotFoundException(HTTPException):
    """Raised when user is not found in database."""

    def __init__(self, detail: str = "User not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )


class DatabaseException(HTTPException):
    """Raised when database connection fails."""

    def __init__(self, detail: str = "Database connection error"):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        )


def init_database(database_url: str, pool_size: int, echo: bool):
    """
    Initialize database connection pool.

    Args:
        database_url: PostgreSQL database URL
        pool_size: Connection pool size
        echo: Enable SQL query logging

    Raises:
        DatabaseException: If connection fails
    """
    global _engine, _SessionLocal, _AsyncSessionLocal

    try:
        # psycopg2 (Postgres) expects "connect_timeout"; SQLite expects
        # "timeout". Pick the right key based on the URL so the same code path
        # works for the app (Postgres) and any SQLite-based tooling.
        if database_url.startswith("sqlite"):
            connect_args = {"timeout": 10}
        else:
            connect_args = {"connect_timeout": 10}

        _engine = create_engine(
            database_url,
            poolclass=None,  # Use default QueuePool
            pool_size=pool_size,
            max_overflow=10,
            pool_pre_ping=True,
            echo=echo,
            connect_args=connect_args,
        )

        _SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=_engine,
        )

        # Async engine for services that use AsyncSession (e.g. product_dedup)
        async_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if not database_url.startswith("sqlite"):
            try:
                from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
                from sqlalchemy.ext.asyncio import create_async_engine as _cae

                _async_engine = _cae(
                    async_url,
                    pool_size=pool_size,
                    max_overflow=10,
                    pool_pre_ping=True,
                    echo=echo,
                )
                _AsyncSessionLocal = _asm(
                    _async_engine,
                    class_=AsyncSession,
                    expire_on_commit=False,
                )
            except Exception as ae:
                logger.warning(f"Async engine setup failed (asyncpg not installed?): {ae}")
                _AsyncSessionLocal = None

        # Test connection
        with _engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))

        logger.info("Database connection pool initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise DatabaseException(f"Database initialization failed: {str(e)}")


def init_redis(redis_url: str, socket_timeout: int):
    """
    Initialize Redis connection pool.

    Args:
        redis_url: Redis connection URL
        socket_timeout: Socket connection timeout in seconds

    Raises:
        DatabaseException: If connection fails
    """
    global _redis_pool

    try:
        _redis_pool = Redis.from_url(
            redis_url,
            socket_connect_timeout=socket_timeout,
            socket_keepalive=True,
            socket_keepalive_options={
                1: 1,  # TCP_KEEPIDLE
                2: 1,  # TCP_KEEPINTVL
                3: 3,  # TCP_KEEPCNT
            }
            if hasattr(Redis, "socket_keepalive_options")
            else None,
            decode_responses=True,
        )

        # Test connection
        _redis_pool.ping()
        logger.info("Redis connection pool initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Redis: {e}")
        raise DatabaseException(f"Redis initialization failed: {str(e)}")


def init_firebase(project_id: str, service_account_json: Optional[str] = None):
    """
    Initialize Firebase Admin SDK.

    Args:
        project_id: Firebase project ID
        service_account_json: Path to service account JSON file (optional)

    Raises:
        DatabaseException: If initialization fails
    """
    global _firebase_app

    try:
        if firebase_admin._apps:
            _firebase_app = firebase_admin._apps[0]
            logger.info("Using existing Firebase app instance")
        else:
            if service_account_json:
                cred = credentials.Certificate(service_account_json)
                _firebase_app = firebase_admin.initialize_app(cred)
            else:
                _firebase_app = firebase_admin.initialize_app()

        logger.info(f"Firebase initialized for project: {project_id}")
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {e}")
        raise DatabaseException(f"Firebase initialization failed: {str(e)}")


async def fetch_firebase_public_keys() -> dict:
    """
    Fetch Firebase public keys for JWT verification.

    Returns:
        dict: Mapping of key ID to public key

    Raises:
        InvalidTokenException: If keys cannot be fetched
    """
    global _firebase_public_keys

    if _firebase_public_keys:
        return _firebase_public_keys

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com",
                timeout=10,
            )
            response.raise_for_status()
            _firebase_public_keys = response.json()
            return _firebase_public_keys
    except Exception as e:
        logger.error(f"Failed to fetch Firebase public keys: {e}")
        raise InvalidTokenException("Unable to verify authentication token")


def get_db() -> Generator[Session, None, None]:
    """
    Get database session dependency.

    Yields:
        Session: SQLAlchemy session

    Raises:
        DatabaseException: If session creation fails
    """
    if not _SessionLocal:
        raise DatabaseException("Database not initialized")

    db = _SessionLocal()
    try:
        yield db
    except Exception as e:
        logger.error(f"Database session error: {e}")
        db.rollback()
        raise DatabaseException("Database session error")
    finally:
        db.close()


def get_redis() -> Generator[Redis, None, None]:
    """
    Get Redis connection dependency.

    Yields:
        Redis: Redis connection

    Raises:
        DatabaseException: If Redis connection fails
    """
    if not _redis_pool:
        raise DatabaseException("Redis not initialized")

    try:
        yield _redis_pool
    except Exception as e:
        logger.error(f"Redis connection error: {e}")
        raise DatabaseException("Redis connection error")


async def verify_firebase_token(authorization: str = Header(None)) -> dict:
    """
    Verify Firebase JWT token from Authorization header.

    Extracts the Bearer token from the Authorization header, verifies its
    signature using Firebase public keys, validates timestamps, and extracts
    the firebase_uid and email claims.

    Args:
        authorization: Authorization header value (e.g., "Bearer <token>")

    Returns:
        dict: Token claims containing firebase_uid and email

    Raises:
        InvalidTokenException: If token is missing, invalid, or expired
    """
    if not authorization:
        raise InvalidTokenException("Missing authorization header")

    # Extract Bearer token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise InvalidTokenException("Invalid authorization header format")

    token = parts[1]

    try:
        # Decode without verification first to get header
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        if not kid:
            raise InvalidTokenException("Token missing key ID (kid)")

        # Fetch public keys
        public_keys = await fetch_firebase_public_keys()

        if kid not in public_keys:
            raise InvalidTokenException("Token key not found in Firebase public keys")

        # Verify token signature
        settings = get_settings()
        claims = jwt.decode(
            token,
            public_keys[kid],
            algorithms=["RS256"],
            audience=settings.FIREBASE_PROJECT_ID,
            options={"verify_aud": True},
        )

        # Extract firebase_uid and email from claims
        # Firebase ID tokens store the user UID in the "sub" claim, not "uid"
        firebase_uid = claims.get("sub") or claims.get("user_id")
        email = claims.get("email")

        if not firebase_uid:
            raise InvalidTokenException("Token missing user ID")

        return {
            "firebase_uid": firebase_uid,
            "email": email,
            "claims": claims,
        }

    except JWTError as e:
        logger.warning(f"JWT verification failed: {e}")
        raise InvalidTokenException(f"Invalid token: {str(e)}")
    except Exception as e:
        logger.error(f"Token verification error: {e}")
        raise InvalidTokenException(f"Token verification failed: {str(e)}")


async def get_current_user(
    token_data: dict = Depends(verify_firebase_token),
    db: Session = Depends(get_db),
) -> "User":
    """
    Get current authenticated user from database.

    Verifies the Firebase token and retrieves the corresponding user
    from the database. If user doesn't exist, raises UserNotFoundException.

    Args:
        token_data: Verified Firebase token data
        db: Database session

    Returns:
        User: User DTO with authentication context

    Raises:
        UserNotFoundException: If user doesn't exist in database
    """
    firebase_uid = token_data.get("firebase_uid")

    try:
        # Import models here to avoid circular imports
        from app.models.user import User, UserProfile

        # Query UserProfile by firebase_uid
        user_profile = (
            db.query(UserProfile).filter(UserProfile.firebase_uid == firebase_uid).first()
        )

        if not user_profile:
            # First login: auto-create a UserProfile row from Firebase claims
            email = token_data.get("email") or ""
            display_name = email.split("@")[0] if email else firebase_uid[:8]
            user_profile = UserProfile(
                firebase_uid=firebase_uid,
                email=email,
                display_name=display_name,
            )
            db.add(user_profile)
            db.commit()
            db.refresh(user_profile)
            logger.info("Auto-created UserProfile for firebase_uid=%s", firebase_uid)

        # Create User DTO from UserProfile
        user = User(
            firebase_uid=user_profile.firebase_uid,
            email=user_profile.email or token_data.get("email"),
            user_profile=user_profile,
        )

        return user

    except Exception as e:
        if isinstance(e, UserNotFoundException):
            raise
        logger.error(f"Error retrieving user: {e}")
        raise UserNotFoundException("Failed to retrieve user information")


async def require_admin(current_user: "User" = Depends(get_current_user)) -> "User":
    """Raises 403 if the authenticated user does not have is_admin=True."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def verify_token_param(
    authorization: str = Header(None),
    token: str = None,
) -> dict:
    """
    Like verify_firebase_token but also accepts ?token= query param.
    Used for SSE endpoints where EventSource cannot set Authorization headers.
    """
    auth_header = authorization or (f"Bearer {token}" if token else None)
    if not auth_header:
        from app.deps import InvalidTokenException

        raise InvalidTokenException("Missing authorization header or token param")
    return await verify_firebase_token(auth_header)


async def require_admin_sse(
    token_data: dict = Depends(verify_token_param),
    db: Session = Depends(get_db),
) -> "User":
    """Admin guard for SSE endpoints that accept ?token= as a fallback."""
    from app.models.user import User, UserProfile

    firebase_uid = token_data.get("firebase_uid")
    user_profile = db.query(UserProfile).filter(UserProfile.firebase_uid == firebase_uid).first()
    if not user_profile:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    user = User(
        firebase_uid=user_profile.firebase_uid,
        email=user_profile.email or token_data.get("email"),
        user_profile=user_profile,
    )
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def close_connections():
    """Close all open connections (database, Redis, Firebase)."""
    global _engine, _redis_pool, _firebase_app

    if _engine:
        _engine.dispose()
        logger.info("Database connection pool closed")

    if _redis_pool:
        _redis_pool.close()
        logger.info("Redis connection closed")

    if _firebase_app:
        firebase_admin.delete_app(_firebase_app)
        logger.info("Firebase app uninitialized")


# Cached Anthropic client (lazily constructed).
_anthropic_client = None


def get_anthropic_client():
    """
    Provide a shared Anthropic client for Claude vision/text calls.

    Lazily constructs a singleton client from ``ANTHROPIC_API_KEY``. Used as a
    FastAPI dependency (e.g. by the receipts router for OCR parsing).

    Returns:
        anthropic.Anthropic: Configured Anthropic SDK client.

    Raises:
        RuntimeError: If ANTHROPIC_API_KEY is not configured.
    """
    global _anthropic_client

    if _anthropic_client is None:
        settings = get_settings()
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not configured; receipt parsing is unavailable"
            )
        from anthropic import Anthropic

        # Resolve the proxy base URL DB-first (admin Configs UI) then env. Like
        # ANTHROPIC_API_KEY, this is read at client-construction time — the
        # client is a cached singleton, so a base-URL change made in the admin UI
        # takes effect on the next backend start.
        base_url = settings.ANTHROPIC_BASE_URL
        if _SessionLocal is not None:
            try:
                from app.services.config_store import get_runtime_value

                with _SessionLocal() as session:
                    base_url = get_runtime_value("ANTHROPIC_BASE_URL", session) or base_url
            except Exception as exc:  # noqa: BLE001 — never block client init on config lookup
                logger.warning("Could not read ANTHROPIC_BASE_URL override: %s", exc)

        client_kwargs = {"api_key": settings.ANTHROPIC_API_KEY}
        if base_url:
            # Route through a proxy / gateway when configured.
            client_kwargs["base_url"] = base_url
        _anthropic_client = Anthropic(**client_kwargs)
        logger.info(
            "Anthropic client initialized%s",
            f" (base_url={base_url})" if base_url else "",
        )

    return _anthropic_client

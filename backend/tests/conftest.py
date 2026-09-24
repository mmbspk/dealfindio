"""
Pytest configuration and fixtures for dealfindio test suite.

Provides fixtures for:
- Database setup/teardown with test database
- FastAPI test client
- Sample data (products, stores, price reports)
- Redis mock/test instance
- Firebase mock authentication
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Generator
from uuid import uuid4

import pytest
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, pool
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.main import app
from app.models import Base, Product, Store
from app.models.base import PlainBase

logger = logging.getLogger(__name__)


def pytest_configure(config):
    """Pytest configuration hook."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests requiring database",
    )
    config.addinivalue_line(
        "markers",
        "unit: marks tests as unit tests (no database required)",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow running",
    )


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Get test settings."""
    settings = get_settings()
    # Override for testing
    settings.ENVIRONMENT = "testing"
    settings.DATABASE_URL = "sqlite:///:memory:"
    settings.REDIS_URL = "redis://localhost:6380/0"
    return settings


@pytest.fixture(scope="function")
def test_db_engine():
    """
    Test database engine.

    Defaults to SQLite in-memory for fast, isolated tests. If the
    ``TEST_DATABASE_URL`` env var is set (e.g. a Postgres+PostGIS DB), tests run
    against that instead — exercising Postgres-only behavior (PostGIS,
    psycopg2 DSN, trigram search) that SQLite can't. The Postgres lane builds
    the schema from the ORM models to match the app.
    """
    import os

    pg_url = os.environ.get("TEST_DATABASE_URL")

    if pg_url:
        engine = create_engine(pg_url, echo=False)
        # Ensure a clean schema, then build from the ORM models.
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            conn.exec_driver_sql("CREATE SCHEMA public")
            for ext in ("postgis", "pg_trgm", '"uuid-ossp"', "cube", "earthdistance", "vector"):
                conn.exec_driver_sql(f"CREATE EXTENSION IF NOT EXISTS {ext}")
        Base.metadata.create_all(bind=engine)
        PlainBase.metadata.create_all(bind=engine)
        yield engine
        Base.metadata.drop_all(bind=engine)
        PlainBase.metadata.drop_all(bind=engine)
        engine.dispose()
        return

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=pool.StaticPool,
        echo=False,
    )

    # SQLite disables foreign-key enforcement by default. Turn it on so the
    # test DB rejects invalid FKs the way Postgres does in production.
    @event.listens_for(engine, "connect")
    def _enable_sqlite_fks(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Create all tables
    Base.metadata.create_all(bind=engine)
    PlainBase.metadata.create_all(bind=engine)

    yield engine

    # Cleanup
    Base.metadata.drop_all(bind=engine)
    PlainBase.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def test_db_session(test_db_engine) -> Generator[Session, None, None]:
    """Create test database session."""
    connection = test_db_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def test_client(test_db_session) -> TestClient:
    """Create FastAPI test client with DB + Redis dependency overrides."""

    def override_get_db():
        return test_db_session

    # A fake Redis so endpoints that use rate-limiting / dedup work in tests
    # instead of 500-ing with "Redis not initialized".
    fake = FakeStrictRedis(decode_responses=True)

    def override_get_redis():
        return fake

    from app.deps import get_db, get_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    client = TestClient(app)
    yield client

    # Cleanup
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def fake_redis():
    """Create fake Redis instance for testing."""
    return FakeStrictRedis(decode_responses=True)


@pytest.fixture(scope="function")
def authed_user(test_db_session: Session):
    """Persist a UserProfile representing the authenticated test user."""
    from app.models import UserProfile

    profile = UserProfile(
        id=uuid4(),
        firebase_uid="authed_test_uid",
        email="authed@example.com",
        fcm_token="test-fcm-token",
        reputation_score=2.0,
        contribution_count=5,
    )
    test_db_session.add(profile)
    test_db_session.commit()
    test_db_session.refresh(profile)
    return profile


@pytest.fixture(scope="function")
def authed_client(test_db_session, authed_user) -> TestClient:
    """
    TestClient authenticated as ``authed_user``.

    Overrides get_db, get_redis, and get_current_user so authenticated
    endpoints run their real logic against the test DB with a known user.
    """
    from app.deps import get_current_user, get_db, get_redis
    from app.models.user import User

    fake = FakeStrictRedis(decode_responses=True)

    def override_get_db():
        return test_db_session

    def override_get_redis():
        return fake

    def override_get_current_user():
        return User(
            firebase_uid=authed_user.firebase_uid,
            email=authed_user.email,
            user_profile=authed_user,
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)
    yield client

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def sample_product(test_db_session: Session) -> Product:
    """Create sample product for tests."""
    product = Product(
        id=uuid4(),
        gtin="9300617006552",
        name="Test Milk 2L",
        brand="Test Brand",
        category="dairy",
    )
    test_db_session.add(product)
    test_db_session.commit()
    test_db_session.refresh(product)
    return product


@pytest.fixture(scope="function")
def sample_store(test_db_session: Session) -> Store:
    """Create sample store for tests."""
    store = Store(
        id=uuid4(),
        chain="coles",
        name="Coles Chatswood",
        suburb="Chatswood",
        state="NSW",
        postcode="2067",
        lat=-33.87,
        lon=151.21,
        address="123 Victoria Ave",
    )
    test_db_session.add(store)
    test_db_session.commit()
    test_db_session.refresh(store)
    return store


@pytest.fixture(scope="function")
def sample_products(test_db_session: Session) -> list[Product]:
    """Create multiple sample products for tests."""
    products = [
        Product(
            id=uuid4(),
            gtin="9300617006552",
            name="Milk 2L",
            brand="Coles",
            category="dairy",
        ),
        Product(
            id=uuid4(),
            gtin="9300617006559",
            name="Bread Multigrain",
            brand="Bakers Delight",
            category="bakery",
        ),
        Product(
            id=uuid4(),
            gtin="9300617006566",
            name="Eggs Dozen",
            brand="Countryside",
            category="dairy",
        ),
    ]
    test_db_session.add_all(products)
    test_db_session.commit()
    for product in products:
        test_db_session.refresh(product)
    return products


@pytest.fixture(scope="function")
def sample_stores(test_db_session: Session) -> list[Store]:
    """Create multiple sample stores for tests."""
    stores = [
        Store(
            id=uuid4(),
            chain="coles",
            name="Coles Chatswood",
            suburb="Chatswood",
            state="NSW",
            postcode="2067",
            lat=-33.87,
            lon=151.21,
        ),
        Store(
            id=uuid4(),
            chain="woolworths",
            name="Woolworths Chatswood",
            suburb="Chatswood",
            state="NSW",
            postcode="2067",
            lat=-33.87,
            lon=151.21,
        ),
        Store(
            id=uuid4(),
            chain="aldi",
            name="ALDI Chatswood",
            suburb="Chatswood",
            state="NSW",
            postcode="2067",
            lat=-33.87,
            lon=151.21,
        ),
    ]
    test_db_session.add_all(stores)
    test_db_session.commit()
    for store in stores:
        test_db_session.refresh(store)
    return stores


@pytest.fixture
def mock_firebase_token():
    """Create mock Firebase token for testing."""
    return {
        "firebase_uid": "test_user_123",
        "email": "test@example.com",
        "aud": "test-project",
        "iat": 1000000,
        "exp": 2000000,
        "uid": "test_user_123",
    }


@pytest.fixture
def fixture_html_path() -> Path:
    """Get path to HTML fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_coles_html(fixture_html_path: Path) -> str:
    """Load sample Coles scraper HTML fixture."""
    file_path = fixture_html_path / "coles_specials.html"
    if file_path.exists():
        return file_path.read_text()
    # Return minimal mock HTML if fixture doesn't exist
    return """
    <html>
        <div class="product" data-product-id="123">
            <span class="product-name">Milk 2L</span>
            <span class="product-price">$3.50</span>
            <span class="product-brand">Coles</span>
        </div>
    </html>
    """


@pytest.fixture
def sample_woolworths_json(fixture_html_path: Path) -> dict:
    """Load sample Woolworths API JSON fixture."""
    file_path = fixture_html_path / "woolworths_api.json"
    if file_path.exists():
        return json.loads(file_path.read_text())
    # Return minimal mock JSON if fixture doesn't exist
    return {
        "Products": [
            {
                "ProductId": "456",
                "Name": "Milk 2L",
                "Brand": "Woolworths",
                "Price": 3.50,
                "Category": "Dairy",
            }
        ]
    }


@pytest.fixture(autouse=True)
def log_test_info(request):
    """Automatically log test info for debugging."""
    logger.info(f"Starting test: {request.node.name}")
    yield
    logger.info(f"Completed test: {request.node.name}")


@pytest.fixture
def caplog_at_level(caplog):
    """Fixture to capture logs at specific level."""

    def set_level(level):
        caplog.set_level(level)
        return caplog

    return set_level

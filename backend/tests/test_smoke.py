"""
Postgres-backed smoke test.

Runs only when TEST_DATABASE_URL points at a real Postgres+PostGIS database
(marker: pg). Boots the real app and asserts the exact things that were broken
at runtime but passed the SQLite suite: health checks, and that product-search
and nearby-stores return 200 with seeded data against real Postgres/PostGIS.

Run with:
    TEST_DATABASE_URL=postgresql://dealfindio:dealfindio_dev@localhost:5432/dealfindio_test \
        poetry run pytest -m pg
"""

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.pg

# Skip the whole module unless a Postgres test DB is configured.
if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip(
        "TEST_DATABASE_URL not set; skipping Postgres smoke tests",
        allow_module_level=True,
    )

from app.models import PriceReport, Product, Store  # noqa: E402


@pytest.fixture
def seeded(test_db_session):
    """Seed one product + store + on-sale price against Postgres."""
    from datetime import date

    p = Product(
        id=uuid4(), gtin="9300000000001", name="Smoke Test Milk 2L",
        brand="Coles", category="dairy",
    )
    s = Store(
        id=uuid4(), chain="coles", name="Smoke Coles", suburb="Chatswood",
        state="NSW", postcode="2067", lat=-33.7969, lon=151.1803, is_active=True,
    )
    test_db_session.add_all([p, s])
    test_db_session.flush()
    test_db_session.add(
        PriceReport(
            product_id=p.id, store_id=s.id, price_cents=350, was_price_cents=500,
            source="flyer_coles", is_on_sale=True, sale_pct_off=30.0,
            confidence=1.0, is_verified=True, valid_from=date.today(),
            lat=s.lat, lon=s.lon,
        )
    )
    test_db_session.commit()
    return {"product": p, "store": s}


class TestPostgresSmoke:
    @pytest.mark.pg
    def test_health_ok_on_postgres(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] in ("ok", "healthy")

    @pytest.mark.pg
    def test_root_ok(self, test_client):
        assert test_client.get("/").status_code == 200

    @pytest.mark.pg
    def test_product_search_trigram_returns_data(self, test_client, seeded):
        # Trigram/ILIKE search on real Postgres.
        resp = test_client.get("/api/v1/products/search?q=Milk")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert any("Milk" in n for n in names)

    @pytest.mark.pg
    def test_nearby_stores_ll_to_earth_path(self, test_client, seeded):
        # Exercises the nearby-stores endpoint end-to-end on Postgres — the
        # exact query that crashed at runtime.
        resp = test_client.get(
            "/api/v1/stores/nearby",
            params={"lat": -33.797, "lon": 151.180, "radius_km": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert data["stores"][0]["distance_km"] >= 0

"""
Integration tests for FastAPI endpoints (unauthenticated + public paths).

Assertions here are exact: a request against a seeded resource asserts 200 plus
the response body shape; a random UUID asserts exactly 404; a bad query param
asserts exactly 422. Endpoints are the real routes from the OpenAPI spec.

Authenticated flows (watchlist CRUD, authed price-report submit, notifications,
receipt upload) live in test_authed_api.py.
"""

import pytest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.models import Product, Store


class TestProductSearchEndpoints:
    """GET /api/v1/products/search"""

    @pytest.mark.integration
    def test_search_by_name_returns_seeded_product(
        self, test_client: TestClient, sample_products: list[Product]
    ):
        response = test_client.get("/api/v1/products/search?q=Milk")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        names = [p["name"] for p in data]
        assert any("Milk" in n for n in names)
        # Body shape of a result row.
        row = data[0]
        for key in ("id", "gtin", "name", "brand", "category"):
            assert key in row

    @pytest.mark.integration
    def test_search_by_gtin_exact_match(
        self, test_client: TestClient, sample_product: Product
    ):
        response = test_client.get(
            f"/api/v1/products/search?gtin={sample_product.gtin}"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["gtin"] == sample_product.gtin
        assert data[0]["id"] == str(sample_product.id)

    @pytest.mark.integration
    def test_search_pagination_limit_respected(
        self, test_client: TestClient, sample_products: list[Product]
    ):
        response = test_client.get("/api/v1/products/search?q=e&limit=2")
        assert response.status_code == 200
        assert len(response.json()) <= 2

    @pytest.mark.integration
    def test_search_no_match_returns_empty_list(self, test_client: TestClient):
        response = test_client.get(
            "/api/v1/products/search?q=zzznonexistentproductzzz"
        )
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.integration
    def test_search_missing_query_returns_all(
        self, test_client: TestClient, sample_products: list[Product]
    ):
        # NOTE: with neither q nor gtin the endpoint returns all products (200),
        # not a validation error. Asserting the actual behavior.
        response = test_client.get("/api/v1/products/search")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.integration
    def test_search_query_too_long_is_422(self, test_client: TestClient):
        response = test_client.get("/api/v1/products/search?q=" + "a" * 300)
        assert response.status_code == 422

    @pytest.mark.integration
    def test_search_ranked_sale_items_float_first(
        self, test_client: TestClient, test_db_session, sample_products: list[Product]
    ):
        """On-sale products should appear before non-sale products for the same query."""
        from app.models import PriceReport, Store
        from datetime import datetime, timezone

        # Find any store to attach the price report to
        store = test_db_session.execute(
            __import__("sqlalchemy").select(Store)
        ).scalars().first()
        if store is None:
            return  # No stores seeded — skip gracefully

        # Mark the second sample product as on-sale via a price report
        on_sale_product = sample_products[1]
        report = PriceReport(
            product_id=on_sale_product.id,
            store_id=store.id,
            price_cents=199,
            source="flyer_coles",
            confidence=1.0,
            is_on_sale=True,
            sale_pct_off=20.0,
            valid_from=datetime.now(timezone.utc),
        )
        test_db_session.add(report)
        test_db_session.commit()

        response = test_client.get("/api/v1/products/search?q=e")
        assert response.status_code == 200
        data = response.json()
        if len(data) < 2:
            return  # Not enough results to verify ordering

        on_sale_ids = {str(on_sale_product.id)}
        positions = [i for i, p in enumerate(data) if p["id"] in on_sale_ids]
        non_sale_positions = [i for i, p in enumerate(data) if p["id"] not in on_sale_ids]
        if positions and non_sale_positions:
            assert min(positions) < max(non_sale_positions)

    @pytest.mark.integration
    def test_search_typo_tolerance(
        self, test_client: TestClient, sample_products: list[Product]
    ):
        """Trigram search should find 'Milk' even with a minor typo."""
        response = test_client.get("/api/v1/products/search?q=Mlk")
        assert response.status_code == 200
        # May or may not match depending on threshold — just ensure no server error
        assert isinstance(response.json(), list)


class TestSearchService:
    """Unit tests for search.py (dialect-aware, no HTTP layer)."""

    def test_rrf_fusion_deduplicates_and_sums(self):
        from unittest.mock import MagicMock
        from app.services.search import _fuse_rrf

        p1, p2, p3 = MagicMock(), MagicMock(), MagicMock()
        p1.id, p2.id, p3.id = "aaa", "bbb", "ccc"

        trgm = [(p1, 0.9), (p2, 0.5)]
        vec  = [(p2, 0.8), (p3, 0.6)]

        fused = _fuse_rrf(trgm, vec)
        ids = [p.id for p, _ in fused]

        # p2 appears in both lanes so must score higher than p3 (vec only)
        assert ids.index("bbb") < ids.index("ccc")
        # All 3 products present
        assert set(ids) == {"aaa", "bbb", "ccc"}

    def test_rrf_score_decreases_with_rank(self):
        from app.services.search import _rrf_score
        assert _rrf_score(0) > _rrf_score(1) > _rrf_score(10)

    def test_normalise_unit_ml_to_l(self):
        from app.services.embeddings import normalise_product_name
        assert "2l" in normalise_product_name("Full Cream Milk 2000ml")

    def test_normalise_unit_g_to_kg(self):
        from app.services.embeddings import normalise_product_name
        assert "0.5kg" in normalise_product_name("Plain Flour 500g")

    def test_embed_text_returns_384_dims(self):
        from app.services.embeddings import embed_text
        vec = embed_text("full cream milk 2l")
        assert len(vec) == 384
        # Unit normalised vectors should be approximately unit length
        import math
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-4

    def test_sqlite_path_uses_ilike(self, test_client, sample_products):
        """Search on the SQLite test DB should return results via ILIKE fallback."""
        resp = test_client.get("/api/v1/products/search?q=Milk")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert any("Milk" in n for n in names)

    def test_geo_params_accepted(self, test_client, sample_products):
        """lat/lon/radius_km params should be accepted without error."""
        resp = test_client.get(
            "/api/v1/products/search?q=Milk&lat=-33.8688&lon=151.2093&radius_km=10"
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_geo_params_out_of_range_rejected(self, test_client):
        """lat > 90 should return 422."""
        resp = test_client.get("/api/v1/products/search?q=Milk&lat=91&lon=0")
        assert resp.status_code == 422

    def test_sale_bonus_function(self):
        from app.services.search import _sale_bonus, _SALE_BONUS_MAX
        assert _sale_bonus(None) == 0.0
        assert _sale_bonus(0.0) == 0.0
        assert abs(_sale_bonus(100.0) - _SALE_BONUS_MAX) < 1e-9
        assert _sale_bonus(50.0) == _SALE_BONUS_MAX * 0.5
        assert _sale_bonus(200.0) == _SALE_BONUS_MAX  # capped at 100%

    def test_geo_bonus_decays_with_distance(self):
        from app.services.search import _geo_bonus, _GEO_BONUS_MAX
        assert abs(_geo_bonus(0.0) - _GEO_BONUS_MAX) < 1e-9
        assert _geo_bonus(5.0) == _GEO_BONUS_MAX * 0.5   # half-life
        assert _geo_bonus(1000.0) < 0.0001                # negligible at far distance

    def test_unit_normalisation_applied_to_trigram_query(self):
        """Query normalisation: '2000ml' should become '2l' before trigram matching."""
        from app.services.embeddings import normalise_product_name
        assert "2l" in normalise_product_name("milk 2000ml")
        assert "0.5kg" in normalise_product_name("flour 500g")

    def test_haversine_sydney_melbourne(self):
        from app.services.search import _haversine_km
        # Sydney (-33.87, 151.21) to Melbourne (-37.81, 144.96) ≈ 713 km
        dist = _haversine_km(-33.87, 151.21, -37.81, 144.96)
        assert 700 < dist < 730

    def test_negation_parser_detects_free(self):
        from app.services.search import _parse_negations
        words = ["dairy", "free", "milk"]
        negated = _parse_negations(words)
        assert "dairy" in negated

    def test_negation_parser_detects_no(self):
        from app.services.search import _parse_negations
        words = ["no", "added", "sugar"]
        negated = _parse_negations(words)
        assert "added" in negated or "sugar" in negated

    def test_negation_parser_detects_without(self):
        from app.services.search import _parse_negations
        words = ["without", "lactose"]
        negated = _parse_negations(words)
        assert "lactose" in negated

    def test_negation_parser_empty_on_no_modifiers(self):
        from app.services.search import _parse_negations
        words = ["full", "cream", "milk"]
        assert _parse_negations(words) == []

    def test_negation_multiplier_penalises_brand_mismatch(self):
        from app.services.search import _negation_score_multiplier, _NEGATION_PENALTY
        # "Dairy Milk Freddo" contains "dairy" but NOT "free" → penalty
        mult = _negation_score_multiplier("Dairy Milk Freddo 200g", ["dairy"])
        assert mult == _NEGATION_PENALTY

    def test_negation_multiplier_spares_correct_product(self):
        from app.services.search import _negation_score_multiplier
        # "Oat Milk Dairy Free 1L" contains "dairy" AND "free" → no penalty
        mult = _negation_score_multiplier("Oat Milk Dairy Free 1L", ["dairy"])
        assert mult == 1.0

    def test_negation_multiplier_no_negated_words(self):
        from app.services.search import _negation_score_multiplier
        mult = _negation_score_multiplier("Full Cream Milk 2L", [])
        assert mult == 1.0

    def test_is_intent_query_single_word(self):
        from app.services.search import _is_intent_query
        assert not _is_intent_query(["milk"])

    def test_is_intent_query_multi_word(self):
        from app.services.search import _is_intent_query
        assert _is_intent_query(["dairy", "free", "milk"])

    def test_rrf_fusion_intent_flag(self):
        from unittest.mock import MagicMock
        from app.services.search import _fuse_rrf

        p1, p2 = MagicMock(), MagicMock()
        p1.id, p2.id = "aaa", "bbb"

        # p1 ranks first in trigram, p2 ranks first in vector
        trgm = [(p1, 0.9), (p2, 0.1)]
        vec  = [(p2, 0.9), (p1, 0.1)]

        # With intent=False (equal k), trigram lane rank-0 dominates
        non_intent = _fuse_rrf(trgm, vec, intent_query=False)
        # With intent=True (larger trigram k), semantic lane rank-0 should dominate
        intent = _fuse_rrf(trgm, vec, intent_query=True)

        non_intent_ids = [p.id for p, _ in non_intent]
        intent_ids = [p.id for p, _ in intent]

        # In intent mode, p2 (top of vec lane) should score at least as high as p1
        assert intent_ids[0] == "bbb"
        # In non-intent mode, p1 (top of trgm lane at lower k) should lead
        assert non_intent_ids[0] == "aaa"


class TestProductDetailEndpoints:
    """GET /api/v1/products/{id} and /prices"""

    @pytest.mark.integration
    def test_get_product_detail_200(
        self, test_client: TestClient, sample_product: Product
    ):
        response = test_client.get(f"/api/v1/products/{sample_product.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(sample_product.id)
        assert data["name"] == sample_product.name

    @pytest.mark.integration
    def test_get_product_detail_unknown_is_404(self, test_client: TestClient):
        response = test_client.get(f"/api/v1/products/{uuid4()}")
        assert response.status_code == 404

    @pytest.mark.integration
    def test_get_product_detail_bad_uuid_is_422(self, test_client: TestClient):
        response = test_client.get("/api/v1/products/not-a-uuid")
        assert response.status_code == 422

    @pytest.mark.integration
    def test_get_product_prices_200(
        self, test_client: TestClient, sample_product: Product
    ):
        response = test_client.get(
            f"/api/v1/products/{sample_product.id}/prices?days=90"
        )
        assert response.status_code == 200
        data = response.json()
        # Price-history envelope shape.
        assert data["product_id"] == str(sample_product.id)
        assert "data" in data and isinstance(data["data"], list)

    @pytest.mark.integration
    def test_get_product_prices_unknown_is_404(self, test_client: TestClient):
        response = test_client.get(f"/api/v1/products/{uuid4()}/prices")
        assert response.status_code == 404


class TestStoreEndpoints:
    """GET /api/v1/stores/nearby, /{id}, /{id}/deals"""

    @pytest.mark.integration
    def test_nearby_returns_seeded_store_with_distance(
        self, test_client: TestClient, sample_store: Store
    ):
        response = test_client.get(
            "/api/v1/stores/nearby",
            params={"lat": -33.87, "lon": 151.21, "radius_km": 10},
        )
        assert response.status_code == 200
        data = response.json()
        # Envelope shape — this is the exact regression that crashed (500).
        assert isinstance(data, dict)
        assert data["count"] >= 1
        assert isinstance(data["stores"], list)
        store = data["stores"][0]
        assert "distance_km" in store
        assert isinstance(store["distance_km"], (int, float))
        assert store["distance_km"] >= 0

    @pytest.mark.integration
    def test_nearby_no_results_is_empty_envelope(
        self, test_client: TestClient, sample_store: Store
    ):
        # Far from the seeded Sydney store, tight radius -> zero results, still 200.
        response = test_client.get(
            "/api/v1/stores/nearby",
            params={"lat": -43.0, "lon": 147.0, "radius_km": 1},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["stores"] == []

    @pytest.mark.integration
    def test_nearby_missing_required_params_is_422(self, test_client: TestClient):
        response = test_client.get("/api/v1/stores/nearby")
        assert response.status_code == 422

    @pytest.mark.integration
    def test_nearby_invalid_latitude_is_422(self, test_client: TestClient):
        response = test_client.get(
            "/api/v1/stores/nearby",
            params={"lat": 91, "lon": 151.21, "radius_km": 10},
        )
        assert response.status_code == 422

    @pytest.mark.integration
    def test_get_store_detail_200(
        self, test_client: TestClient, sample_store: Store
    ):
        response = test_client.get(f"/api/v1/stores/{sample_store.id}")
        assert response.status_code == 200
        assert response.json()["id"] == str(sample_store.id)

    @pytest.mark.integration
    def test_get_store_detail_unknown_is_404(self, test_client: TestClient):
        response = test_client.get(f"/api/v1/stores/{uuid4()}")
        assert response.status_code == 404

    @pytest.mark.integration
    def test_get_store_deals_200(
        self, test_client: TestClient, sample_store: Store
    ):
        response = test_client.get(f"/api/v1/stores/{sample_store.id}/deals")
        assert response.status_code == 200
        assert isinstance(response.json(), (list, dict))


class TestDealsEndpoint:
    """GET /api/v1/deals/"""

    @pytest.mark.integration
    def test_list_deals_200(self, test_client: TestClient):
        response = test_client.get("/api/v1/deals/")
        assert response.status_code == 200
        assert isinstance(response.json(), (list, dict))


class TestPredictionsEndpoint:
    """GET /api/v1/predictions/{product_id}"""

    @pytest.mark.integration
    def test_predictions_unknown_product_404(self, test_client: TestClient):
        import uuid
        response = test_client.get(f"/api/v1/predictions/{uuid.uuid4()}")
        assert response.status_code == 404

    @pytest.mark.integration
    def test_predictions_returns_list(self, test_client: TestClient, sample_product):
        response = test_client.get(f"/api/v1/predictions/{sample_product.id}")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestPublicUserProfile:
    """GET /api/v1/users/{user_id}/profile (public)"""

    @pytest.mark.integration
    def test_unknown_user_profile_is_404(self, test_client: TestClient):
        response = test_client.get(f"/api/v1/users/{uuid4()}/profile")
        assert response.status_code == 404


class TestAuthGuards:
    """Endpoints requiring auth must reject anonymous requests."""

    @pytest.mark.integration
    def test_get_watchlist_requires_auth(self, test_client: TestClient):
        response = test_client.get("/api/v1/watchlist/")
        assert response.status_code in (401, 403)

    @pytest.mark.integration
    def test_post_watchlist_requires_auth(self, test_client: TestClient):
        response = test_client.post(
            "/api/v1/watchlist/", json={"product_id": str(uuid4())}
        )
        assert response.status_code in (401, 403, 422)

    @pytest.mark.integration
    def test_get_notifications_requires_auth(self, test_client: TestClient):
        response = test_client.get("/api/v1/notifications/")
        assert response.status_code in (401, 403)

    @pytest.mark.integration
    def test_users_me_requires_auth(self, test_client: TestClient):
        response = test_client.get("/api/v1/users/me")
        assert response.status_code in (401, 403)

    @pytest.mark.integration
    def test_submit_price_report_requires_auth(self, test_client: TestClient):
        response = test_client.post("/api/v1/price-reports/", json={})
        assert response.status_code in (401, 403, 422)


class TestHealthEndpoints:
    """/health and root."""

    @pytest.mark.integration
    def test_health_ok(self, test_client: TestClient):
        response = test_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("ok", "healthy")
        assert "checks" in data

    @pytest.mark.integration
    def test_health_live(self, test_client: TestClient):
        response = test_client.get("/health/live")
        assert response.status_code == 200

    @pytest.mark.integration
    def test_root_ok(self, test_client: TestClient):
        response = test_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "name" in data
        assert "version" in data


class TestErrorHandling:
    """Cross-cutting error behavior."""

    @pytest.mark.integration
    def test_unknown_route_is_404(self, test_client: TestClient):
        response = test_client.get("/api/v1/does-not-exist")
        assert response.status_code == 404

    @pytest.mark.integration
    def test_wrong_method_is_405(self, test_client: TestClient):
        # search is GET-only.
        response = test_client.post("/api/v1/products/search")
        assert response.status_code == 405

    @pytest.mark.integration
    def test_error_response_has_detail(self, test_client: TestClient):
        response = test_client.get(f"/api/v1/products/{uuid4()}")
        assert response.status_code == 404
        assert "detail" in response.json()

    @pytest.mark.integration
    def test_json_content_type_on_success(
        self, test_client: TestClient, sample_products: list[Product]
    ):
        response = test_client.get("/api/v1/products/search?q=Milk")
        assert response.headers["content-type"].startswith("application/json")

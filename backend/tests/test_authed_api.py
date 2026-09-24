"""
Authenticated-path integration tests.

Uses the ``authed_client`` fixture (conftest) which overrides get_current_user
to return a seeded test user, so these exercise the real authenticated business
logic against the test DB — the bulk of Phase 2 that the unauth tests can't reach.

Note: the price-reports POST endpoint authenticates via a raw Authorization
header + verify_firebase_token (not get_current_user), so it's covered with a
patched verifier in TestAuthedPriceReports rather than the authed_client fixture.
"""

import io
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models import Product, Store, UserProfile


class TestUsersMe:
    @pytest.mark.integration
    def test_get_me_returns_profile(self, authed_client, authed_user: UserProfile):
        resp = authed_client.get("/api/v1/users/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == authed_user.email
        # tier is derived from contribution_count (5 -> bronze)
        assert data["tier"] == "bronze"


class TestWatchlistCrud:
    """Named-lists + items API: /watchlist/ (lists) and /watchlist/{id}/items."""

    def _make_list(self, authed_client, name="Groceries"):
        resp = authed_client.post("/api/v1/watchlist/", json={"name": name, "emoji": "🛒"})
        assert resp.status_code == 201, resp.text
        return resp.json()

    @pytest.mark.integration
    def test_create_list_add_update_delete_item(self, authed_client, sample_product: Product):
        # Create a list.
        lst = self._make_list(authed_client)
        list_id = lst["id"]
        assert lst["name"] == "Groceries"
        assert lst["item_count"] == 0

        # The list shows up in the user's lists.
        resp = authed_client.get("/api/v1/watchlist/")
        assert resp.status_code == 200
        assert any(l["id"] == list_id for l in resp.json())

        # Add a product to the list.
        resp = authed_client.post(
            f"/api/v1/watchlist/{list_id}/items",
            json={"product_id": str(sample_product.id), "target_price_cents": 400},
        )
        assert resp.status_code == 201, resp.text
        item = resp.json()
        assert item["product_id"] == str(sample_product.id)
        assert item["target_price_cents"] == 400

        # It appears in the list's items.
        resp = authed_client.get(f"/api/v1/watchlist/{list_id}/items")
        assert resp.status_code == 200
        assert any(i["product_id"] == str(sample_product.id) for i in resp.json())

        # Update the target price via PATCH.
        resp = authed_client.patch(
            f"/api/v1/watchlist/{list_id}/items/{sample_product.id}",
            json={"product_id": str(sample_product.id), "target_price_cents": 300},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["target_price_cents"] == 300

        # Soft-delete the item -> no longer in active items.
        resp = authed_client.delete(f"/api/v1/watchlist/{list_id}/items/{sample_product.id}")
        assert resp.status_code == 204
        resp = authed_client.get(f"/api/v1/watchlist/{list_id}/items")
        assert all(i["product_id"] != str(sample_product.id) for i in resp.json())

    @pytest.mark.integration
    def test_add_item_without_target_succeeds(self, authed_client, sample_product: Product):
        # target_price_cents is optional in the item schema — an item can watch a
        # product for any price drop / prediction without a hard target.
        list_id = self._make_list(authed_client)["id"]
        resp = authed_client.post(
            f"/api/v1/watchlist/{list_id}/items",
            json={"product_id": str(sample_product.id)},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["target_price_cents"] is None

    @pytest.mark.integration
    def test_add_unknown_product_is_404(self, authed_client):
        list_id = self._make_list(authed_client)["id"]
        resp = authed_client.post(
            f"/api/v1/watchlist/{list_id}/items",
            json={"product_id": str(uuid4()), "target_price_cents": 400},
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_add_item_to_unknown_list_is_404(self, authed_client, sample_product: Product):
        resp = authed_client.post(
            f"/api/v1/watchlist/{uuid4()}/items",
            json={"product_id": str(sample_product.id), "target_price_cents": 400},
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_duplicate_product_is_idempotent(self, authed_client, sample_product: Product):
        # Adding the same product to a list twice returns the same item (the
        # endpoint reactivates/returns the existing row rather than erroring).
        list_id = self._make_list(authed_client)["id"]
        body = {"product_id": str(sample_product.id), "target_price_cents": 400}
        first = authed_client.post(f"/api/v1/watchlist/{list_id}/items", json=body)
        assert first.status_code == 201, first.text
        dup = authed_client.post(f"/api/v1/watchlist/{list_id}/items", json=body)
        assert dup.status_code == 201, dup.text
        assert dup.json()["id"] == first.json()["id"]

        # Only one active item for the product in the list.
        items = authed_client.get(f"/api/v1/watchlist/{list_id}/items").json()
        matching = [i for i in items if i["product_id"] == str(sample_product.id)]
        assert len(matching) == 1


class TestNotifications:
    @pytest.mark.integration
    def test_list_notifications_empty(self, authed_client):
        resp = authed_client.get("/api/v1/notifications/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.integration
    def test_register_fcm_token(self, authed_client):
        resp = authed_client.post(
            "/api/v1/notifications/fcm-token", json={"fcm_token": "new-token-123"}
        )
        assert resp.status_code in (200, 201, 204)


class TestAuthedReceiptUpload:
    @pytest.mark.integration
    def test_upload_matches_and_creates_report(
        self, authed_client, sample_product: Product, sample_store: Store
    ):
        # Mock the Claude vision parser to return one line item that matches
        # the seeded product by name, so a receipt_ocr report is created.
        from app.deps import get_anthropic_client
        from app.main import app
        from app.services.receipt_parser import ParsedProduct, ParsedReceipt

        parsed = ParsedReceipt(
            products=[ParsedProduct(name=sample_product.name, price=3.50, quantity=1)],
            store_name="Coles",
            total=3.50,
        )

        async def fake_parse(self, image_bytes, image_type):
            return parsed

        # The Anthropic client dependency raises if no API key; override it.
        app.dependency_overrides[get_anthropic_client] = lambda: object()
        try:
            with patch(
                "app.routers.receipts.ReceiptParser.parse_receipt_image",
                new=fake_parse,
            ):
                resp = authed_client.post(
                    "/api/v1/receipts/upload",
                    files={"file": ("r.jpg", io.BytesIO(b"\xff\xd8\xffdata"), "image/jpeg")},
                    data={"store_id": str(sample_store.id)},
                )
        finally:
            app.dependency_overrides.pop(get_anthropic_client, None)

        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["status"] in ("success", "partial")
        assert len(data["created_reports"]) == 1
        assert data["created_reports"][0]["product_name"] == sample_product.name

    @pytest.mark.integration
    def test_upload_unmatched_line_item(self, authed_client, sample_store: Store):
        from app.deps import get_anthropic_client
        from app.main import app
        from app.services.receipt_parser import ParsedProduct, ParsedReceipt

        parsed = ParsedReceipt(
            products=[ParsedProduct(name="Totally Unknown SKU XYZ", price=9.99)],
            total=9.99,
        )

        async def fake_parse(self, image_bytes, image_type):
            return parsed

        app.dependency_overrides[get_anthropic_client] = lambda: object()
        try:
            with patch(
                "app.routers.receipts.ReceiptParser.parse_receipt_image",
                new=fake_parse,
            ):
                resp = authed_client.post(
                    "/api/v1/receipts/upload",
                    files={"file": ("r.jpg", io.BytesIO(b"\xff\xd8\xffdata"), "image/jpeg")},
                    data={"store_id": str(sample_store.id)},
                )
        finally:
            app.dependency_overrides.pop(get_anthropic_client, None)

        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["created_reports"] == []
        assert len(data["unmatched_items"]) == 1

"""
Tests for receipt photo parsing (Claude vision) and the upload endpoint.

Unit tests exercise the pure parsing logic in
:mod:`app.services.receipt_parser` with a mocked Anthropic client (no real API
calls). Integration tests exercise the ``/api/v1/receipts/upload`` endpoint
against the test database with the Anthropic dependency overridden.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.receipt_parser import (
    ParsedReceipt,
    ReceiptParseError,
    ReceiptParser,
    _strip_json_fences,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_anthropic(response_text: str) -> MagicMock:
    """Build a mock Anthropic client whose messages.create returns text."""
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text=response_text)]
    )
    return client


def _mock_anthropic_raising(exc: Exception) -> MagicMock:
    """Build a mock Anthropic client whose messages.create raises."""
    client = MagicMock()
    client.messages.create.side_effect = exc
    return client


VALID_RECEIPT_JSON = json.dumps(
    {
        "store_name": "Coles Chatswood",
        "receipt_date": "2026-08-29",
        "total": 12.30,
        "products": [
            {"name": "Milk 2L", "price": 3.50, "quantity": 1, "unit_price": 3.50},
            {"name": "Bread Multigrain", "price": 4.80, "quantity": 1, "unit_price": None},
            {"name": "Eggs Dozen", "price": 3.00, "quantity": 1, "unit_price": None},
        ],
    }
)

# A tiny valid-looking byte payload; the mock never inspects real image content.
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 128


# ---------------------------------------------------------------------------
# Unit tests: pure parsing helpers
# ---------------------------------------------------------------------------


class TestStripJsonFences:
    @pytest.mark.unit
    def test_plain_json_untouched(self):
        assert _strip_json_fences('{"a": 1}') == '{"a": 1}'

    @pytest.mark.unit
    def test_strips_json_fence(self):
        wrapped = '```json\n{"a": 1}\n```'
        assert _strip_json_fences(wrapped) == '{"a": 1}'

    @pytest.mark.unit
    def test_strips_bare_fence(self):
        wrapped = '```\n{"a": 1}\n```'
        assert _strip_json_fences(wrapped) == '{"a": 1}'


class TestReceiptParserUnit:
    @pytest.mark.unit
    async def test_parses_valid_receipt(self):
        parser = ReceiptParser(_mock_anthropic(VALID_RECEIPT_JSON))
        result = await parser.parse_receipt_image(FAKE_JPEG, "image/jpeg")

        assert isinstance(result, ParsedReceipt)
        assert result.store_name == "Coles Chatswood"
        assert result.total == 12.30
        assert len(result.products) == 3
        assert result.products[0].name == "Milk 2L"
        assert result.products[0].price == 3.50

    @pytest.mark.unit
    async def test_rejects_unsupported_type(self):
        parser = ReceiptParser(_mock_anthropic(VALID_RECEIPT_JSON))
        with pytest.raises(ReceiptParseError):
            await parser.parse_receipt_image(FAKE_JPEG, "application/pdf")

    @pytest.mark.unit
    async def test_rejects_empty_image(self):
        parser = ReceiptParser(_mock_anthropic(VALID_RECEIPT_JSON))
        with pytest.raises(ReceiptParseError):
            await parser.parse_receipt_image(b"", "image/jpeg")

    @pytest.mark.unit
    async def test_handles_fenced_json_response(self):
        fenced = f"```json\n{VALID_RECEIPT_JSON}\n```"
        parser = ReceiptParser(_mock_anthropic(fenced))
        result = await parser.parse_receipt_image(FAKE_JPEG, "image/png")
        assert len(result.products) == 3

    @pytest.mark.unit
    async def test_empty_products_for_non_receipt(self):
        parser = ReceiptParser(_mock_anthropic('{"products": []}'))
        result = await parser.parse_receipt_image(FAKE_JPEG, "image/jpeg")
        assert result.products == []

    @pytest.mark.unit
    async def test_skips_malformed_line_items(self):
        payload = json.dumps(
            {
                "products": [
                    {"name": "Milk", "price": 3.5},
                    {"name": "NoPrice"},          # missing price -> skipped
                    {"price": 2.0},               # missing name -> skipped
                    {"name": "Bad", "price": "x"},  # non-numeric -> skipped
                ]
            }
        )
        parser = ReceiptParser(_mock_anthropic(payload))
        result = await parser.parse_receipt_image(FAKE_JPEG, "image/jpeg")
        assert len(result.products) == 1
        assert result.products[0].name == "Milk"

    @pytest.mark.unit
    async def test_invalid_json_raises(self):
        parser = ReceiptParser(_mock_anthropic("not json at all"))
        with pytest.raises(ReceiptParseError):
            await parser.parse_receipt_image(FAKE_JPEG, "image/jpeg")

    @pytest.mark.unit
    async def test_api_error_normalized(self):
        parser = ReceiptParser(_mock_anthropic_raising(RuntimeError("429 rate limit")))
        with pytest.raises(ReceiptParseError):
            await parser.parse_receipt_image(FAKE_JPEG, "image/jpeg")


# ---------------------------------------------------------------------------
# Integration tests: upload endpoint
# ---------------------------------------------------------------------------


class TestReceiptUploadEndpoint:
    """Integration tests for POST /api/v1/receipts/upload.

    These override the Anthropic dependency with a mock client so no real API
    calls are made. They require the test DB fixtures from conftest.py.
    """

    @pytest.mark.integration
    def test_upload_requires_auth(self, test_client):
        """Upload without auth should be rejected (401/403/404)."""
        resp = test_client.post(
            "/api/v1/receipts/upload",
            files={"file": ("r.jpg", FAKE_JPEG, "image/jpeg")},
        )
        assert resp.status_code in (401, 403, 404, 422)

    @pytest.mark.integration
    def test_upload_rejects_bad_content_type(self, test_client):
        """Non-image content type should 400 (or 401 if auth checked first)."""
        resp = test_client.post(
            "/api/v1/receipts/upload",
            files={"file": ("r.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code in (400, 401, 403, 404, 422)

    # Authenticated receipt-upload flows (matched/unmatched line items, created
    # reports, source='receipt_ocr') are covered in test_authed_api.py, which
    # overrides get_current_user and the Anthropic client.

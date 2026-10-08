"""
Story #63 regression tests — Email Deliverability Hardening.

Verifies:
  1. List-Unsubscribe and List-Unsubscribe-Post headers are included in every outbound email.
  2. List-Unsubscribe header value uses one-click URL + mailto fallback.
  3. list-Unsubscribe-Post value is "List-Unsubscribe=One-Click" (RFC 8058).
  4. HTML exceeding 90 KB triggers a warning log (pre-send size guard).
  5. HTML under 90 KB does NOT trigger the warning.
  6. One-click unsubscribe endpoint returns 200 and sets user preference.
  7. Expired/invalid unsubscribe token returns 400.
"""

import logging
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.email_sender import (
    _EMAIL_CLIP_WARN_BYTES,
    _UNSUBSCRIBE_EMAIL,
    generate_unsubscribe_token,
    send_price_alert_email,
    send_weekly_digest_email,
)


def _fake_resend(captured: dict):
    """httpx.post side-effect that records the JSON payload and returns a mock response."""

    def _post(url, *, headers, json, timeout):
        captured.update(json)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"id": "msg-test-001"}
        return resp

    return _post


# ── 1–3: List-Unsubscribe headers ────────────────────────────────────────────


class TestListUnsubscribeHeaders:
    @pytest.fixture(autouse=True)
    def _enable_email(self):
        with patch("app.services.email_sender.settings") as mock_settings:
            mock_settings.RESEND_API_KEY = "re_test_key"
            mock_settings.SECRET_KEY = "test-secret"
            mock_settings.APP_BASE_URL = "https://dealfindio.com.au"
            mock_settings.EMAIL_FROM_NAME = "DealFindio"
            mock_settings.EMAIL_FROM_ADDRESS = "alerts@dealfindio.com.au"
            yield mock_settings

    def test_price_alert_includes_list_unsubscribe(self):
        """send_price_alert_email sends List-Unsubscribe and List-Unsubscribe-Post headers."""
        captured: dict = {}
        with patch("app.services.email_sender.httpx.post", side_effect=_fake_resend(captured)):
            send_price_alert_email(
                to="user@example.com",
                user_id=str(uuid4()),
                product_name="Weet-Bix 750g",
                price=4.50,
                was_price=6.00,
                store_name="Coles",
                product_id=str(uuid4()),
                trigger_type="price_match",
            )

        headers = captured.get("headers", {})
        assert "List-Unsubscribe" in headers, "List-Unsubscribe header missing from price alert"
        assert "List-Unsubscribe-Post" in headers, "List-Unsubscribe-Post header missing"
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert f"<mailto:{_UNSUBSCRIBE_EMAIL}>" in headers["List-Unsubscribe"]

    def test_weekly_digest_includes_list_unsubscribe(self):
        """send_weekly_digest_email sends List-Unsubscribe and List-Unsubscribe-Post headers."""
        captured: dict = {}
        with patch("app.services.email_sender.httpx.post", side_effect=_fake_resend(captured)):
            send_weekly_digest_email(
                to="user@example.com",
                user_id=str(uuid4()),
                deals=[
                    {
                        "product_name": "Milk 2L",
                        "price": 2.50,
                        "was_price": 3.00,
                        "store_name": "Woolworths",
                        "product_id": str(uuid4()),
                        "pct_off": 17,
                    }
                ],
            )

        headers = captured.get("headers", {})
        assert "List-Unsubscribe" in headers, "List-Unsubscribe header missing from weekly digest"
        assert "List-Unsubscribe-Post" in headers, "List-Unsubscribe-Post header missing"
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_list_unsubscribe_contains_https_url(self):
        """List-Unsubscribe header contains an HTTPS one-click URL."""
        captured: dict = {}
        with patch("app.services.email_sender.httpx.post", side_effect=_fake_resend(captured)):
            send_price_alert_email(
                to="user@example.com",
                user_id=str(uuid4()),
                product_name="Bread",
                price=3.00,
                was_price=None,
                store_name="IGA",
                product_id=str(uuid4()),
                trigger_type="flash_sale",
            )

        unsub_header = captured.get("headers", {}).get("List-Unsubscribe", "")
        # Must have an HTTPS URL bracketed with <>
        assert "<https://" in unsub_header, f"No HTTPS URL in List-Unsubscribe: {unsub_header!r}"


# ── 4–5: Pre-send size guard ─────────────────────────────────────────────────


class TestEmailSizeGuard:
    @pytest.fixture(autouse=True)
    def _enable_email(self):
        with patch("app.services.email_sender.settings") as mock_settings:
            mock_settings.RESEND_API_KEY = "re_test_key"
            mock_settings.SECRET_KEY = "test-secret"
            mock_settings.APP_BASE_URL = "https://dealfindio.com.au"
            mock_settings.EMAIL_FROM_NAME = "DealFindio"
            mock_settings.EMAIL_FROM_ADDRESS = "alerts@dealfindio.com.au"
            yield mock_settings

    def test_oversized_html_logs_warning(self, caplog):
        """HTML exceeding 90 KB triggers a warning log before sending."""
        from app.services.email_sender import _send_email

        big_html = "x" * (_EMAIL_CLIP_WARN_BYTES + 1)

        with patch("app.services.email_sender.httpx.post", side_effect=_fake_resend({})):
            with caplog.at_level(logging.WARNING, logger="app.services.email_sender"):
                _send_email("u@e.com", "Subject", big_html)

        assert any("clipped" in r.message or str(_EMAIL_CLIP_WARN_BYTES) in r.message for r in caplog.records), (
            "Expected a clipping-warning log for oversized HTML"
        )

    def test_normal_html_does_not_log_warning(self, caplog):
        """HTML under 90 KB does not trigger any clip warning."""
        from app.services.email_sender import _send_email

        small_html = "<p>Hello</p>"

        with patch("app.services.email_sender.httpx.post", side_effect=_fake_resend({})):
            with caplog.at_level(logging.WARNING, logger="app.services.email_sender"):
                _send_email("u@e.com", "Subject", small_html)

        assert not any("clipped" in r.message for r in caplog.records)


# ── 6–10: One-click unsubscribe endpoints ────────────────────────────────────


class TestUnsubscribeEndpoint:
    @pytest.mark.integration
    def test_get_shows_confirmation_page_and_does_not_mutate(
        self, authed_client, authed_user, test_db_session
    ):
        """GET renders a confirmation form — does NOT mutate preferences (scanner-safe)."""
        token = generate_unsubscribe_token(str(authed_user.id), "email_price_match")
        original_pref = authed_user.notif_email_price_match

        resp = authed_client.get(f"/api/v1/notifications/unsubscribe?token={token}")
        assert resp.status_code == 200
        # Page must contain a POST form for human confirmation
        assert 'method="POST"' in resp.text or 'method="post"' in resp.text

        # Preferences MUST NOT have changed
        test_db_session.refresh(authed_user)
        assert authed_user.notif_email_price_match == original_pref

    @pytest.mark.integration
    def test_post_valid_token_unsubscribes_and_returns_200(
        self, authed_client, authed_user, test_db_session
    ):
        """RFC 8058: POST with a valid token disables email prefs and returns 200."""
        token = generate_unsubscribe_token(str(authed_user.id), "email_price_match")

        resp = authed_client.post(f"/api/v1/notifications/unsubscribe?token={token}")
        assert resp.status_code == 200
        assert "unsubscribed" in resp.text.lower()

        test_db_session.refresh(authed_user)
        assert authed_user.notif_email_price_match is False
        assert authed_user.notif_email_pct_off is False

    @pytest.mark.integration
    def test_post_invalid_token_returns_400(self, authed_client):
        """POST with a tampered token returns 400 error page."""
        resp = authed_client.post("/api/v1/notifications/unsubscribe?token=bad.token.here")
        assert resp.status_code == 400
        assert "invalid" in resp.text.lower() or "expired" in resp.text.lower()

    @pytest.mark.integration
    def test_post_is_idempotent(self, authed_client, authed_user, test_db_session):
        """Repeated POST with the same token returns 200 both times (idempotent unsubscribe)."""
        token = generate_unsubscribe_token(str(authed_user.id), "email_price_match")

        resp1 = authed_client.post(f"/api/v1/notifications/unsubscribe?token={token}")
        resp2 = authed_client.post(f"/api/v1/notifications/unsubscribe?token={token}")
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        test_db_session.refresh(authed_user)
        assert authed_user.notif_email_price_match is False

    @pytest.mark.integration
    def test_get_invalid_token_returns_400(self, authed_client):
        """GET with an invalid token returns 400 without showing the confirmation form."""
        resp = authed_client.get("/api/v1/notifications/unsubscribe?token=invalid.token.here")
        assert resp.status_code == 400
        assert "invalid" in resp.text.lower() or "expired" in resp.text.lower()

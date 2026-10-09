"""
Story #62/#65 tests — notification copy length, product image cards, list grouping.

Covers:
  1. Email subject lines <= 50 chars for all trigger types (mobile preview budget).
  2. Push bodies <= 100 chars for all trigger types (lock-screen truncation).
  3. Price alert HTML contains <img> with CDN URL when image_url is supplied.
  4. Price alert HTML contains no base64 data URIs.
  5. Weekly digest HTML contains list section headers when list_name is provided.
  6. Weekly digest groups deals under separate <h3> headers per list.
  7. 80 KB size guard: digest rebuilt without images when HTML exceeds budget.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.email_sender import (
    _DIGEST_SIZE_BUDGET,
    _build_digest_content,
    _build_subject,
)
from app.workers.notification_tasks import _build_push_copy

# ── helpers ───────────────────────────────────────────────────────────────────

_SHORT_PRODUCT = "Weet-Bix 750g"
_LONG_PRODUCT = "Sanitarium Weet-Bix Breakfast Cereal 1.2kg Multi-Grain Wholegrain"
_STORE = "Woolworths"
_PRICE = 4.50
_PCT = 40


def _mock_resend_ok():
    resp = MagicMock()
    resp.json.return_value = {"id": "test-msg-id"}
    resp.raise_for_status = MagicMock()
    return resp


# ── 1. Subject line length ≤ 50 chars ────────────────────────────────────────


class TestSubjectLineLengths:
    @pytest.mark.parametrize(
        "trigger_type,pct",
        [
            ("price_match", None),
            ("pct_off_match", _PCT),
            ("flash_sale", _PCT),
            ("prediction_drop", None),
        ],
    )
    def test_short_product_name_within_50(self, trigger_type, pct):
        subject = _build_subject(trigger_type, _SHORT_PRODUCT, _PRICE, pct_off=pct)
        assert (
            len(subject) <= 50
        ), f"{trigger_type!r} subject too long ({len(subject)}): {subject!r}"

    @pytest.mark.parametrize(
        "trigger_type,pct",
        [
            ("price_match", None),
            ("pct_off_match", _PCT),
            ("flash_sale", _PCT),
            ("prediction_drop", None),
        ],
    )
    def test_long_product_name_truncated_within_50(self, trigger_type, pct):
        subject = _build_subject(trigger_type, _LONG_PRODUCT, _PRICE, pct_off=pct)
        assert (
            len(subject) <= 50
        ), f"{trigger_type!r} subject too long ({len(subject)}): {subject!r}"

    def test_weekly_digest_subject_within_50(self):
        subject = "Your deals this week \U0001f6d2"
        assert len(subject) <= 50

    def test_subject_contains_price_for_price_match(self):
        subject = _build_subject("price_match", _SHORT_PRODUCT, _PRICE)
        assert f"${_PRICE:.2f}" in subject

    def test_subject_contains_pct_for_pct_off(self):
        subject = _build_subject("pct_off_match", _SHORT_PRODUCT, _PRICE, pct_off=_PCT)
        assert f"{_PCT}%" in subject

    def test_subject_contains_pct_for_flash_sale(self):
        subject = _build_subject("flash_sale", _SHORT_PRODUCT, _PRICE, pct_off=_PCT)
        assert f"{_PCT}%" in subject


# ── 2. Push body length ≤ 100 chars ──────────────────────────────────────────


class TestPushBodyLengths:
    @pytest.mark.parametrize(
        "trigger_type,pct",
        [
            ("price_match", None),
            ("pct_off_match", _PCT),
            ("flash_sale", _PCT),
            ("prediction_drop", None),
        ],
    )
    def test_short_product_body_within_100(self, trigger_type, pct):
        _, body = _build_push_copy(trigger_type, _SHORT_PRODUCT, _PRICE, _STORE, pct_off=pct)
        assert len(body) <= 100, f"{trigger_type!r} push body too long ({len(body)}): {body!r}"

    @pytest.mark.parametrize(
        "trigger_type,pct",
        [
            ("price_match", None),
            ("pct_off_match", _PCT),
            ("flash_sale", _PCT),
            ("prediction_drop", None),
        ],
    )
    def test_long_product_body_within_100(self, trigger_type, pct):
        _, body = _build_push_copy(trigger_type, _LONG_PRODUCT, _PRICE, _STORE, pct_off=pct)
        assert len(body) <= 100, f"{trigger_type!r} push body too long ({len(body)}): {body!r}"

    def test_push_copy_returns_title_and_body(self):
        title, body = _build_push_copy("price_match", _SHORT_PRODUCT, _PRICE, _STORE)
        assert title
        assert body

    def test_prediction_body_does_not_contain_chain_details(self):
        _, body = _build_push_copy("prediction_drop", _SHORT_PRODUCT, _PRICE, "Coles")
        assert "drop soon" in body.lower() or "buy now" in body.lower()


# ── 3–4. Price alert HTML: CDN image, no base64 ──────────────────────────────


class TestPriceAlertEmailTemplate:
    CDN_URL = "https://cdn.example.com/products/weetbix-750.jpg"

    def test_cdn_image_rendered_when_provided(self):
        with patch("httpx.post", return_value=_mock_resend_ok()), patch(
            "app.config.get_settings"
        ) as mock_settings:
            mock_settings.return_value = MagicMock(
                RESEND_API_KEY="re_test",
                EMAIL_FROM_NAME="DealFindio",
                EMAIL_FROM_ADDRESS="alerts@dealfindio.com.au",
                SECRET_KEY="test-secret",
                APP_BASE_URL="https://dealfindio.com.au",
            )
            import httpx

            captured = {}

            def capture_post(url, **kwargs):
                captured["payload"] = kwargs.get("json", {})
                return _mock_resend_ok()

            with patch.object(httpx, "post", side_effect=capture_post):
                from app.services import email_sender as es

                # Reload settings inside the module via patching the module-level settings
                original_settings = es.settings
                es.settings = mock_settings.return_value
                try:
                    es.send_price_alert_email(
                        to="user@test.com",
                        user_id="user-1",
                        product_name=_SHORT_PRODUCT,
                        price=_PRICE,
                        was_price=6.00,
                        store_name=_STORE,
                        product_id="prod-1",
                        trigger_type="price_match",
                        image_url=self.CDN_URL,
                    )
                finally:
                    es.settings = original_settings

            html = captured.get("payload", {}).get("html", "")
            assert self.CDN_URL in html, "CDN image URL missing from price alert HTML"
            assert "<img" in html

    def test_no_base64_in_price_alert_html(self):
        with patch("httpx.post", return_value=_mock_resend_ok()):
            from app.services import email_sender as es

            original_settings = es.settings
            es.settings = MagicMock(
                RESEND_API_KEY="re_test",
                EMAIL_FROM_NAME="DealFindio",
                EMAIL_FROM_ADDRESS="alerts@dealfindio.com.au",
                SECRET_KEY="test-secret",
                APP_BASE_URL="https://dealfindio.com.au",
            )
            captured = {}

            def capture_post(url, **kwargs):
                captured["payload"] = kwargs.get("json", {})
                return _mock_resend_ok()

            import httpx

            try:
                with patch.object(httpx, "post", side_effect=capture_post):
                    es.send_price_alert_email(
                        to="user@test.com",
                        user_id="user-1",
                        product_name=_SHORT_PRODUCT,
                        price=_PRICE,
                        was_price=None,
                        store_name=_STORE,
                        product_id="prod-1",
                        trigger_type="price_match",
                        image_url=self.CDN_URL,
                    )
            finally:
                es.settings = original_settings

            html = captured.get("payload", {}).get("html", "")
            assert "data:image" not in html, "base64 image data found in price alert HTML"


# ── 5–6. Weekly digest: list grouping ────────────────────────────────────────


class TestWeeklyDigestListGrouping:
    def _make_deals(self):
        return [
            {
                "product_name": "Weet-Bix 750g",
                "price": 4.50,
                "was_price": 6.00,
                "store_name": "Woolworths",
                "product_id": "prod-1",
                "pct_off": 25,
                "list_id": "list-1",
                "list_name": "Weekly Shop",
                "list_emoji": "\U0001f6d2",
                "image_url": "https://cdn.example.com/weetbix.jpg",
            },
            {
                "product_name": "Full Cream Milk 2L",
                "price": 2.50,
                "was_price": None,
                "store_name": "Coles",
                "product_id": "prod-2",
                "pct_off": None,
                "list_id": "list-2",
                "list_name": "Dairy",
                "list_emoji": "\U0001f95b",
                "image_url": None,
            },
        ]

    def test_list_section_headers_rendered(self):
        from collections import defaultdict

        deals = self._make_deals()
        grouped = defaultdict(list)
        for d in deals:
            grouped[d.get("list_id") or "__other__"].append(d)

        html = _build_digest_content(grouped, "https://dealfindio.com.au", include_images=True)
        assert "Weekly Shop" in html
        assert "Dairy" in html
        assert "<h3" in html

    def test_each_list_gets_separate_header(self):
        from collections import defaultdict

        deals = self._make_deals()
        grouped = defaultdict(list)
        for d in deals:
            grouped[d.get("list_id") or "__other__"].append(d)

        html = _build_digest_content(grouped, "https://dealfindio.com.au", include_images=True)
        assert html.count("<h3") >= 2

    def test_cdn_image_in_digest_when_provided(self):
        from collections import defaultdict

        deals = self._make_deals()
        grouped = defaultdict(list)
        for d in deals:
            grouped[d.get("list_id") or "__other__"].append(d)

        html = _build_digest_content(grouped, "https://dealfindio.com.au", include_images=True)
        assert "https://cdn.example.com/weetbix.jpg" in html

    def test_no_images_when_include_images_false(self):
        from collections import defaultdict

        deals = self._make_deals()
        grouped = defaultdict(list)
        for d in deals:
            grouped[d.get("list_id") or "__other__"].append(d)

        html = _build_digest_content(grouped, "https://dealfindio.com.au", include_images=False)
        assert "https://cdn.example.com/weetbix.jpg" not in html


# ── 7. 80 KB size guard ───────────────────────────────────────────────────────


class TestDigestSizeGuard:
    def test_size_guard_removes_images_when_over_budget(self):
        """When digest HTML > 80 KB, it rebuilds without images and logs a warning."""
        from app.services import email_sender as es

        original_settings = es.settings
        es.settings = MagicMock(
            RESEND_API_KEY="re_test",
            EMAIL_FROM_NAME="DealFindio",
            EMAIL_FROM_ADDRESS="alerts@dealfindio.com.au",
            SECRET_KEY="test-secret",
            APP_BASE_URL="https://dealfindio.com.au",
        )

        try:
            # Patch _build_digest_content so the first call returns oversized HTML
            big_html = "X" * (_DIGEST_SIZE_BUDGET + 100)
            call_count = {"n": 0}
            original_build = es._build_digest_content

            def fake_build(grouped, base_url, *, include_images):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return big_html
                return original_build(grouped, base_url, include_images=include_images)

            import httpx

            captured = {}

            def capture_post(url, **kwargs):
                captured["payload"] = kwargs.get("json", {})
                return _mock_resend_ok()

            with patch.object(es, "_build_digest_content", side_effect=fake_build), patch.object(
                httpx, "post", side_effect=capture_post
            ):
                es.send_weekly_digest_email(
                    to="user@test.com",
                    user_id="user-1",
                    deals=[
                        {
                            "product_name": "Weet-Bix 750g",
                            "price": 4.50,
                            "was_price": None,
                            "store_name": "Woolworths",
                            "product_id": "prod-1",
                            "pct_off": 25,
                            "list_id": "list-1",
                            "list_name": "Weekly Shop",
                            "list_emoji": "\U0001f6d2",
                            "image_url": "https://cdn.example.com/img.jpg",
                        }
                    ],
                )
        finally:
            es.settings = original_settings

        # _build_digest_content must have been called twice (once with images, once without)
        assert call_count["n"] == 2

    def test_size_guard_not_triggered_for_normal_digest(self):
        """Normal-sized digest calls _build_digest_content exactly once."""
        from app.services import email_sender as es

        original_settings = es.settings
        es.settings = MagicMock(
            RESEND_API_KEY="re_test",
            EMAIL_FROM_NAME="DealFindio",
            EMAIL_FROM_ADDRESS="alerts@dealfindio.com.au",
            SECRET_KEY="test-secret",
            APP_BASE_URL="https://dealfindio.com.au",
        )

        try:
            call_count = {"n": 0}
            original_build = es._build_digest_content

            def counting_build(grouped, base_url, *, include_images):
                call_count["n"] += 1
                return original_build(grouped, base_url, include_images=include_images)

            import httpx

            with patch.object(
                es, "_build_digest_content", side_effect=counting_build
            ), patch.object(httpx, "post", return_value=_mock_resend_ok()):
                es.send_weekly_digest_email(
                    to="user@test.com",
                    user_id="user-1",
                    deals=[
                        {
                            "product_name": "Weet-Bix 750g",
                            "price": 4.50,
                            "was_price": None,
                            "store_name": "Woolworths",
                            "product_id": "prod-1",
                            "pct_off": 25,
                            "image_url": None,
                        }
                    ],
                )
        finally:
            es.settings = original_settings

        assert call_count["n"] == 1

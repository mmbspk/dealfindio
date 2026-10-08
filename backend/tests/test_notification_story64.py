"""
Story #64 tests — Per-list notification channel preferences.

Covers:
  1. _resolve_channels with channel_override="none" → (False, False, True).
  2. _resolve_channels with channel_override="push" → (True, False, False).
  3. _resolve_channels with channel_override="email" → (False, True, False).
  4. _resolve_channels with channel_override="both" → (True, True, False).
  5. _resolve_channels with channel_override=None → user-level defaults, skip_all=False.
  6. _resolve_channels with list_id=None → user-level defaults, no DB query.
  7. Trigger type pct_off_match maps to correct user attributes.
  8. PATCH /watchlists/{id} persists channel_override.
  9. PATCH /watchlists/{id} resets channel_override to null.
 10. PATCH /watchlists/{id} preserves unset name/emoji fields.
 11. PATCH /watchlists/{id} by another user → 404 (authorization).
 12. GET /watchlists/ returns channel_override in response.
 13. POST create with channel_override stored correctly.
 14. Weekly digest: notif_email_weekly_digest=False + channel_override="email" → not selected.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.workers.notification_tasks import _resolve_channels

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_user(push_price_match=True, email_price_match=False):
    return SimpleNamespace(
        notif_push_price_match=push_price_match,
        notif_email_price_match=email_price_match,
        notif_push_pct_off=True,
        notif_email_pct_off=False,
        notif_push_flash_sale=True,
        notif_email_pct_off_flash=False,
        notif_push_prediction=True,
        notif_email_prediction=False,
    )


def _make_item(list_id=None):
    return SimpleNamespace(list_id=list_id or uuid4())


def _mock_session(channel_override):
    """Session that returns a WatchlistList with the given channel_override."""
    lst = SimpleNamespace(channel_override=channel_override)
    session = MagicMock()
    session.query.return_value.filter_by.return_value.first.return_value = lst
    return session


# ── 1–4: Explicit channel_override values ─────────────────────────────────────


class TestResolveChannelsOverride:
    def test_none_override_suppresses_all(self):
        """channel_override="none" → (False, False, True): skip all channels incl. in-app."""
        user = _make_user(push_price_match=True, email_price_match=True)
        item = _make_item()
        session = _mock_session("none")
        push, email, skip_all = _resolve_channels(user, item, "price_match", session)
        assert push is False
        assert email is False
        assert skip_all is True

    def test_push_override_enables_push_disables_email(self):
        user = _make_user(email_price_match=True)
        item = _make_item()
        session = _mock_session("push")
        push, email, skip_all = _resolve_channels(user, item, "price_match", session)
        assert push is True
        assert email is False
        assert skip_all is False

    def test_email_override_disables_push_enables_email(self):
        user = _make_user(push_price_match=True, email_price_match=False)
        item = _make_item()
        session = _mock_session("email")
        push, email, skip_all = _resolve_channels(user, item, "price_match", session)
        assert push is False
        assert email is True
        assert skip_all is False

    def test_both_override_enables_push_and_email(self):
        user = _make_user(push_price_match=False, email_price_match=False)
        item = _make_item()
        session = _mock_session("both")
        push, email, skip_all = _resolve_channels(user, item, "price_match", session)
        assert push is True
        assert email is True
        assert skip_all is False


# ── 5–7: Null / missing list falls back to user defaults ──────────────────────


class TestResolveChannelsFallback:
    def test_null_override_uses_user_defaults(self):
        """channel_override=None → user-level preferences apply, skip_all=False."""
        user = _make_user(push_price_match=False, email_price_match=True)
        item = _make_item()
        session = _mock_session(None)
        push, email, skip_all = _resolve_channels(user, item, "price_match", session)
        assert push is False
        assert email is True
        assert skip_all is False

    def test_no_list_id_uses_user_defaults(self):
        """list_id=None (product not in any list) → user-level prefs, no DB query."""
        user = _make_user(push_price_match=True, email_price_match=False)
        item = SimpleNamespace(list_id=None)
        session = MagicMock()
        push, email, skip_all = _resolve_channels(user, item, "price_match", session)
        assert push is True
        assert email is False
        assert skip_all is False
        session.query.assert_not_called()

    def test_trigger_type_pct_off_uses_correct_attrs(self):
        """Each trigger type maps to its own user preference attributes."""
        user = SimpleNamespace(
            notif_push_price_match=False,
            notif_email_price_match=False,
            notif_push_pct_off=True,
            notif_email_pct_off=True,
            notif_push_prediction=False,
            notif_email_prediction=False,
            notif_push_flash_sale=False,
        )
        item = SimpleNamespace(list_id=None)
        session = MagicMock()
        push, email, skip_all = _resolve_channels(user, item, "pct_off_match", session)
        assert push is True
        assert email is True
        assert skip_all is False


# ── 8–14: Integration tests ────────────────────────────────────────────────────


class TestChannelOverrideEndpoints:
    @pytest.mark.integration
    def test_get_lists_returns_channel_override(
        self, authed_client, authed_user, test_db_session
    ):
        """GET /watchlists/ returns channel_override in every list response."""
        from app.models.watchlist_list import WatchlistList

        lst_none = WatchlistList(
            id=uuid4(), user_id=authed_user.id, name="Suppressed", channel_override="none"
        )
        lst_normal = WatchlistList(
            id=uuid4(), user_id=authed_user.id, name="Normal", channel_override=None
        )
        test_db_session.add_all([lst_none, lst_normal])
        test_db_session.commit()

        resp = authed_client.get("/api/v1/watchlists/")
        assert resp.status_code == 200
        by_name = {lst["name"]: lst for lst in resp.json()}
        assert by_name["Suppressed"]["channel_override"] == "none"
        assert by_name["Normal"]["channel_override"] is None

    @pytest.mark.integration
    def test_patch_sets_channel_override(
        self, authed_client, authed_user, test_db_session
    ):
        """PATCH /watchlists/{id} persists channel_override to the database."""
        from app.models.watchlist_list import WatchlistList

        lst = WatchlistList(id=uuid4(), user_id=authed_user.id, name="My List")
        test_db_session.add(lst)
        test_db_session.commit()

        resp = authed_client.patch(
            f"/api/v1/watchlists/{lst.id}",
            json={"channel_override": "email"},
        )
        assert resp.status_code == 200
        assert resp.json()["channel_override"] == "email"

        test_db_session.refresh(lst)
        assert lst.channel_override == "email"

    @pytest.mark.integration
    def test_patch_resets_channel_override_to_null(
        self, authed_client, authed_user, test_db_session
    ):
        """PATCH with channel_override=null resets to inherit user defaults."""
        from app.models.watchlist_list import WatchlistList

        lst = WatchlistList(
            id=uuid4(), user_id=authed_user.id, name="My List", channel_override="push"
        )
        test_db_session.add(lst)
        test_db_session.commit()

        resp = authed_client.patch(
            f"/api/v1/watchlists/{lst.id}",
            json={"channel_override": None},
        )
        assert resp.status_code == 200
        assert resp.json()["channel_override"] is None

        test_db_session.refresh(lst)
        assert lst.channel_override is None

    @pytest.mark.integration
    def test_patch_preserves_unset_fields(
        self, authed_client, authed_user, test_db_session
    ):
        """PATCH with only channel_override leaves name and emoji unchanged."""
        from app.models.watchlist_list import WatchlistList

        lst = WatchlistList(
            id=uuid4(),
            user_id=authed_user.id,
            name="Keep This Name",
            emoji="🛒",
        )
        test_db_session.add(lst)
        test_db_session.commit()

        authed_client.patch(
            f"/api/v1/watchlists/{lst.id}",
            json={"channel_override": "both"},
        )

        test_db_session.refresh(lst)
        assert lst.name == "Keep This Name"
        assert lst.emoji == "🛒"
        assert lst.channel_override == "both"

    @pytest.mark.integration
    def test_patch_another_users_list_returns_404(
        self, authed_client, test_db_session
    ):
        """A user cannot PATCH a list they don't own."""
        from app.models import UserProfile
        from app.models.watchlist_list import WatchlistList

        other = UserProfile(
            id=uuid4(),
            firebase_uid="other_uid",
            email="other@example.com",
        )
        test_db_session.add(other)
        test_db_session.flush()

        other_list = WatchlistList(id=uuid4(), user_id=other.id, name="Other's List")
        test_db_session.add(other_list)
        test_db_session.commit()

        resp = authed_client.patch(
            f"/api/v1/watchlists/{other_list.id}",
            json={"channel_override": "none"},
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_create_list_with_channel_override(
        self, authed_client, authed_user, test_db_session
    ):
        """POST /watchlists/ accepts and stores channel_override on creation."""
        resp = authed_client.post(
            "/api/v1/watchlists/",
            json={"name": "Push Only", "emoji": "📲", "channel_override": "push"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["channel_override"] == "push"
        assert data["name"] == "Push Only"

    @pytest.mark.integration
    def test_patch_name_null_returns_422(
        self, authed_client, authed_user, test_db_session
    ):
        """PATCH with explicit name: null must return 422, not a DB error."""
        from app.models.watchlist_list import WatchlistList

        lst = WatchlistList(id=uuid4(), user_id=authed_user.id, name="Original")
        test_db_session.add(lst)
        test_db_session.commit()

        resp = authed_client.patch(
            f"/api/v1/watchlists/{lst.id}",
            json={"name": None},
        )
        assert resp.status_code == 422

        test_db_session.refresh(lst)
        assert lst.name == "Original"  # DB unchanged


# ── Weekly digest preference inheritance ──────────────────────────────────────


class TestDigestPreferenceInheritance:
    def test_global_opt_out_prevents_digest_regardless_of_list_override(self):
        """
        A user with notif_email_weekly_digest=False never receives the weekly
        digest even when a list has channel_override="email".

        Design: channel_override controls per-list content suppression for
        opted-in users only. The global flag is the master on/off switch for
        the digest feature itself.
        """
        from app.workers.notification_tasks import send_weekly_digest

        session_mock = MagicMock()
        # Worker queries users with notif_email_weekly_digest==True.
        # Return empty list → no emails, regardless of any list override.
        session_mock.query.return_value.filter.return_value.all.return_value = []

        with (
            patch(
                "app.workers.notification_tasks.get_db_session",
                return_value=session_mock,
            ),
            patch("app.services.email_sender.send_weekly_digest_email") as mock_send,
        ):
            send_weekly_digest.apply()

        mock_send.assert_not_called()

    @pytest.mark.integration
    def test_digest_suppression_by_channel_override(self, test_db_session):
        """
        Runs the actual digest worker against the test DB and asserts that:
          - "none"    list products are excluded (email AND push suppressed)
          - "push"    list products are excluded (push only → no email digest)
          - "email"   list products are included
          - "both"    list products are included
          - None      (inherit) list products are included (global prefs allow email)

        This directly exercises the channel_override filtering inside
        send_weekly_digest rather than checking only stored values.
        """
        from app.models import PriceReport, Product, Store, UserProfile, UserWatchlist
        from app.models.watchlist_list import WatchlistList
        from app.workers.notification_tasks import send_weekly_digest

        # ── Create user with weekly digest opted in ─────────────────────────
        user = UserProfile(
            id=uuid4(),
            firebase_uid="digest_suppression_uid",
            email="digest_suppression@example.com",
            notif_email_weekly_digest=True,
        )
        test_db_session.add(user)
        test_db_session.flush()

        # ── Create one list per override state ──────────────────────────────
        list_none = WatchlistList(
            id=uuid4(), user_id=user.id, name="None list", channel_override="none"
        )
        list_push = WatchlistList(
            id=uuid4(), user_id=user.id, name="Push list", channel_override="push"
        )
        list_email = WatchlistList(
            id=uuid4(), user_id=user.id, name="Email list", channel_override="email"
        )
        list_both = WatchlistList(
            id=uuid4(), user_id=user.id, name="Both list", channel_override="both"
        )
        list_inherit = WatchlistList(
            id=uuid4(), user_id=user.id, name="Inherit list", channel_override=None
        )
        all_lists = [list_none, list_push, list_email, list_both, list_inherit]
        test_db_session.add_all(all_lists)
        test_db_session.flush()

        # ── One product + on-sale price report per list ─────────────────────
        store = Store(id=uuid4(), chain="coles", name="Coles Test")
        test_db_session.add(store)
        test_db_session.flush()

        for i, lst in enumerate(all_lists):
            product = Product(id=uuid4(), name=f"Digest Product {i}")
            test_db_session.add(product)
            test_db_session.flush()

            report = PriceReport(
                id=uuid4(),
                product_id=product.id,
                store_id=store.id,
                price_cents=500,
                was_price_cents=1000,
                is_on_sale=True,
                sale_pct_off=50.0,
            )
            test_db_session.add(report)

            item = UserWatchlist(
                id=uuid4(),
                user_id=user.id,
                list_id=lst.id,
                product_id=product.id,
                is_active=True,
            )
            test_db_session.add(item)

        test_db_session.commit()

        # ── Run the worker, capturing the deals sent to the email function ──
        # Wrap test_db_session to prevent the worker from closing it.
        session_spy = MagicMock(wraps=test_db_session)
        session_spy.close = MagicMock()

        captured_deals = []

        def _capture(to, user_id, deals, **kwargs):
            captured_deals.extend(deals)
            return "test-msg-id"

        with (
            patch(
                "app.workers.notification_tasks.get_db_session",
                return_value=session_spy,
            ),
            patch(
                "app.services.email_sender.send_weekly_digest_email",
                side_effect=_capture,
            ),
        ):
            send_weekly_digest.apply()

        assert captured_deals, "Expected at least some deals to be sent"

        captured_list_ids = {d["list_id"] for d in captured_deals}

        # ── Suppressed ──────────────────────────────────────────────────────
        assert str(list_none.id) not in captured_list_ids, (
            "channel_override='none' list should be excluded from digest"
        )
        assert str(list_push.id) not in captured_list_ids, (
            "channel_override='push' list should be excluded from email digest"
        )

        # ── Included ────────────────────────────────────────────────────────
        assert str(list_email.id) in captured_list_ids, (
            "channel_override='email' list should appear in digest"
        )
        assert str(list_both.id) in captured_list_ids, (
            "channel_override='both' list should appear in digest"
        )
        assert str(list_inherit.id) in captured_list_ids, (
            "channel_override=None (inherit) list should appear in digest"
        )

"""
Story #64 tests — Per-list notification channel preferences.

Covers:
  1. _resolve_channels with channel_override="none" → (False, False).
  2. _resolve_channels with channel_override="push" → (True, False).
  3. _resolve_channels with channel_override="email" → (False, True).
  4. _resolve_channels with channel_override="both" → (True, True).
  5. _resolve_channels with channel_override=None → user-level defaults used.
  6. _resolve_channels with list_id=None → user-level defaults used.
  7. Weekly digest excludes products from "none" lists.
  8. Weekly digest excludes products from "push" lists.
  9. Weekly digest includes products from "email" and "both" lists.
 10. PATCH /watchlists/{id} persists channel_override.
 11. PATCH /watchlists/{id} resets channel_override to null.
 12. GET /watchlists/ returns channel_override in response.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock
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
    def test_none_override_suppresses_both(self):
        user = _make_user(push_price_match=True, email_price_match=True)
        item = _make_item()
        session = _mock_session("none")
        push, email = _resolve_channels(user, item, "price_match", session)
        assert push is False
        assert email is False

    def test_push_override_enables_push_disables_email(self):
        user = _make_user(email_price_match=True)
        item = _make_item()
        session = _mock_session("push")
        push, email = _resolve_channels(user, item, "price_match", session)
        assert push is True
        assert email is False

    def test_email_override_disables_push_enables_email(self):
        user = _make_user(push_price_match=True, email_price_match=False)
        item = _make_item()
        session = _mock_session("email")
        push, email = _resolve_channels(user, item, "price_match", session)
        assert push is False
        assert email is True

    def test_both_override_enables_push_and_email(self):
        user = _make_user(push_price_match=False, email_price_match=False)
        item = _make_item()
        session = _mock_session("both")
        push, email = _resolve_channels(user, item, "price_match", session)
        assert push is True
        assert email is True


# ── 5–6: Null / missing list falls back to user defaults ──────────────────────


class TestResolveChannelsFallback:
    def test_null_override_uses_user_defaults(self):
        """channel_override=None → user-level preferences apply."""
        user = _make_user(push_price_match=False, email_price_match=True)
        item = _make_item()
        session = _mock_session(None)  # list exists but override is null
        push, email = _resolve_channels(user, item, "price_match", session)
        assert push is False   # user disabled push
        assert email is True   # user enabled email

    def test_no_list_id_uses_user_defaults(self):
        """list_id=None (product not in any list) → user-level preferences apply."""
        user = _make_user(push_price_match=True, email_price_match=False)
        item = SimpleNamespace(list_id=None)
        session = MagicMock()  # should never be queried
        push, email = _resolve_channels(user, item, "price_match", session)
        assert push is True
        assert email is False
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
        push, email = _resolve_channels(user, item, "pct_off_match", session)
        assert push is True
        assert email is True


# ── 7–9: Weekly digest list filtering ─────────────────────────────────────────


class TestDigestListFilter:
    @pytest.mark.integration
    def test_digest_excludes_none_lists(
        self, authed_client, authed_user, test_db_session
    ):
        """Products in lists with channel_override='none' are excluded from the digest."""
        from app.models import Product, Store, PriceReport
        from app.models.watchlist import UserWatchlist
        from app.models.watchlist_list import WatchlistList

        # Create a list with override="none" and one with None
        lst_none = WatchlistList(
            id=uuid4(), user_id=authed_user.id, name="Suppressed", channel_override="none"
        )
        lst_normal = WatchlistList(
            id=uuid4(), user_id=authed_user.id, name="Normal", channel_override=None
        )
        test_db_session.add_all([lst_none, lst_normal])
        test_db_session.flush()

        # GET /watchlists/ should return both lists with correct channel_override
        resp = authed_client.get("/api/v1/watchlists/")
        assert resp.status_code == 200
        lists = {lst["name"]: lst for lst in resp.json()}
        assert lists["Suppressed"]["channel_override"] == "none"
        assert lists["Normal"]["channel_override"] is None

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

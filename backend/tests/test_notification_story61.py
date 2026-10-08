"""
Story #61 regression tests — In-App Unread Count, Push Badge & Deep-Link Routing.

Verifies:
  1. Unread-count endpoint counts only channel='in_app' rows.
  2. Push-only and email-only rows are NOT counted.
  3. Read in-app rows are NOT counted.
  4. read_all marks only in_app rows as read (push/email rows unchanged).
  5. Badge number equals the server-side in_app unread count at push-send time.
  6. Badge resets to 0 after read_all.
  7. notification_id in push payload is set to the in_app row's UUID.
  8. Deleting an unread in_app notification triggers badge sync.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models import Notification, Product, Store, UserProfile
from app.models.watchlist import UserWatchlist
from app.models.watchlist_list import WatchlistList

# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_notif(user_id, channel: str, opened_at=None, db=None):
    n = Notification(
        id=uuid4(),
        user_id=user_id,
        trigger_type="price_match",
        message_title="Test",
        message_body="body",
        channel=channel,
        is_delivered=True,
        opened_at=opened_at,
        sent_at=datetime.now(timezone.utc),
    )
    if db:
        db.add(n)
    return n


def _mock_self():
    return SimpleNamespace(request=SimpleNamespace(id="test-task-id"), max_retries=3)


# ── 1–4: Unread-count endpoint ────────────────────────────────────────────────


class TestUnreadCountEndpoint:
    @pytest.mark.integration
    def test_counts_only_in_app_unread(self, authed_client, authed_user, test_db_session):
        """Unread count returns only unread in_app rows."""
        uid = authed_user.id
        _make_notif(uid, "in_app", db=test_db_session)
        _make_notif(uid, "in_app", db=test_db_session)
        _make_notif(uid, "push", db=test_db_session)   # must NOT count
        _make_notif(uid, "email", db=test_db_session)  # must NOT count
        test_db_session.commit()

        resp = authed_client.get("/api/v1/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    @pytest.mark.integration
    def test_excludes_push_only_rows(self, authed_client, authed_user, test_db_session):
        """Push delivery rows alone produce a count of 0."""
        _make_notif(authed_user.id, "push", db=test_db_session)
        test_db_session.commit()

        resp = authed_client.get("/api/v1/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.integration
    def test_excludes_email_only_rows(self, authed_client, authed_user, test_db_session):
        """Email delivery rows alone produce a count of 0."""
        _make_notif(authed_user.id, "email", db=test_db_session)
        test_db_session.commit()

        resp = authed_client.get("/api/v1/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.integration
    def test_excludes_read_in_app_rows(self, authed_client, authed_user, test_db_session):
        """Already-read in_app rows do not inflate the count."""
        opened = datetime.now(timezone.utc)
        _make_notif(authed_user.id, "in_app", opened_at=opened, db=test_db_session)
        _make_notif(authed_user.id, "in_app", db=test_db_session)  # still unread
        test_db_session.commit()

        resp = authed_client.get("/api/v1/notifications/unread-count")
        assert resp.json()["count"] == 1

    @pytest.mark.integration
    def test_read_all_marks_only_in_app(self, authed_client, authed_user, test_db_session):
        """read_all marks in_app rows read and leaves push/email rows untouched."""
        uid = authed_user.id
        in_app = _make_notif(uid, "in_app", db=test_db_session)
        push = _make_notif(uid, "push", db=test_db_session)
        email = _make_notif(uid, "email", db=test_db_session)
        test_db_session.commit()

        with patch("app.routers.notifications._sync_push_badge", new=AsyncMock()):
            resp = authed_client.put("/api/v1/notifications/read-all")
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1  # only the in_app row

        test_db_session.expire_all()
        assert test_db_session.get(Notification, in_app.id).opened_at is not None
        assert test_db_session.get(Notification, push.id).opened_at is None
        assert test_db_session.get(Notification, email.id).opened_at is None

    @pytest.mark.integration
    def test_unread_count_zero_after_read_all(self, authed_client, authed_user, test_db_session):
        """Unread count drops to 0 after read_all."""
        _make_notif(authed_user.id, "in_app", db=test_db_session)
        _make_notif(authed_user.id, "in_app", db=test_db_session)
        test_db_session.commit()

        with patch("app.routers.notifications._sync_push_badge", new=AsyncMock()):
            authed_client.put("/api/v1/notifications/read-all")

        resp = authed_client.get("/api/v1/notifications/unread-count")
        assert resp.json()["count"] == 0


# ── 5–7: list_notifications defaults to in_app ───────────────────────────────


class TestListNotificationsDefault:
    @pytest.mark.integration
    def test_default_channel_is_in_app(self, authed_client, authed_user, test_db_session):
        """GET /notifications/ without ?channel returns only in_app rows."""
        _make_notif(authed_user.id, "in_app", db=test_db_session)
        _make_notif(authed_user.id, "push", db=test_db_session)
        _make_notif(authed_user.id, "email", db=test_db_session)
        test_db_session.commit()

        resp = authed_client.get("/api/v1/notifications/")
        assert resp.status_code == 200
        channels = {n["channel"] for n in resp.json()}
        assert channels == {"in_app"}

    @pytest.mark.integration
    def test_explicit_channel_push(self, authed_client, authed_user, test_db_session):
        """?channel=push returns only push rows."""
        _make_notif(authed_user.id, "in_app", db=test_db_session)
        _make_notif(authed_user.id, "push", db=test_db_session)
        test_db_session.commit()

        resp = authed_client.get("/api/v1/notifications/?channel=push")
        assert resp.status_code == 200
        assert all(n["channel"] == "push" for n in resp.json())

    @pytest.mark.integration
    def test_channel_none_returns_all(self, authed_client, authed_user, test_db_session):
        """?channel= (empty) returns all channels."""
        _make_notif(authed_user.id, "in_app", db=test_db_session)
        _make_notif(authed_user.id, "push", db=test_db_session)
        _make_notif(authed_user.id, "email", db=test_db_session)
        test_db_session.commit()

        resp = authed_client.get("/api/v1/notifications/?channel=")
        assert resp.status_code == 200
        channels = {n["channel"] for n in resp.json()}
        assert channels == {"in_app", "push", "email"}


# ── 8–9: badge passed to Expo push ───────────────────────────────────────────


class TestPushBadge:
    @pytest.mark.integration
    def test_badge_equals_in_app_unread_at_dispatch(
        self, test_db_session, sample_product: Product, sample_store: Store
    ):
        """
        When check_watchlist_for_price fires, the Expo push receives badge=N
        where N is the in_app unread count AFTER the in_app row was committed.
        """
        from datetime import date

        from app.models import PriceReport
        from app.workers import notification_tasks

        watcher = UserProfile(
            id=uuid4(),
            firebase_uid="badge_watcher",
            email="bw@x.com",
            fcm_token="ExponentPushToken[badge-test-001]",
            reputation_score=1.0,
            contribution_count=0,
        )
        test_db_session.add(watcher)
        test_db_session.flush()

        # Pre-existing unread in_app notification (count will be 2 after new one logged)
        _make_notif(watcher.id, "in_app", db=test_db_session)
        # Pre-existing push row — must NOT affect badge
        _make_notif(watcher.id, "push", db=test_db_session)
        test_db_session.flush()

        lst = WatchlistList(id=uuid4(), user_id=watcher.id, name="Groceries")
        entry = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            list_id=lst.id,
            product_id=sample_product.id,
            target_price_cents=400,
            is_active=True,
        )
        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,
            source="scraper",
            reporter_user_id=None,
            confidence=1.0,
            valid_from=date.today(),
        )
        test_db_session.add_all([lst, entry, report])
        test_db_session.commit()
        report_id = str(report.id)

        captured_badge = {}

        def fake_send_push(user, title, body, data, badge=None):
            captured_badge["badge"] = badge
            return "receipt-001"

        with patch.object(
            notification_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(notification_tasks, "_send_push", side_effect=fake_send_push):
            notification_tasks.check_watchlist_for_price.run(report_id)

        # The in_app row was logged first; badge = 2 (prior unread + new one)
        assert captured_badge.get("badge") == 2

    @pytest.mark.integration
    def test_badge_resets_to_zero_after_read_all(self, authed_client, authed_user, test_db_session):
        """_sync_push_badge is called after read_all."""
        _make_notif(authed_user.id, "in_app", db=test_db_session)
        test_db_session.commit()

        mock_badge = AsyncMock()
        with patch("app.routers.notifications._sync_push_badge", mock_badge):
            resp = authed_client.put("/api/v1/notifications/read-all")
        assert resp.status_code == 200
        assert mock_badge.called

    @pytest.mark.integration
    def test_badge_increments_across_two_watchlist_entries_same_price_report(
        self, test_db_session, sample_product: Product, sample_store: Store
    ):
        """
        A single task run matches two watchlist entries for the same price report.
        The first entry's in_app row is committed before the second entry fires, so
        badge counts accumulate sequentially: entry1 → badge=1, entry2 → badge=2.
        """
        from datetime import date

        from app.models import PriceReport
        from app.workers import notification_tasks

        watcher = UserProfile(
            id=uuid4(),
            firebase_uid="concurrent_watcher",
            email="cw@x.com",
            fcm_token="ExponentPushToken[concurrent-001]",
            reputation_score=1.0,
            contribution_count=0,
        )
        test_db_session.add(watcher)
        test_db_session.flush()

        lst = WatchlistList(id=uuid4(), user_id=watcher.id, name="Test")
        lst2 = WatchlistList(id=uuid4(), user_id=watcher.id, name="Test 2")
        # Two separate watchlist entries in different lists — dedup is keyed on watchlist_id, so each
        # entry can fire independently without the first blocking the second.
        entry1 = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            list_id=lst.id,
            product_id=sample_product.id,
            target_price_cents=500,
            is_active=True,
        )
        entry2 = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            list_id=lst2.id,
            product_id=sample_product.id,
            target_price_cents=500,
            is_active=True,
        )
        test_db_session.add_all([lst, lst2, entry1, entry2])
        test_db_session.commit()

        # Capture plain values before the task's session.close() can detach these objects
        product_id = sample_product.id
        store_id = sample_store.id

        badge_calls = []

        def fake_send_push(user, title, body, data, badge=None):
            badge_calls.append(badge)
            return f"receipt-{len(badge_calls)}"

        # Run 1: fires for entry1 and entry2 (both match); expects badge 1 then 2.
        r = PriceReport(
            product_id=product_id,
            store_id=store_id,
            price_cents=450,
            source="scraper",
            reporter_user_id=None,
            confidence=1.0,
            valid_from=date.today(),
        )
        test_db_session.add(r)
        test_db_session.commit()
        report_id = str(r.id)

        with patch.object(
            notification_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(notification_tasks, "_send_push", side_effect=fake_send_push):
            notification_tasks.check_watchlist_for_price.run(report_id)

        # entry1 fires first (badge=1), entry2 fires second (badge=2)
        assert len(badge_calls) == 2
        assert badge_calls[0] == 1
        assert badge_calls[1] == 2


# ── 10: Push payload deep-link fields ────────────────────────────────────────


class TestPushPayloadDeepLink:
    @pytest.mark.integration
    def test_payload_contains_product_id_and_deep_link(
        self, test_db_session, sample_product: Product, sample_store: Store
    ):
        """Push payload includes product_id, list_id, deep_link, and trigger_type."""
        from datetime import date

        from app.models import PriceReport
        from app.workers import notification_tasks

        watcher = UserProfile(
            id=uuid4(),
            firebase_uid="deeplink_watcher",
            email="dl@x.com",
            fcm_token="ExponentPushToken[deeplink-001]",
            reputation_score=1.0,
            contribution_count=0,
        )
        test_db_session.add(watcher)
        test_db_session.flush()

        lst = WatchlistList(id=uuid4(), user_id=watcher.id, name="Test")
        entry = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            list_id=lst.id,
            product_id=sample_product.id,
            target_price_cents=400,
            is_active=True,
        )
        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,
            source="scraper",
            reporter_user_id=None,
            confidence=1.0,
            valid_from=date.today(),
        )
        test_db_session.add_all([lst, entry, report])
        test_db_session.commit()

        # Capture IDs before the task's session.close() detaches these objects
        expected_product_id = str(sample_product.id)
        expected_list_id = str(lst.id)
        report_id = str(report.id)

        captured: dict = {}

        def fake_send_push(user, title, body, data, badge=None):
            captured.update(data)
            return "receipt-dl"

        with patch.object(
            notification_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(notification_tasks, "_send_push", side_effect=fake_send_push):
            notification_tasks.check_watchlist_for_price.run(report_id)

        assert captured.get("product_id") == expected_product_id
        assert captured.get("trigger_type") in ("price_match", "pct_off_match", "flash_sale")
        assert expected_product_id in captured.get("deep_link", "")
        assert captured.get("deep_link", "").startswith("dealfindio://product/")
        assert captured.get("list_id") == expected_list_id
        # notification_id must be set to the in_app row's UUID (not None)
        assert captured.get("notification_id") is not None

    @pytest.mark.integration
    def test_mark_notification_read_syncs_badge(self, authed_client, authed_user, test_db_session):
        """Marking a single in_app notification as read triggers badge sync."""
        notif = _make_notif(authed_user.id, "in_app", db=test_db_session)
        test_db_session.commit()

        mock_badge = AsyncMock()
        with patch("app.routers.notifications._sync_push_badge", mock_badge):
            resp = authed_client.put(f"/api/v1/notifications/{notif.id}/read")
        assert resp.status_code == 200
        assert mock_badge.called

    @pytest.mark.integration
    def test_mark_push_row_read_does_not_sync_badge(
        self, authed_client, authed_user, test_db_session
    ):
        """Marking a push-channel row as read does NOT trigger badge sync."""
        notif = _make_notif(authed_user.id, "push", db=test_db_session)
        test_db_session.commit()

        mock_badge = AsyncMock()
        with patch("app.routers.notifications._sync_push_badge", mock_badge):
            resp = authed_client.put(f"/api/v1/notifications/{notif.id}/read")
        assert resp.status_code == 200
        assert not mock_badge.called

    @pytest.mark.integration
    def test_delete_unread_in_app_syncs_badge(self, authed_client, authed_user, test_db_session):
        """Deleting an unread in_app notification triggers badge sync."""
        notif = _make_notif(authed_user.id, "in_app", db=test_db_session)
        test_db_session.commit()

        mock_badge = AsyncMock()
        with patch("app.routers.notifications._sync_push_badge", mock_badge):
            resp = authed_client.delete(f"/api/v1/notifications/{notif.id}")
        assert resp.status_code == 204
        assert mock_badge.called

    @pytest.mark.integration
    def test_delete_read_in_app_does_not_sync_badge(self, authed_client, authed_user, test_db_session):
        """Deleting an already-read in_app notification does NOT trigger badge sync."""
        notif = _make_notif(authed_user.id, "in_app", opened_at=datetime.now(timezone.utc), db=test_db_session)
        test_db_session.commit()

        mock_badge = AsyncMock()
        with patch("app.routers.notifications._sync_push_badge", mock_badge):
            resp = authed_client.delete(f"/api/v1/notifications/{notif.id}")
        assert resp.status_code == 204
        assert not mock_badge.called


# ── 11: Badge task lifecycle ──────────────────────────────────────────────────


class TestBadgeDispatch:
    """Unit tests for _fire_badge lifecycle — strong-reference management and error handling."""

    def test_fire_badge_calls_send_expo_badge_async(self):
        """_fire_badge delegates to send_expo_badge_async with the correct token and badge."""
        import asyncio

        from app.routers import notifications as nr

        sent = []

        async def fake_send(token, badge):
            sent.append((token, badge))
            return True

        with patch("app.services.push_sender.send_expo_badge_async", fake_send):
            asyncio.run(nr._fire_badge("ExponentPushToken[test-001]", 3))

        assert sent == [("ExponentPushToken[test-001]", 3)]

    def test_fire_badge_does_not_propagate_expo_failure(self):
        """Expo push failures are caught and logged — never re-raised to the caller."""
        import asyncio

        from app.routers import notifications as nr

        async def failing_send(token, badge):
            raise RuntimeError("Expo API error")

        # Must complete without raising
        with patch("app.services.push_sender.send_expo_badge_async", failing_send):
            asyncio.run(nr._fire_badge("ExponentPushToken[test-001]", 5))

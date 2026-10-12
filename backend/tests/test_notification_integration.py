"""
Story #84: Backend integration tests — notification pipeline coverage gaps.

Covers areas not tested by stories #61–#66:
  1. Full lifecycle: worker → in_app → unread count → mark read → mark-all-read → delete.
  2. Cross-user ownership: notification read/delete by another user → 404.
  3. Invalid channel_override value → 422.
  4. Per-list isolation: user A override="none" does not suppress user B on same product.
  5. Push failure graceful degradation: _send_push returns None → task succeeds, in_app persists.
  6. RFC 8058 subsequent-alert: after unsubscribe, next price alert skips email channel.
  7. Weekly digest empty: opted-in user with no on-sale items → no email sent.
  8. Global prefs all disabled: push=False, email=False → only in_app created.
  9. Push payload completeness: store_id and price_cents present in data dict.
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models import Notification, PriceReport, Product, Store, UserProfile, UserWatchlist
from app.models.watchlist_list import WatchlistList
from app.workers import notification_tasks

# ── helpers ───────────────────────────────────────────────────────────────────


def _seed(db, user):
    """Seed product/store/list/entry/report; returns (report_id_str, list, product, store)."""
    store = Store(id=uuid4(), chain="coles", name=f"Store {uuid4().hex[:6]}")
    product = Product(id=uuid4(), name=f"Product {uuid4().hex[:6]}")
    db.add_all([store, product])
    db.flush()
    lst = WatchlistList(id=uuid4(), user_id=user.id, name="Test List")
    entry = UserWatchlist(
        id=uuid4(),
        user_id=user.id,
        list_id=lst.id,
        product_id=product.id,
        target_price_cents=500,
        is_active=True,
    )
    report = PriceReport(
        id=uuid4(),
        product_id=product.id,
        store_id=store.id,
        price_cents=450,
        source="catalog_coles",
        reporter_user_id=None,
        confidence=1.0,
        valid_from=date.today(),
    )
    db.add_all([lst, entry, report])
    db.commit()
    return str(report.id), lst, product, store


def _run(db, report_id):
    """Run check_watchlist_for_price synchronously; return (mock_push, mock_email, EagerResult)."""
    spy = MagicMock(wraps=db)
    spy.close = MagicMock()
    mock_push = MagicMock(return_value="push-receipt-001")
    mock_email = MagicMock(return_value="msg-001")
    with (
        patch.object(notification_tasks, "get_db_session", return_value=spy),
        patch.object(notification_tasks, "_send_push", mock_push),
        patch("app.services.email_sender.send_price_alert_email", mock_email),
    ):
        result = notification_tasks.check_watchlist_for_price.apply(args=[report_id])
    return mock_push, mock_email, result


# ── 1: Full lifecycle ─────────────────────────────────────────────────────────


@pytest.mark.integration
def test_full_lifecycle(authed_client, authed_user, test_db_session):
    """Complete lifecycle: worker → unread count → mark read → read-all → delete."""
    authed_user.notif_push_price_match = True
    authed_user.notif_email_price_match = False
    test_db_session.commit()

    # Phase 1: create notification, check unread count and list
    report_id, *_ = _seed(test_db_session, authed_user)
    _, _, task_result = _run(test_db_session, report_id)
    assert task_result.get()["status"] == "success"

    resp = authed_client.get("/api/v1/notifications/unread-count")
    assert resp.json()["count"] == 1

    resp = authed_client.get("/api/v1/notifications/")
    assert resp.status_code == 200
    notifs = resp.json()
    assert len(notifs) == 1
    notif_id = notifs[0]["id"]
    assert notifs[0]["opened_at"] is None

    # Phase 2: mark one read
    with patch("app.routers.notifications._sync_push_badge", new=AsyncMock()):
        resp = authed_client.put(f"/api/v1/notifications/{notif_id}/read")
    assert resp.status_code == 200
    assert resp.json()["opened_at"] is not None
    assert authed_client.get("/api/v1/notifications/unread-count").json()["count"] == 0

    # Phase 3: create a second notification, then read-all
    report_id2, *_ = _seed(test_db_session, authed_user)
    _run(test_db_session, report_id2)
    assert authed_client.get("/api/v1/notifications/unread-count").json()["count"] == 1

    with patch("app.routers.notifications._sync_push_badge", new=AsyncMock()):
        resp = authed_client.put("/api/v1/notifications/read-all")
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    assert authed_client.get("/api/v1/notifications/unread-count").json()["count"] == 0

    # Phase 4: delete the first notification (already read — no badge sync)
    resp = authed_client.delete(f"/api/v1/notifications/{notif_id}")
    assert resp.status_code == 204

    remaining_ids = [n["id"] for n in authed_client.get("/api/v1/notifications/").json()]
    assert notif_id not in remaining_ids


# ── 2: Cross-user ownership ───────────────────────────────────────────────────


@pytest.mark.integration
def test_cross_user_read_returns_404(authed_client, test_db_session):
    """Another user's notification cannot be marked read (404)."""
    other = UserProfile(
        id=uuid4(),
        firebase_uid="ownership_read_other",
        email="ownership_read@example.com",
    )
    test_db_session.add(other)
    test_db_session.flush()

    other_notif = Notification(
        id=uuid4(),
        user_id=other.id,
        trigger_type="price_match",
        message_title="Title",
        message_body="Body",
        channel="in_app",
        is_delivered=True,
        sent_at=datetime.now(timezone.utc),
    )
    test_db_session.add(other_notif)
    test_db_session.commit()

    resp = authed_client.put(f"/api/v1/notifications/{other_notif.id}/read")
    assert resp.status_code == 404


@pytest.mark.integration
def test_cross_user_delete_returns_404(authed_client, test_db_session):
    """Another user's notification cannot be deleted (404)."""
    other = UserProfile(
        id=uuid4(),
        firebase_uid="ownership_delete_other",
        email="ownership_delete@example.com",
    )
    test_db_session.add(other)
    test_db_session.flush()

    other_notif = Notification(
        id=uuid4(),
        user_id=other.id,
        trigger_type="price_match",
        message_title="Title",
        message_body="Body",
        channel="in_app",
        is_delivered=True,
        sent_at=datetime.now(timezone.utc),
    )
    test_db_session.add(other_notif)
    test_db_session.commit()

    resp = authed_client.delete(f"/api/v1/notifications/{other_notif.id}")
    assert resp.status_code == 404


# ── 3: Invalid channel_override ───────────────────────────────────────────────


@pytest.mark.integration
def test_invalid_channel_override_rejected(authed_client, authed_user, test_db_session):
    """PATCH with an unrecognized channel_override value returns 422."""
    lst = WatchlistList(id=uuid4(), user_id=authed_user.id, name="Test List")
    test_db_session.add(lst)
    test_db_session.commit()

    resp = authed_client.patch(
        f"/api/v1/watchlist/{lst.id}",
        json={"channel_override": "sms"},
    )
    assert resp.status_code == 422


# ── 4: Per-list isolation ─────────────────────────────────────────────────────


@pytest.mark.integration
def test_per_list_isolation_override_none_does_not_suppress_other_user(test_db_session):
    """User A's channel_override=none does not suppress notifications for user B on the same product."""
    user_a = UserProfile(
        id=uuid4(),
        firebase_uid="isolation_user_a",
        email="isolation_a@example.com",
        fcm_token="ExponentPushToken[iso-a]",
        notif_push_price_match=True,
    )
    user_b = UserProfile(
        id=uuid4(),
        firebase_uid="isolation_user_b",
        email="isolation_b@example.com",
        fcm_token="ExponentPushToken[iso-b]",
        notif_push_price_match=True,
    )
    test_db_session.add_all([user_a, user_b])
    test_db_session.flush()

    store = Store(id=uuid4(), chain="coles", name="Isolation Store")
    product = Product(id=uuid4(), name="Shared Product")
    test_db_session.add_all([store, product])
    test_db_session.flush()

    list_a = WatchlistList(
        id=uuid4(), user_id=user_a.id, name="Suppressed", channel_override="none"
    )
    entry_a = UserWatchlist(
        id=uuid4(),
        user_id=user_a.id,
        list_id=list_a.id,
        product_id=product.id,
        target_price_cents=500,
        is_active=True,
    )
    list_b = WatchlistList(id=uuid4(), user_id=user_b.id, name="Normal")
    entry_b = UserWatchlist(
        id=uuid4(),
        user_id=user_b.id,
        list_id=list_b.id,
        product_id=product.id,
        target_price_cents=500,
        is_active=True,
    )
    report = PriceReport(
        id=uuid4(),
        product_id=product.id,
        store_id=store.id,
        price_cents=450,
        source="catalog_coles",
        reporter_user_id=None,
        confidence=1.0,
        valid_from=date.today(),
    )
    test_db_session.add_all([list_a, entry_a, list_b, entry_b, report])
    test_db_session.commit()

    push_user_ids = []

    spy = MagicMock(wraps=test_db_session)
    spy.close = MagicMock()

    def fake_push(user, title, body, data, badge=None):
        push_user_ids.append(user.id)
        return "receipt"

    with (
        patch.object(notification_tasks, "get_db_session", return_value=spy),
        patch.object(notification_tasks, "_send_push", side_effect=fake_push),
        patch("app.services.email_sender.send_price_alert_email", return_value=None),
    ):
        notification_tasks.check_watchlist_for_price.apply(args=[str(report.id)])

    assert user_b.id in push_user_ids, "user B must receive push"
    assert user_a.id not in push_user_ids, "user A suppressed by channel_override=none"

    assert (
        test_db_session.query(Notification).filter_by(user_id=user_a.id, channel="in_app").count()
    ) == 0, "user A must have no in_app row"
    assert (
        test_db_session.query(Notification).filter_by(user_id=user_b.id, channel="in_app").count()
    ) == 1, "user B must have an in_app row"


# ── 5: Push failure graceful degradation ─────────────────────────────────────


@pytest.mark.integration
def test_push_failure_task_succeeds_in_app_persists(test_db_session):
    """When _send_push returns None (Expo failure), task succeeds and in_app row persists."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="push_fail_user",
        email="pushfail@example.com",
        fcm_token="ExponentPushToken[push-fail]",
        notif_push_price_match=True,
        notif_email_price_match=False,
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id, *_ = _seed(test_db_session, user)

    spy = MagicMock(wraps=test_db_session)
    spy.close = MagicMock()

    with (
        patch.object(notification_tasks, "get_db_session", return_value=spy),
        patch.object(notification_tasks, "_send_push", return_value=None),
        patch("app.services.email_sender.send_price_alert_email", return_value=None),
    ):
        result = notification_tasks.check_watchlist_for_price.apply(args=[report_id])

    # status='no_matches' because notifications_sent counts only delivered push/email, not in_app
    assert result.get()["status"] == "no_matches"
    assert (
        test_db_session.query(Notification).filter_by(user_id=user.id, channel="in_app").count()
    ) == 1, "in_app notification must persist even when push returns None"


# ── 6: RFC 8058 subsequent-alert no email ─────────────────────────────────────


@pytest.mark.integration
def test_unsubscribed_user_skips_email_on_next_alert(test_client, test_db_session):
    """After one-click unsubscribe, a subsequent price alert does not send email."""
    from app.services.email_sender import generate_unsubscribe_token

    user = UserProfile(
        id=uuid4(),
        firebase_uid="rfc8058_subsequent",
        email="subsequent@example.com",
        fcm_token="ExponentPushToken[subsequent]",
        notif_push_price_match=True,
        notif_email_price_match=True,
    )
    test_db_session.add(user)
    test_db_session.commit()

    token = generate_unsubscribe_token(str(user.id), "email_price_match")
    resp = test_client.post(f"/api/v1/notifications/unsubscribe?token={token}")
    assert resp.status_code == 200

    test_db_session.refresh(user)
    assert user.notif_email_price_match is False

    report_id, *_ = _seed(test_db_session, user)
    mock_push, mock_email, task_result = _run(test_db_session, report_id)

    assert task_result.get()["status"] == "success"
    assert mock_push.call_count == 1, "push still fires after email unsubscribe"
    assert mock_email.call_count == 0, "email must not fire after unsubscribe"


# ── 7: Weekly digest — no email when no on-sale items ────────────────────────


@pytest.mark.integration
def test_weekly_digest_empty_no_email(test_db_session):
    """Weekly digest sends no email when opted-in user has no on-sale watchlist items."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="digest_empty_user",
        email="digest_empty@example.com",
        notif_email_weekly_digest=True,
    )
    test_db_session.add(user)
    test_db_session.flush()

    store = Store(id=uuid4(), chain="coles", name="Empty Digest Store")
    product = Product(id=uuid4(), name="Not On Sale Product")
    test_db_session.add_all([store, product])
    test_db_session.flush()

    lst = WatchlistList(id=uuid4(), user_id=user.id, name="My List")
    entry = UserWatchlist(
        id=uuid4(), user_id=user.id, list_id=lst.id, product_id=product.id, is_active=True
    )
    report = PriceReport(
        id=uuid4(),
        product_id=product.id,
        store_id=store.id,
        price_cents=500,
        is_on_sale=False,
        source="catalog_coles",
        reporter_user_id=None,
        confidence=1.0,
        valid_from=date.today(),
    )
    test_db_session.add_all([lst, entry, report])
    test_db_session.commit()

    spy = MagicMock(wraps=test_db_session)
    spy.close = MagicMock()

    with (
        patch.object(notification_tasks, "get_db_session", return_value=spy),
        patch("app.services.email_sender.send_weekly_digest_email") as mock_digest,
    ):
        task_result = notification_tasks.send_weekly_digest.apply()

    assert task_result.get()["emails_sent"] == 0
    assert mock_digest.call_count == 0, "no digest email when no on-sale items exist"


# ── 8: Global prefs all disabled — only in_app created ───────────────────────


@pytest.mark.integration
def test_global_prefs_disabled_creates_only_in_app(test_db_session):
    """User with push=False and email=False still receives an in_app notification."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="prefs_all_off",
        email="alloff@example.com",
        fcm_token="ExponentPushToken[all-off]",
        notif_push_price_match=False,
        notif_email_price_match=False,
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id, *_ = _seed(test_db_session, user)
    mock_push, mock_email, task_result = _run(test_db_session, report_id)

    assert (
        task_result.get()["status"] == "no_matches"
    )  # notifications_sent=0; in_app doesn't increment it
    assert mock_push.call_count == 0, "push disabled globally"
    assert mock_email.call_count == 0, "email disabled globally"
    assert (
        test_db_session.query(Notification).filter_by(user_id=user.id, channel="in_app").count()
    ) == 1, "in_app row must still be created when push/email both disabled"


# ── 9: Push payload completeness ─────────────────────────────────────────────


@pytest.mark.integration
def test_push_payload_includes_store_id_and_price(test_db_session):
    """Push data dict includes store_id and price_cents in addition to the fields tested in story #61."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="payload_complete_user",
        email="payload_complete@example.com",
        fcm_token="ExponentPushToken[payload-complete]",
        notif_push_price_match=True,
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id, lst, product, store = _seed(test_db_session, user)

    captured: dict = {}
    spy = MagicMock(wraps=test_db_session)
    spy.close = MagicMock()

    def fake_push(u, title, body, data, badge=None):
        captured.update(data)
        return "receipt"

    with (
        patch.object(notification_tasks, "get_db_session", return_value=spy),
        patch.object(notification_tasks, "_send_push", side_effect=fake_push),
        patch("app.services.email_sender.send_price_alert_email", return_value=None),
    ):
        notification_tasks.check_watchlist_for_price.apply(args=[report_id])

    assert str(store.id) == captured.get("store_id"), "store_id must be in push payload"
    assert captured.get("price_cents") == "450", "price_cents must be in push payload as string"
    assert str(product.id) == captured.get("product_id")
    assert str(lst.id) == captured.get("list_id")
    assert captured.get("notification_id") is not None
    assert captured.get("deep_link", "").startswith("dealfindio://product/")

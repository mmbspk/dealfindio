"""
Story #66: End-to-end integration tests — full watchlist notification pipeline.

No SQLAlchemy mocking; only Expo push and Resend email HTTP calls are patched.
All tests assert the Celery task's own return value to catch silent failures.

Coverage:
  1. Happy path — in_app + push + email → unread 1 → read → 0.
  2. Quiet hours — push suppressed, email fires, in_app row created.
  3. Dedup (in_app row count) — same trigger twice → one notification row.
  4. Channel override — all five per-list states enforced through actual worker execution.
  5. Dedup (all channels) — push and email also suppressed on second run.
  6. Push payload — notification_id matches in_app UUID; badge equals unread count.
  7. Weekly digest — sends email for opted-in user with on-sale watchlist items.
  8. Unsubscribe — POST RFC 8058 one-click disables the correct email preference.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models import Notification, PriceReport, Product, Store, UserProfile, UserWatchlist
from app.models.watchlist_list import WatchlistList
from app.workers import notification_tasks

# ── helpers ───────────────────────────────────────────────────────────────────


def _seed(db, user):
    """Seed product/store/list/entry/report that triggers price_match for *user*."""
    store = Store(id=uuid4(), chain="coles", name="Coles E2E")
    product = Product(id=uuid4(), name="Vegemite 380g")
    db.add_all([store, product])
    db.flush()
    lst = WatchlistList(id=uuid4(), user_id=user.id, name="Groceries")
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
    return str(report.id), lst


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


# ── Test 1: Happy path ────────────────────────────────────────────────────────


@pytest.mark.integration
def test_e2e_happy_path(authed_client, authed_user, test_db_session):
    """Full pipeline: price drop → in_app + push + email; read clears unread count."""
    authed_user.notif_email_price_match = True
    test_db_session.commit()

    report_id, _ = _seed(test_db_session, authed_user)
    mock_push, mock_email, task_result = _run(test_db_session, report_id)

    assert task_result.get()["status"] == "success"

    in_app = (
        test_db_session.query(Notification)
        .filter_by(user_id=authed_user.id, channel="in_app")
        .all()
    )
    assert len(in_app) == 1
    assert in_app[0].opened_at is None
    assert mock_push.call_count == 1
    assert mock_email.call_count == 1

    resp = authed_client.get("/api/v1/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1

    with patch("app.routers.notifications._sync_push_badge", new=AsyncMock()):
        authed_client.put(f"/api/v1/notifications/{in_app[0].id}/read")
    assert authed_client.get("/api/v1/notifications/unread-count").json()["count"] == 0


# ── Test 2: Quiet hours ───────────────────────────────────────────────────────


@pytest.mark.integration
def test_e2e_quiet_hours(test_db_session):
    """All-day quiet hours: push suppressed, email fires, in_app row created."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="e2e_quiet",
        email="quiet@example.com",
        fcm_token="ExponentPushToken[e2e-quiet]",
        notif_push_price_match=True,
        notif_email_price_match=True,
        notif_quiet_hours_start=0,
        notif_quiet_hours_end=24,
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id, _ = _seed(test_db_session, user)
    mock_push, mock_email, task_result = _run(test_db_session, report_id)

    assert task_result.get()["status"] == "success"
    assert mock_push.call_count == 0, "push suppressed during quiet hours"
    assert mock_email.call_count == 1, "email fires even during quiet hours"
    assert (
        test_db_session.query(Notification).filter_by(user_id=user.id, channel="in_app").count()
    ) == 1


# ── Test 3: Dedup (in_app row count) ─────────────────────────────────────────


@pytest.mark.integration
def test_e2e_dedup(test_db_session):
    """Same trigger twice within 24 h → only one notification row created."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="e2e_dedup",
        email="dedup@example.com",
        fcm_token="ExponentPushToken[e2e-dedup]",
        notif_push_price_match=True,
        notif_email_price_match=False,
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id, _ = _seed(test_db_session, user)
    _, _, r1 = _run(test_db_session, report_id)
    _, _, r2 = _run(test_db_session, report_id)

    assert r1.get()["status"] == "success"
    assert r2.get()["status"] == "no_matches", "dedup must return no_matches on second run"
    assert (
        test_db_session.query(Notification).filter_by(user_id=user.id, channel="in_app").count()
    ) == 1


# ── Test 4: Per-list channel_override (all five states) ──────────────────────


@pytest.mark.integration
@pytest.mark.parametrize(
    "override,exp_push,exp_email,exp_in_app,exp_status",
    [
        (None, 1, 1, 1, "success"),
        ("push", 1, 0, 1, "success"),
        ("email", 0, 1, 1, "success"),
        ("both", 1, 1, 1, "success"),
        ("none", 0, 0, 0, "no_matches"),
    ],
)
def test_e2e_channel_override(
    test_db_session, override, exp_push, exp_email, exp_in_app, exp_status
):
    """Per-list channel_override is enforced through actual worker execution."""
    uid_tag = str(override)
    user = UserProfile(
        id=uuid4(),
        firebase_uid=f"e2e_ov_{uid_tag}",
        email=f"ov_{uid_tag}@example.com",
        fcm_token="ExponentPushToken[override]",
        notif_push_price_match=True,
        notif_email_price_match=True,
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id, lst = _seed(test_db_session, user)
    lst.channel_override = override
    test_db_session.commit()

    mock_push, mock_email, task_result = _run(test_db_session, report_id)

    assert task_result.get()["status"] == exp_status
    assert mock_push.call_count == exp_push
    assert mock_email.call_count == exp_email
    assert (
        test_db_session.query(Notification).filter_by(user_id=user.id, channel="in_app").count()
    ) == exp_in_app


# ── Test 5: Dedup across all channels ────────────────────────────────────────


@pytest.mark.integration
def test_e2e_dedup_all_channels(test_db_session):
    """Dedup prevents push and email from dispatching a second time, not just in_app."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="e2e_dedup_all",
        email="dedup_all@example.com",
        fcm_token="ExponentPushToken[e2e-dedup-all]",
        notif_push_price_match=True,
        notif_email_price_match=True,
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id, _ = _seed(test_db_session, user)
    mp1, me1, r1 = _run(test_db_session, report_id)
    mp2, me2, r2 = _run(test_db_session, report_id)

    assert r1.get()["status"] == "success"
    assert r2.get()["status"] == "no_matches"
    assert mp1.call_count + mp2.call_count == 1, "push must fire only once across two runs"
    assert me1.call_count + me2.call_count == 1, "email must fire only once across two runs"


# ── Test 6: Push payload — notification_id and badge ─────────────────────────


@pytest.mark.integration
def test_e2e_push_payload(test_db_session):
    """Push payload notification_id matches in_app UUID; badge equals current unread count."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="e2e_payload",
        email="payload@example.com",
        fcm_token="ExponentPushToken[e2e-payload]",
        notif_push_price_match=True,
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id, _ = _seed(test_db_session, user)

    captured: dict = {}
    spy = MagicMock(wraps=test_db_session)
    spy.close = MagicMock()

    def fake_push(u, title, body, data, badge=None):
        captured.update({"data": data, "badge": badge})
        return "receipt-001"

    with (
        patch.object(notification_tasks, "get_db_session", return_value=spy),
        patch.object(notification_tasks, "_send_push", side_effect=fake_push),
        patch("app.services.email_sender.send_price_alert_email", return_value=None),
    ):
        notification_tasks.check_watchlist_for_price.apply(args=[report_id])

    in_app = test_db_session.query(Notification).filter_by(user_id=user.id, channel="in_app").one()
    assert captured["data"]["notification_id"] == str(in_app.id)
    assert captured["badge"] == 1


# ── Test 7: Weekly digest — sends email for eligible watchlist items ──────────


@pytest.mark.integration
def test_e2e_weekly_digest_sends(test_db_session):
    """Weekly digest task sends an email with on-sale items for an opted-in user."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="e2e_digest_send",
        email="digest_send@example.com",
        notif_email_weekly_digest=True,
    )
    test_db_session.add(user)
    test_db_session.flush()

    store = Store(id=uuid4(), chain="coles", name="Digest Store")
    product = Product(id=uuid4(), name="Digest Product")
    test_db_session.add_all([store, product])
    test_db_session.flush()

    lst = WatchlistList(id=uuid4(), user_id=user.id, name="Weekly List")
    entry = UserWatchlist(
        id=uuid4(),
        user_id=user.id,
        list_id=lst.id,
        product_id=product.id,
        is_active=True,
    )
    report = PriceReport(
        id=uuid4(),
        product_id=product.id,
        store_id=store.id,
        price_cents=300,
        was_price_cents=600,
        is_on_sale=True,
        sale_pct_off=50.0,
        source="catalog_coles",
        reporter_user_id=None,
        confidence=1.0,
        valid_from=date.today(),
    )
    test_db_session.add_all([lst, entry, report])
    test_db_session.commit()

    captured_kwargs: list[dict] = []

    def _capture(to, user_id, deals, **kw):
        captured_kwargs.append({"to": to, "user_id": user_id, "deals": deals})
        return "msg-digest-001"

    spy = MagicMock(wraps=test_db_session)
    spy.close = MagicMock()
    with (
        patch.object(notification_tasks, "get_db_session", return_value=spy),
        patch("app.services.email_sender.send_weekly_digest_email", side_effect=_capture),
    ):
        task_result = notification_tasks.send_weekly_digest.apply()

    assert task_result.get()["status"] == "success"
    assert len(captured_kwargs) == 1, "digest email must be sent exactly once"
    assert captured_kwargs[0]["to"] == user.email
    assert str(product.id) in {d["product_id"] for d in captured_kwargs[0]["deals"]}


# ── Test 8: One-click unsubscribe (RFC 8058) ──────────────────────────────────


@pytest.mark.integration
def test_e2e_unsubscribe(test_client, test_db_session):
    """POST to unsubscribe endpoint disables the correct email preference."""
    from app.services.email_sender import generate_unsubscribe_token

    user = UserProfile(
        id=uuid4(),
        firebase_uid="e2e_unsub",
        email="unsub@example.com",
        notif_email_price_match=True,
    )
    test_db_session.add(user)
    test_db_session.commit()

    token = generate_unsubscribe_token(str(user.id), "email_price_match")
    resp = test_client.post(f"/api/v1/notifications/unsubscribe?token={token}")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert "unsubscribed" in resp.text.lower()

    test_db_session.refresh(user)
    assert user.notif_email_price_match is False

    bad = test_client.post("/api/v1/notifications/unsubscribe?token=invalid-token")
    assert bad.status_code == 400

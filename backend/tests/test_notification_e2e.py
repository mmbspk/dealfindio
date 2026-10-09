"""
Story #66: End-to-end integration tests — full watchlist notification pipeline.

No SQLAlchemy mocking; only Expo push and Resend email calls are patched.

Test 1  Happy path  — price drop → in_app row + push + email; unread-count 1 → read → 0.
Test 2  Quiet hours — push suppressed, email still fires, in_app row created.
Test 3  Dedup       — same trigger twice within 24 h → only one notification row.
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
    """Seed a product/store/list/entry/report that triggers price_match for *user*."""
    store = Store(id=uuid4(), chain="coles", name="Coles E2E")
    product = Product(id=uuid4(), name="Vegemite 380g")
    db.add_all([store, product])
    db.flush()

    lst = WatchlistList(id=uuid4(), user_id=user.id, name="Groceries")
    entry = UserWatchlist(
        id=uuid4(), user_id=user.id, list_id=lst.id, product_id=product.id,
        target_price_cents=500, is_active=True,
    )
    report = PriceReport(
        id=uuid4(), product_id=product.id, store_id=store.id,
        price_cents=450, source="catalog_coles", reporter_user_id=None,
        confidence=1.0, valid_from=date.today(),
    )
    db.add_all([lst, entry, report])
    db.commit()
    return str(report.id)


def _run(db, report_id):
    """Run check_watchlist_for_price synchronously against *db*; return (mock_push, mock_email)."""
    spy = MagicMock(wraps=db)
    spy.close = MagicMock()  # prevent the task from closing the test session
    mock_push = MagicMock(return_value="push-receipt-001")
    mock_email = MagicMock(return_value="msg-001")
    with (
        patch.object(notification_tasks, "get_db_session", return_value=spy),
        patch.object(notification_tasks, "_send_push", mock_push),
        patch("app.services.email_sender.send_price_alert_email", mock_email),
    ):
        notification_tasks.check_watchlist_for_price.apply(args=[report_id])
    return mock_push, mock_email


# ── Test 1: Happy path ────────────────────────────────────────────────────────


@pytest.mark.integration
def test_e2e_happy_path(authed_client, authed_user, test_db_session):
    """Full pipeline: price drop → in_app + push + email; read clears unread count."""
    authed_user.notif_email_price_match = True  # default is False
    test_db_session.commit()

    report_id = _seed(test_db_session, authed_user)
    mock_push, mock_email = _run(test_db_session, report_id)

    # One in_app notification row, unread
    in_app = (
        test_db_session.query(Notification)
        .filter_by(user_id=authed_user.id, channel="in_app")
        .all()
    )
    assert len(in_app) == 1
    assert in_app[0].opened_at is None

    # Both channels dispatched
    assert mock_push.call_count == 1
    assert mock_email.call_count == 1

    # Unread count endpoint reflects the new row
    resp = authed_client.get("/api/v1/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1

    # Mark read → count drops to 0
    with patch("app.routers.notifications._sync_push_badge", new=AsyncMock()):
        authed_client.put(f"/api/v1/notifications/{in_app[0].id}/read")
    assert authed_client.get("/api/v1/notifications/unread-count").json()["count"] == 0


# ── Test 2: Quiet hours ───────────────────────────────────────────────────────


@pytest.mark.integration
def test_e2e_quiet_hours(test_db_session):
    """All-day quiet hours: push suppressed, email fires, in_app row still created."""
    user = UserProfile(
        id=uuid4(), firebase_uid="e2e_quiet", email="quiet@example.com",
        fcm_token="ExponentPushToken[e2e-quiet]",
        notif_push_price_match=True, notif_email_price_match=True,
        notif_quiet_hours_start=0, notif_quiet_hours_end=24,  # covers all 24 hours
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id = _seed(test_db_session, user)
    mock_push, mock_email = _run(test_db_session, report_id)

    assert mock_push.call_count == 0, "push must be suppressed during quiet hours"
    assert mock_email.call_count == 1, "email must fire even during quiet hours"
    assert (
        test_db_session.query(Notification)
        .filter_by(user_id=user.id, channel="in_app")
        .count()
    ) == 1, "in_app row must be logged during quiet hours"


# ── Test 3: Dedup ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_e2e_dedup(test_db_session):
    """Same price-match trigger fired twice within 24 h → only one notification created."""
    user = UserProfile(
        id=uuid4(), firebase_uid="e2e_dedup", email="dedup@example.com",
        fcm_token="ExponentPushToken[e2e-dedup]",
        notif_push_price_match=True, notif_email_price_match=False,
    )
    test_db_session.add(user)
    test_db_session.flush()

    report_id = _seed(test_db_session, user)
    _run(test_db_session, report_id)   # first run — creates rows
    _run(test_db_session, report_id)   # second run — deduped

    total = (
        test_db_session.query(Notification)
        .filter_by(user_id=user.id, channel="in_app")
        .count()
    )
    assert total == 1, "duplicate trigger within 24 h must be suppressed by dedup"

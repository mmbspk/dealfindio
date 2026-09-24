"""
Unit tests for Celery worker tasks (report validation, notifications).

Celery tasks acquire their own DB session via ``get_db_session`` and reference
``self.request`` (bound task). Tests patch ``get_db_session`` to yield the test
session and invoke the task's underlying function with a mock ``self``, so the
real validation/notification logic runs against the test DB without a broker.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models import PriceReport, Product, Store, UserProfile


def _mock_self():
    """A stand-in for the bound Celery task `self` (has .request.id)."""
    return SimpleNamespace(request=SimpleNamespace(id="test-task-id"), max_retries=2)


class TestValidateCommunityReport:
    """app.workers.report_validator.validate_community_report"""

    def _fn(self):
        # The undecorated function is accessible via .run on the task object.
        from app.workers.report_validator import validate_community_report

        return validate_community_report

    @pytest.mark.integration
    def test_invalid_uuid_rejected(self):
        task = self._fn()
        result = task.run("not-a-uuid")
        assert result["valid"] is False
        assert result["status"] == "rejected"
        assert "invalid_uuid" in result["flags"]

    @pytest.mark.integration
    def test_report_not_found_rejected(self, test_db_session):
        from app.workers import report_validator

        with patch.object(report_validator, "get_db_session", return_value=test_db_session):
            result = self._fn().run(str(uuid4()))
        assert result["valid"] is False
        assert "report_not_found" in result["flags"]

    @pytest.mark.integration
    def test_non_community_report_passes_through(
        self, test_db_session, sample_product: Product, sample_store: Store
    ):
        # A scraper (flyer) report should be auto-validated with confidence 1.0.
        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,
            source="flyer_coles",
            confidence=1.0,
            valid_from=date.today(),
        )
        test_db_session.add(report)
        test_db_session.commit()

        from app.workers import report_validator

        with patch.object(report_validator, "get_db_session", return_value=test_db_session):
            result = self._fn().run(str(report.id))
        assert result["valid"] is True
        assert result["confidence_score"] == 1.0
        assert result["status"] == "validated"


class TestRecalculateReputation:
    """app.workers.report_validator.recalculate_reporter_reputation"""

    def _fn(self):
        from app.workers.report_validator import recalculate_reporter_reputation

        return recalculate_reporter_reputation

    @pytest.mark.integration
    def test_invalid_uuid_returns_error(self):
        result = self._fn().run("not-a-uuid")
        assert result["status"] == "error"

    @pytest.mark.integration
    def test_user_not_found(self, test_db_session):
        from app.workers import report_validator

        with patch.object(report_validator, "get_db_session", return_value=test_db_session):
            result = self._fn().run(str(uuid4()))
        assert result["status"] == "user_not_found"

    @pytest.mark.integration
    def test_new_user_default_reputation(self, test_db_session):
        # A user with zero reports gets the neutral default reputation.
        user = UserProfile(
            id=uuid4(),
            firebase_uid="rep_uid",
            email="rep@example.com",
            reputation_score=1.0,
            contribution_count=0,
        )
        test_db_session.add(user)
        test_db_session.commit()

        from app.workers import report_validator

        with patch.object(report_validator, "get_db_session", return_value=test_db_session):
            result = self._fn().run(str(user.id))
        assert result["status"] in ("success", "completed", "updated") or "new_reputation" in result
        assert result["new_reputation"] >= 0


class TestCheckWatchlistForPrice:
    """app.workers.notification_tasks.check_watchlist_for_price"""

    def _fn(self):
        from app.workers.notification_tasks import check_watchlist_for_price

        return check_watchlist_for_price

    @pytest.mark.integration
    def test_invalid_report_id_handled(self, test_db_session):
        from app.workers import notification_tasks

        with patch.object(notification_tasks, "get_db_session", return_value=test_db_session):
            result = self._fn().run("not-a-uuid")
        # Should not raise; returns a result dict indicating nothing sent.
        assert isinstance(result, dict)

    @pytest.mark.integration
    def test_missing_report_handled(self, test_db_session):
        from app.workers import notification_tasks

        with patch.object(notification_tasks, "get_db_session", return_value=test_db_session):
            result = self._fn().run(str(uuid4()))
        assert isinstance(result, dict)

    @pytest.mark.integration
    def test_price_match_sends_notification(
        self, test_db_session, sample_product: Product, sample_store: Store
    ):
        # Reporter (no notification) and a watcher with a matching target + FCM token.
        reporter = UserProfile(
            id=uuid4(),
            firebase_uid="rep",
            email="rep@x.com",
            reputation_score=2.0,
            contribution_count=1,
        )
        watcher = UserProfile(
            id=uuid4(),
            firebase_uid="watch",
            email="watch@x.com",
            fcm_token="tok-123",
            reputation_score=1.0,
            contribution_count=0,
        )
        test_db_session.add_all([reporter, watcher])
        test_db_session.flush()

        from app.models.watchlist import UserWatchlist

        entry = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            product_id=sample_product.id,
            target_price_cents=400,
            is_active=True,
        )
        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,  # <= 400 -> price match
            source="community",
            reporter_user_id=reporter.id,
            confidence=0.8,
            valid_from=date.today(),
        )
        test_db_session.add_all([entry, report])
        test_db_session.commit()
        # Capture plain values before the task may expire/detach ORM objects.
        report_id = str(report.id)
        watcher_id = watcher.id

        from app.workers import notification_tasks

        # Mock Expo push so no real network call happens.
        with patch.object(
            notification_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(
            notification_tasks, "_send_push", return_value="expo-receipt-id"
        ) as mock_push:
            result = self._fn().run(report_id)

        assert result["watchlists_matched"] >= 1
        assert result["notifications_sent"] >= 1
        mock_push.assert_called()
        # A notification row was logged for the watcher.
        from app.models import Notification

        notes = test_db_session.query(Notification).filter_by(user_id=watcher_id).all()
        assert len(notes) >= 1

    @pytest.mark.integration
    def test_reporter_not_self_notified(
        self, test_db_session, sample_product: Product, sample_store: Store
    ):
        # The reporter watches their own product; they must NOT be notified.
        reporter = UserProfile(
            id=uuid4(),
            firebase_uid="rep2",
            email="rep2@x.com",
            fcm_token="tok-self",
            reputation_score=2.0,
            contribution_count=1,
        )
        test_db_session.add(reporter)
        test_db_session.flush()

        from app.models.watchlist import UserWatchlist

        entry = UserWatchlist(
            id=uuid4(),
            user_id=reporter.id,
            product_id=sample_product.id,
            target_price_cents=400,
            is_active=True,
        )
        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,
            source="community",
            reporter_user_id=reporter.id,
            confidence=0.8,
            valid_from=date.today(),
        )
        test_db_session.add_all([entry, report])
        test_db_session.commit()
        report_id = str(report.id)

        from app.workers import notification_tasks

        with patch.object(
            notification_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(
            notification_tasks, "_send_push", return_value="expo-receipt-id"
        ) as mock_push:
            result = self._fn().run(report_id)

        assert result["notifications_sent"] == 0
        mock_push.assert_not_called()


class TestValidateCommunityReportFullPath:
    """Exercise the full validation body (outlier detection + confidence)."""

    def _fn(self):
        from app.workers.report_validator import validate_community_report

        return validate_community_report

    @pytest.mark.integration
    def test_community_report_gets_validated_with_confidence(
        self, test_db_session, sample_product: Product, sample_store: Store
    ):
        # A reporter plus a small price history so the outlier/confidence
        # calculation runs end to end (not just the early returns).
        reporter = UserProfile(
            id=uuid4(),
            firebase_uid="fullrep",
            email="fullrep@x.com",
            reputation_score=3.0,
            contribution_count=10,
        )
        test_db_session.add(reporter)
        test_db_session.flush()

        # Historical prices around $3.50 so a new $3.50 report is in-range.
        for cents in (340, 350, 360, 355, 345):
            test_db_session.add(
                PriceReport(
                    product_id=sample_product.id,
                    store_id=sample_store.id,
                    price_cents=cents,
                    source="community",
                    reporter_user_id=reporter.id,
                    confidence=0.8,
                    is_verified=True,
                    valid_from=date.today(),
                )
            )
        new_report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,
            source="community",
            reporter_user_id=reporter.id,
            confidence=0.5,
            valid_from=date.today(),
        )
        test_db_session.add(new_report)
        test_db_session.commit()
        report_id = str(new_report.id)

        from app.workers import report_validator

        with patch.object(
            report_validator, "get_db_session", return_value=test_db_session
        ), patch.object(report_validator, "celery_app", MagicMock()):
            result = self._fn().run(report_id)

        assert "confidence_score" in result
        assert 0.0 <= result["confidence_score"] <= 1.0
        assert result["status"] in ("validated", "flagged", "pending_review")


class TestRunFlyerScrape:
    """app.workers.scraper_tasks.run_flyer_scrape"""

    def _fn(self):
        from app.workers.scraper_tasks import run_flyer_scrape

        return run_flyer_scrape

    @pytest.mark.integration
    def test_invalid_chain_raises(self, test_db_session):
        from app.workers import scraper_tasks

        with patch.object(
            scraper_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(scraper_tasks, "get_redis", return_value=MagicMock()):
            # Invalid chain -> ValueError -> task triggers a retry/raise
            # (no broker in tests, so it surfaces as an exception, not a result).
            with pytest.raises(Exception):
                self._fn().run("not_a_real_chain")


class TestNotifyPredictionDrops:
    """app.workers.notification_tasks.notify_prediction_drops"""

    def _fn(self):
        from app.workers.notification_tasks import notify_prediction_drops

        return notify_prediction_drops

    @pytest.mark.integration
    def test_no_watchlists_returns_success(self, test_db_session):
        from app.workers import notification_tasks

        with patch.object(
            notification_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(notification_tasks, "_send_push", return_value="expo-receipt-id"):
            result = self._fn().run()

        assert result["status"] == "success"
        assert result["notifications_sent"] == 0
        assert result["watchlists_matched"] == 0

    @pytest.mark.integration
    def test_sends_prediction_drop_notification(self, test_db_session, sample_product: Product):
        from datetime import datetime, timedelta, timezone

        from app.models.prediction import PricePrediction
        from app.models.watchlist import UserWatchlist

        watcher = UserProfile(
            id=uuid4(),
            firebase_uid="predwatcher",
            email="pred@x.com",
            fcm_token="tok-pred",
            reputation_score=1.0,
            contribution_count=0,
        )
        test_db_session.add(watcher)
        test_db_session.flush()

        entry = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            product_id=sample_product.id,
            alert_on_prediction=True,
            is_active=True,
        )
        test_db_session.add(entry)
        test_db_session.flush()

        future_week = datetime.now(timezone.utc) + timedelta(days=7)
        pred = PricePrediction(
            id=uuid4(),
            product_id=sample_product.id,
            chain="coles",
            predicted_price_cents=350,
            prediction_week=future_week,
            is_likely_sale=True,
            model_version="test-v1",
        )
        test_db_session.add(pred)
        test_db_session.commit()

        watcher_id = watcher.id

        from app.workers import notification_tasks

        with patch.object(
            notification_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(notification_tasks, "_send_push", return_value="expo-pred-id") as mock_push:
            result = self._fn().run()

        assert result["watchlists_matched"] >= 1
        assert result["notifications_sent"] >= 1
        mock_push.assert_called()

        from app.models import Notification

        notes = (
            test_db_session.query(Notification)
            .filter_by(user_id=watcher_id, trigger_type="prediction_drop")
            .all()
        )
        assert len(notes) >= 1

    @pytest.mark.integration
    def test_dedup_skips_recent_prediction_notif(self, test_db_session, sample_product: Product):
        from datetime import datetime, timedelta, timezone

        from app.models import Notification
        from app.models.prediction import PricePrediction
        from app.models.watchlist import UserWatchlist

        watcher = UserProfile(
            id=uuid4(),
            firebase_uid="preddedup",
            email="dedup@x.com",
            fcm_token="tok-dedup",
            reputation_score=1.0,
            contribution_count=0,
        )
        test_db_session.add(watcher)
        test_db_session.flush()

        entry = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            product_id=sample_product.id,
            alert_on_prediction=True,
            is_active=True,
        )
        test_db_session.add(entry)
        test_db_session.flush()

        now = datetime.now(timezone.utc)
        future_week = now + timedelta(days=7)
        pred = PricePrediction(
            id=uuid4(),
            product_id=sample_product.id,
            chain="woolworths",
            predicted_price_cents=299,
            prediction_week=future_week,
            is_likely_sale=True,
            model_version="test-v1",
        )
        # Existing recent notification — should suppress a new one
        existing_notif = Notification(
            id=uuid4(),
            user_id=watcher.id,
            watchlist_id=entry.id,
            price_report_id=None,
            trigger_type="prediction_drop",
            message_title="t",
            message_body="b",
            is_delivered=True,
            sent_at=now - timedelta(hours=1),
        )
        test_db_session.add_all([pred, existing_notif])
        test_db_session.commit()

        from app.workers import notification_tasks

        with patch.object(
            notification_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(
            notification_tasks, "_send_push", return_value="expo-receipt-id"
        ) as mock_push:
            result = self._fn().run()

        assert result["notifications_sent"] == 0
        mock_push.assert_not_called()

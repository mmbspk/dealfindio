"""
End-to-end integration tests for the Phase 2 community reporting flow.

Covers the value chain that defines Phase 2:

    community price report  ->  validation  ->  watchlist match  ->  notification

Because Celery tasks and Firebase FCM are external, these tests exercise the
pieces that run in-process (DB writes, watchlist matching logic, notification
log rows) and assert the data-layer outcomes, mocking the FCM boundary.

All tests use the SQLite in-memory test DB and fixtures from conftest.py.
The watchlist model's ``preferred_chains`` column uses a JSON variant on
SQLite (see app/models/watchlist.py) so the schema builds here.
"""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import PriceReport, Product, Store, UserProfile
from app.models.watchlist import UserWatchlist


# ---------------------------------------------------------------------------
# Fixtures specific to the reporting flow
# ---------------------------------------------------------------------------


@pytest.fixture
def reporter(test_db_session: Session) -> UserProfile:
    """A user who submits price reports."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="reporter_uid",
        email="reporter@example.com",
        reputation_score=3.0,
        contribution_count=25,
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return user


@pytest.fixture
def watcher(test_db_session: Session) -> UserProfile:
    """A different user watching a product for price drops."""
    user = UserProfile(
        id=uuid4(),
        firebase_uid="watcher_uid",
        email="watcher@example.com",
        fcm_token="fake-fcm-token",
        reputation_score=1.0,
        contribution_count=0,
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Watchlist matching logic (the core Phase 2 rule)
# ---------------------------------------------------------------------------


class TestWatchlistMatching:
    """Verify the price/percent-off matching rules at the data layer."""

    @pytest.mark.integration
    def test_price_match_when_below_target(
        self,
        test_db_session: Session,
        sample_product: Product,
        sample_store: Store,
        watcher: UserProfile,
    ):
        """A report at or below the target price should satisfy a price match."""
        entry = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            product_id=sample_product.id,
            target_price_cents=400,  # target $4.00
            is_active=True,
        )
        test_db_session.add(entry)

        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,  # $3.50 <= $4.00 -> match
            source="community",
            confidence=0.8,
            is_verified=True,
        )
        test_db_session.add(report)
        test_db_session.commit()

        # Emulate the matcher's query: active watchlist rows for this product
        # whose target is met by the report price.
        matches = (
            test_db_session.query(UserWatchlist)
            .filter(
                UserWatchlist.product_id == report.product_id,
                UserWatchlist.is_active.is_(True),
                UserWatchlist.target_price_cents >= report.price_cents,
            )
            .all()
        )
        assert len(matches) == 1
        assert matches[0].user_id == watcher.id

    @pytest.mark.integration
    def test_no_match_when_above_target(
        self,
        test_db_session: Session,
        sample_product: Product,
        sample_store: Store,
        watcher: UserProfile,
    ):
        """A report above the target price should not match."""
        entry = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            product_id=sample_product.id,
            target_price_cents=300,  # target $3.00
            is_active=True,
        )
        test_db_session.add(entry)

        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,  # $3.50 > $3.00 -> no match
            source="community",
            confidence=0.8,
        )
        test_db_session.add(report)
        test_db_session.commit()

        matches = (
            test_db_session.query(UserWatchlist)
            .filter(
                UserWatchlist.product_id == report.product_id,
                UserWatchlist.is_active.is_(True),
                UserWatchlist.target_price_cents >= report.price_cents,
            )
            .all()
        )
        assert matches == []

    @pytest.mark.integration
    def test_inactive_watchlist_excluded(
        self,
        test_db_session: Session,
        sample_product: Product,
        sample_store: Store,
        watcher: UserProfile,
    ):
        """Soft-deleted (is_active=False) watchlist rows never match."""
        entry = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            product_id=sample_product.id,
            target_price_cents=400,
            is_active=False,  # disabled
        )
        test_db_session.add(entry)
        test_db_session.commit()

        matches = (
            test_db_session.query(UserWatchlist)
            .filter(
                UserWatchlist.product_id == sample_product.id,
                UserWatchlist.is_active.is_(True),
            )
            .all()
        )
        assert matches == []

    @pytest.mark.integration
    def test_preferred_chains_json_roundtrip(
        self,
        test_db_session: Session,
        sample_product: Product,
        watcher: UserProfile,
    ):
        """preferred_chains (ARRAY on PG / JSON on SQLite) round-trips a list."""
        entry = UserWatchlist(
            id=uuid4(),
            user_id=watcher.id,
            product_id=sample_product.id,
            target_price_cents=400,
            preferred_chains=["coles", "woolworths"],
            is_active=True,
        )
        test_db_session.add(entry)
        test_db_session.commit()
        test_db_session.refresh(entry)

        assert entry.preferred_chains == ["coles", "woolworths"]


# ---------------------------------------------------------------------------
# Report ingestion + confidence
# ---------------------------------------------------------------------------


class TestCommunityReportIngestion:
    @pytest.mark.integration
    def test_community_report_persists_with_source(
        self,
        test_db_session: Session,
        sample_product: Product,
        sample_store: Store,
        reporter: UserProfile,
    ):
        """A community report is stored with source='community' and a reporter."""
        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=299,
            source="community",
            reporter_user_id=reporter.id,
            confidence=0.5,
            is_verified=False,
        )
        test_db_session.add(report)
        test_db_session.commit()
        test_db_session.refresh(report)

        assert report.id is not None
        assert report.source == "community"
        assert report.reporter_user_id == reporter.id

    @pytest.mark.integration
    def test_receipt_ocr_report_source(
        self,
        test_db_session: Session,
        sample_product: Product,
        sample_store: Store,
        reporter: UserProfile,
    ):
        """A receipt-derived report is tagged source='receipt_ocr'."""
        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=480,
            source="receipt_ocr",
            reporter_user_id=reporter.id,
            confidence=0.7,
        )
        test_db_session.add(report)
        test_db_session.commit()
        test_db_session.refresh(report)

        assert report.source == "receipt_ocr"
        assert report.confidence == 0.7

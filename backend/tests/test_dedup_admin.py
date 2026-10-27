"""
Integration tests for the admin dedup review flow and brand-aware matching.

Covers:
- ``catalog_ingest._match_product`` brand veto + no-inflation-on-missing-brand.
- The ``POST /api/v1/admin/dedup/candidates/{id}/decide`` approve path, which was
  reported as throwing "Database session error": approving a candidate merges the
  dropped product into the kept one, cascade-deleting the candidate row. The
  endpoint must still return 200 with ``status='approved'`` and re-point the
  dropped product's price_reports onto the kept product.
"""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.deps import get_current_user, get_db, require_admin
from app.main import app
from app.models import PriceReport, Product, Store
from app.models.product_match import ProductMatchCandidate
from app.services.catalog_ingest import _match_product
from scrapers.catalog_base import CatalogProduct


def _catalog_product(name, brand=None, price_cents=None, category=None):
    return CatalogProduct(
        product_name=name,
        price_cents=price_cents or 0,
        brand=brand,
        category=category,
    )


class TestMatchProductBrandVeto:
    """_match_product must not merge across a clear brand conflict."""

    @pytest.mark.integration
    def test_brand_conflict_vetoes_match(self, test_db_session):
        # Existing Woolworths product with a real brand.
        existing = Product(
            id=uuid4(),
            name="Orange Soft Drink 1.25L",
            brand="Fanta",
        )
        test_db_session.add(existing)
        test_db_session.commit()

        # Incoming ALDI item: same generic name, DIFFERENT brand.
        cp = _catalog_product("Orange Soft Drink 1.25L", brand="Kirks", price_cents=250)

        match, score, candidate, _ = _match_product(test_db_session, cp, "aldi")

        # Hard veto: no auto-merge AND no review candidate.
        assert match is None
        assert candidate is None
        assert score == 0.0

    @pytest.mark.integration
    def test_missing_brands_are_not_a_positive_signal(self, test_db_session):
        # Two products with a strong-but-imperfect title match and NO brands.
        # The old code awarded brand_score=100 for "both missing", which could
        # push a 92–99 title match over the ≥100 auto-merge line on phantom
        # brand agreement. With brand dropped from the weighting, a title that
        # isn't a perfect match must stay below 100 and only surface for review.
        existing = Product(
            id=uuid4(),
            name="Orange Soft Drink Bottle 1.25L",
            brand=None,
        )
        test_db_session.add(existing)
        test_db_session.commit()

        # Same product line, slightly different wording → high but sub-100 title.
        cp = _catalog_product("Orange Soft Drink Can 1.25L", brand=None, price_cents=250)

        match, score, candidate, _ = _match_product(test_db_session, cp, "aldi")

        # Not auto-merged: a phantom brand score must not inflate it to ≥100.
        assert match is None
        assert candidate is not None
        assert score < 100.0

    @pytest.mark.integration
    def test_identical_title_no_brand_auto_merges(self, test_db_session):
        # An EXACT normalized-title match with no brand on either side is a
        # legitimate merge — this is the catalog re-crawl / upsert case. Brand is
        # simply absent as a signal; title alone at 100 is enough. (Contrast with
        # a brand CONFLICT, which is vetoed regardless of title.)
        existing = Product(
            id=uuid4(),
            name="Orange Soft Drink 1.25L",
            brand=None,
        )
        test_db_session.add(existing)
        test_db_session.commit()

        cp = _catalog_product("Orange Soft Drink 1.25L", brand=None, price_cents=250)

        match, score, candidate, _ = _match_product(test_db_session, cp, "aldi")

        assert match is not None
        assert candidate is None
        assert score >= 100.0


@pytest.mark.pg
@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="approve endpoint uses Postgres now(); run on the Postgres test lane",
)
class TestDecideCandidateApprove:
    """Regression: approving a candidate must return 200, not a DB session error."""

    @pytest.fixture
    def admin_client(self, test_db_session):
        from fastapi.testclient import TestClient

        from app.models.user import User

        def override_get_db():
            return test_db_session

        admin_user = User(firebase_uid="admin_uid", email="admin@example.com")
        admin_user.is_admin = True

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: admin_user
        app.dependency_overrides[require_admin] = lambda: admin_user
        client = TestClient(app)
        yield client
        app.dependency_overrides.clear()

    def _seed_pair(self, session):
        store = Store(id=uuid4(), chain="coles", name="Coles Test")
        keep = Product(
            id=uuid4(),
            name="Coca-Cola 1.25L",
            brand="Coca-Cola",
        )
        drop = Product(id=uuid4(), name="Coca Cola 1.25L", brand="Coca-Cola")
        session.add_all([store, keep, drop])
        session.flush()

        # Price report on the DROPPED product — must be re-pointed to keep on merge.
        pr = PriceReport(
            id=uuid4(),
            product_id=drop.id,
            store_id=store.id,
            price_cents=250,
            source="catalog_coles",
            valid_from=datetime.now(timezone.utc),
        )
        candidate = ProductMatchCandidate(
            id=uuid4(),
            product_a_id=keep.id,
            product_b_id=drop.id,
            score=96.0,
            status="pending",
            proposed_by="auto",
        )
        session.add_all([pr, candidate])
        session.commit()
        return keep, drop, candidate, pr

    @pytest.mark.integration
    def test_approve_returns_200_and_merges(self, admin_client, test_db_session):
        import uuid as _uuid

        from sqlalchemy import select

        keep, drop, candidate, pr = self._seed_pair(test_db_session)
        keep_id, drop_id, cand_id, pr_id = (str(keep.id), str(drop.id), str(candidate.id), pr.id)

        resp = admin_client.post(
            f"/api/v1/admin/dedup/candidates/{cand_id}/decide",
            json={"decision": "approved", "keep_id": keep_id, "drop_id": drop_id},
        )

        # The bug reported this as a "Database session error" (500). Assert success.
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "approved"
        assert body["product_a"]["id"] == keep_id
        assert body["product_b"]["name"] == "(merged/deleted)"

        # Merge applied: dropped product gone, its price report re-pointed to keep.
        # Query by captured UUIDs (the ORM instances are now stale/deleted).
        test_db_session.expire_all()
        assert (
            test_db_session.execute(
                select(Product).where(Product.id == _uuid.UUID(drop_id))
            ).scalar_one_or_none()
            is None
        )
        assert (
            test_db_session.execute(
                select(Product).where(Product.id == _uuid.UUID(keep_id))
            ).scalar_one_or_none()
            is not None
        )
        moved = test_db_session.execute(
            select(PriceReport).where(PriceReport.id == pr_id)
        ).scalar_one()
        assert str(moved.product_id) == keep_id

        # ProductMergeLog: a permanent record must survive the product deletion.
        from app.models.product_merge_log import ProductMergeLog

        logs = (
            test_db_session.execute(
                select(ProductMergeLog).where(ProductMergeLog.drop_id == drop_id)
            )
            .scalars()
            .all()
        )
        assert len(logs) == 1
        log = logs[0]
        assert log.keep_id == keep_id
        assert log.drop_name == "Coca Cola 1.25L"
        assert log.candidate_id == cand_id
        assert log.reviewed_by == "admin@example.com"
        assert log.price_reports_moved == 1


# ── Size-variant penalty in fuzzy composite score (Gap 4) ────────────────────


class TestSizeVariantPenalty:
    """_match_product must never propose a merge between two products that have
    the same name/brand but different canonical package sizes (e.g. 375mL vs 1.25L)."""

    @pytest.mark.integration
    def test_different_size_variant_capped_below_review_threshold(self, test_db_session):
        """Identical name + brand, different package_size_norm: fully vetoed, no candidate."""
        existing = Product(
            id=uuid4(),
            name="Coke No Sugar",
            brand="Coca-Cola",
            package_size_norm="1.25L",
        )
        test_db_session.add(existing)
        test_db_session.commit()

        cp = CatalogProduct(
            product_name="Coke No Sugar",
            price_cents=350,
            brand="Coca-Cola",
            package_size="375mL",
        )
        match, score, candidate, _ = _match_product(test_db_session, cp, "woolworths")

        # Size mismatch: no auto-merge AND no review candidate (fully vetoed).
        assert match is None, "size variants must not auto-merge"
        assert candidate is None, "size variants must not be proposed as a review candidate"
        assert score == 0.0, f"vetoed pair must return score 0.0, got {score}"

    @pytest.mark.integration
    def test_matching_size_no_penalty(self, test_db_session):
        """Identical name + brand + size: full score, auto-merges without penalty."""
        existing = Product(
            id=uuid4(),
            name="Coca Cola Classic",
            brand="Coca-Cola",
            package_size_norm="375mL",
        )
        test_db_session.add(existing)
        test_db_session.commit()

        cp = CatalogProduct(
            product_name="Coca Cola Classic",
            price_cents=250,
            brand="Coca-Cola",
            package_size="375mL",
        )
        match, score, candidate, _ = _match_product(test_db_session, cp, "woolworths")

        # Same size: no penalty. Identical title + brand should auto-merge (score ≥ 100).
        assert (
            match is not None or score >= 92.0
        ), f"same-size pair should not be penalised (score={score})"

    @pytest.mark.integration
    def test_missing_package_size_no_penalty(self, test_db_session):
        """When either side has no size data, no penalty is applied."""
        existing = Product(
            id=uuid4(),
            name="Milk Full Cream",
            brand="Coles",
            package_size_norm=None,
        )
        test_db_session.add(existing)
        test_db_session.commit()

        cp = CatalogProduct(
            product_name="Milk Full Cream",
            price_cents=280,
            brand="Coles",
        )
        match, score, candidate, _ = _match_product(test_db_session, cp, "woolworths")

        # No size info → no penalty; identical name should auto-merge.
        assert (
            match is not None or score >= 92.0
        ), f"pair without size data should not be penalised (score={score})"


# ── ProductMergeLog audit trail ───────────────────────────────────────────────


class TestProductMergeLog:
    """_merge_products must write a ProductMergeLog row for every merge.

    All ID columns in the log are plain strings (not FKs) so the row survives
    the product DELETE and the cascade-delete of the candidate row.
    """

    def _make_pair(self, session, keep_name="Keep Product", drop_name="Drop Product"):
        """Seed a minimal keep/drop pair with one price report on the drop side."""
        store = Store(id=uuid4(), chain="aldi", name="ALDI Test")
        keep = Product(id=uuid4(), name=keep_name, brand="Brand A")
        drop = Product(id=uuid4(), name=drop_name, brand="Brand A")
        session.add_all([store, keep, drop])
        session.flush()
        pr = PriceReport(
            id=uuid4(),
            product_id=drop.id,
            store_id=store.id,
            price_cents=199,
            source="catalog_aldi",
            valid_from=datetime.now(timezone.utc),
        )
        session.add(pr)
        session.commit()
        return keep, drop, pr

    @pytest.mark.integration
    def test_log_entry_created_on_merge(self, test_db_session) -> None:
        """_merge_products must create a ProductMergeLog row capturing keep/drop names."""
        from sqlalchemy import select

        from app.models.product_merge_log import ProductMergeLog
        from app.routers.admin import _merge_products

        keep, drop, _pr = self._make_pair(test_db_session)
        keep_id, drop_id = str(keep.id), str(drop.id)

        _merge_products(
            test_db_session,
            keep_id,
            drop_id,
            reviewed_by="test",
        )
        test_db_session.commit()

        logs = (
            test_db_session.execute(
                select(ProductMergeLog).where(ProductMergeLog.drop_id == drop_id)
            )
            .scalars()
            .all()
        )
        assert len(logs) == 1
        log = logs[0]
        assert log.keep_id == keep_id
        assert log.drop_name == "Drop Product"
        assert log.keep_name == "Keep Product"
        assert log.reviewed_by == "test"

    @pytest.mark.integration
    def test_log_captures_price_reports_moved(self, test_db_session) -> None:
        """price_reports_moved must equal the number of price reports on the drop product."""
        from sqlalchemy import select

        from app.models.product_merge_log import ProductMergeLog
        from app.routers.admin import _merge_products

        keep, drop, _pr = self._make_pair(test_db_session)
        keep_id, drop_id = str(keep.id), str(drop.id)

        _merge_products(test_db_session, keep_id, drop_id, reviewed_by="test")
        test_db_session.commit()

        log = test_db_session.execute(
            select(ProductMergeLog).where(ProductMergeLog.drop_id == drop_id)
        ).scalar_one()
        assert log.price_reports_moved == 1

    @pytest.mark.integration
    def test_log_survives_after_drop_product_deleted(self, test_db_session) -> None:
        """The log row must exist even after the drop product is gone (plain-string ID, not FK)."""
        import uuid as _uuid

        from sqlalchemy import select

        from app.models.product_merge_log import ProductMergeLog
        from app.routers.admin import _merge_products

        keep, drop, _pr = self._make_pair(test_db_session)
        drop_id = str(drop.id)

        _merge_products(test_db_session, str(keep.id), drop_id, reviewed_by="test")
        test_db_session.commit()

        # Confirm drop product is gone
        assert (
            test_db_session.execute(
                select(Product).where(Product.id == _uuid.UUID(drop_id))
            ).scalar_one_or_none()
            is None
        )
        # Log must still be queryable — ID column is a plain string, not a FK
        log = test_db_session.execute(
            select(ProductMergeLog).where(ProductMergeLog.drop_id == drop_id)
        ).scalar_one_or_none()
        assert log is not None, "ProductMergeLog row must survive the product DELETE"

    @pytest.mark.integration
    def test_log_null_candidate_fields_when_no_candidate(self, test_db_session) -> None:
        """Calling _merge_products without a candidate_id must still create a log row
        with null candidate fields — supports manual/scripted merges without a queue row."""
        from sqlalchemy import select

        from app.models.product_merge_log import ProductMergeLog
        from app.routers.admin import _merge_products

        keep, drop, _pr = self._make_pair(test_db_session)
        keep_id, drop_id = str(keep.id), str(drop.id)

        _merge_products(test_db_session, keep_id, drop_id, reviewed_by="script")
        test_db_session.commit()

        log = test_db_session.execute(
            select(ProductMergeLog).where(ProductMergeLog.drop_id == drop_id)
        ).scalar_one()
        assert log.candidate_id is None
        assert log.score is None
        assert log.ai_verdict is None
        assert log.reviewed_by == "script"

    @pytest.mark.integration
    def test_log_candidate_context_passed_through(self, test_db_session) -> None:
        """All candidate audit fields must be written to the log when supplied."""
        from sqlalchemy import select

        from app.models.product_merge_log import ProductMergeLog
        from app.routers.admin import _merge_products

        keep, drop, _pr = self._make_pair(test_db_session)
        keep_id, drop_id = str(keep.id), str(drop.id)
        fake_cand_id = str(uuid4())

        _merge_products(
            test_db_session,
            keep_id,
            drop_id,
            candidate_id=fake_cand_id,
            score=97.5,
            ai_verdict="same",
            ai_confidence=0.98,
            ai_reason="Identical EAN-13 barcode",
            proposed_by="auto",
            reviewed_by="admin@example.com",
        )
        test_db_session.commit()

        log = test_db_session.execute(
            select(ProductMergeLog).where(ProductMergeLog.drop_id == drop_id)
        ).scalar_one()
        assert log.candidate_id == fake_cand_id
        assert log.score == pytest.approx(97.5)
        assert log.ai_verdict == "same"
        assert log.ai_confidence == pytest.approx(0.98)
        assert log.ai_reason == "Identical EAN-13 barcode"
        assert log.proposed_by == "auto"
        assert log.reviewed_by == "admin@example.com"

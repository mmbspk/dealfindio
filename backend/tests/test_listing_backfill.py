"""
Integration tests for Story #49d — idempotent backfill of ProductRetailerListing.

Tests use _do_backfill() directly (the pure function) so they don't need a
running Celery worker.  The Celery task itself is a thin wrapper around
_do_backfill(), so all logic is covered.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, ProductRetailerListing
from app.workers.catalog_tasks import _do_backfill

# ── helpers ────────────────────────────────────────────────────────────────────


def _product(
    db: Session,
    name: str,
    external_source: str | None = "catalog_coles",
    external_id: str | None = None,
    brand: str | None = None,
    external_image_url: str | None = None,
    package_size: str | None = None,
    in_stock: bool = True,
    is_online_only: bool = False,
    catalog_last_seen_at: datetime | None = None,
) -> Product:
    p = Product(
        id=uuid4(),
        name=name,
        external_source=external_source,
        external_id=external_id,
        brand=brand,
        external_image_url=external_image_url,
        package_size=package_size,
        in_stock=in_stock,
        is_online_only=is_online_only,
        catalog_last_seen_at=catalog_last_seen_at or datetime.now(timezone.utc),
    )
    db.add(p)
    db.flush()
    return p


# ── tests ──────────────────────────────────────────────────────────────────────


class TestBackfillCreatesListings:
    @pytest.mark.integration
    def test_creates_listing_for_eligible_product(self, test_db_session: Session):
        p = _product(
            test_db_session,
            "Coles Milk 2L",
            external_source="catalog_coles",
            external_id="COLES-MILK-001",
            brand="Coles Brand",
            external_image_url="http://img/milk.jpg",
            package_size="2L",
        )
        test_db_session.commit()

        result = _do_backfill(test_db_session)

        assert result["created"] == 1
        assert result["already_exists"] == 0
        assert result["conflict_skipped"] == 0
        assert result["total_eligible"] == 1

        listing = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.chain == "coles",
                ProductRetailerListing.external_id == "COLES-MILK-001",
            )
        ).scalar_one()

        assert listing.product_id == p.id
        assert listing.chain == "coles"
        assert listing.external_source == "catalog_coles"
        assert listing.external_name == "Coles Milk 2L"
        assert listing.external_brand == "Coles Brand"
        assert listing.external_image_url == "http://img/milk.jpg"
        assert listing.package_size_raw == "2L"
        assert listing.in_stock is True
        assert listing.is_online_only is False
        assert listing.catalog_last_seen_at is not None
        assert listing.external_category is None
        assert listing.external_url is None

    @pytest.mark.integration
    def test_creates_listings_for_multiple_chains(self, test_db_session: Session):
        _product(test_db_session, "P1", external_source="catalog_coles", external_id="C-001")
        _product(test_db_session, "P2", external_source="catalog_woolworths", external_id="W-001")
        _product(test_db_session, "P3", external_source="catalog_aldi", external_id="A-001")
        test_db_session.commit()

        result = _do_backfill(test_db_session)

        assert result["created"] == 3
        assert result["total_eligible"] == 3

        count = test_db_session.execute(
            select(func.count()).select_from(ProductRetailerListing)
        ).scalar()
        assert count == 3


class TestBackfillIdempotency:
    @pytest.mark.integration
    def test_second_run_skips_existing_listing(self, test_db_session: Session):
        _product(
            test_db_session, "Bread", external_source="catalog_woolworths", external_id="W-BRD"
        )
        test_db_session.commit()

        r1 = _do_backfill(test_db_session)
        r2 = _do_backfill(test_db_session)

        assert r1["created"] == 1
        assert r2["created"] == 0
        assert r2["already_exists"] == 1

        count = test_db_session.execute(
            select(func.count())
            .select_from(ProductRetailerListing)
            .where(
                ProductRetailerListing.chain == "woolworths",
                ProductRetailerListing.external_id == "W-BRD",
            )
        ).scalar()
        assert count == 1

    @pytest.mark.integration
    def test_pre_existing_listing_from_dual_write_skipped(self, test_db_session: Session):
        """A listing already written by the #49b dual-write path is left untouched."""
        p = _product(test_db_session, "Eggs", external_source="catalog_iga", external_id="IGA-EGG")
        test_db_session.commit()

        # Simulate dual-write already having created the listing
        listing = ProductRetailerListing(
            product_id=p.id,
            chain="iga",
            external_id="IGA-EGG",
            external_source="catalog_iga",
            external_name="Eggs (from dual-write)",
            in_stock=False,
            is_online_only=True,
        )
        test_db_session.add(listing)
        test_db_session.commit()

        result = _do_backfill(test_db_session)

        assert result["already_exists"] == 1
        assert result["created"] == 0

        # Dual-write values are preserved
        test_db_session.refresh(listing)
        assert listing.external_name == "Eggs (from dual-write)"
        assert listing.in_stock is False


class TestBackfillSkipsIneligibleProducts:
    @pytest.mark.integration
    def test_skips_product_without_external_id(self, test_db_session: Session):
        _product(
            test_db_session,
            "Ghost Product",
            external_source="catalog_coles",
            external_id=None,
        )
        test_db_session.commit()

        result = _do_backfill(test_db_session)

        assert result["total_eligible"] == 0
        assert result["created"] == 0

    @pytest.mark.integration
    def test_skips_product_without_catalog_source(self, test_db_session: Session):
        _product(
            test_db_session,
            "Manual Entry",
            external_source=None,
            external_id="MANUAL-001",
        )
        test_db_session.commit()

        result = _do_backfill(test_db_session)

        assert result["total_eligible"] == 0
        assert result["created"] == 0


class TestBackfillConflictHandling:
    @pytest.mark.integration
    def test_skips_when_existing_listing_points_to_different_product(
        self, test_db_session: Session, caplog
    ):
        """An existing listing that maps (chain, external_id) to a different product
        must not be overwritten by the backfill."""
        import logging

        product_a = _product(
            test_db_session, "Product A", external_source="catalog_drakes", external_id="DRK-001"
        )
        product_b = _product(test_db_session, "Product B", external_source=None, external_id=None)
        test_db_session.commit()

        # Existing listing points at product_b (different from product_a)
        listing = ProductRetailerListing(
            product_id=product_b.id,
            chain="drakes",
            external_id="DRK-001",
            external_source="catalog_drakes",
            external_name="Product B (established)",
            in_stock=True,
            is_online_only=False,
        )
        test_db_session.add(listing)
        test_db_session.commit()

        with caplog.at_level(logging.WARNING, logger="app.workers.catalog_tasks"):
            result = _do_backfill(test_db_session)

        assert result["conflict_skipped"] == 1
        assert result["created"] == 0

        # Existing mapping must not have been touched
        test_db_session.refresh(listing)
        assert listing.product_id == product_b.id

        assert any("Backfill conflict" in r.message for r in caplog.records)


class TestBackfillCountsAccuracy:
    @pytest.mark.integration
    def test_mixed_batch_produces_correct_counts(self, test_db_session: Session):
        """Three products: one new, one already backfilled, one without external_id."""
        p_new = _product(
            test_db_session, "New Product", external_source="catalog_coles", external_id="C-NEW"
        )
        p_exists = _product(
            test_db_session, "Existing", external_source="catalog_coles", external_id="C-EXISTS"
        )
        _product(
            test_db_session,
            "No SKU",
            external_source="catalog_coles",
            external_id=None,
        )
        test_db_session.commit()

        # Pre-create listing for p_exists
        test_db_session.add(
            ProductRetailerListing(
                product_id=p_exists.id,
                chain="coles",
                external_id="C-EXISTS",
                external_source="catalog_coles",
                external_name="Existing",
                in_stock=True,
                is_online_only=False,
            )
        )
        test_db_session.commit()

        result = _do_backfill(test_db_session)

        assert result["total_eligible"] == 2  # p_new + p_exists (no-SKU excluded)
        assert result["created"] == 1  # p_new
        assert result["already_exists"] == 1  # p_exists
        assert result["conflict_skipped"] == 0

"""
Tests for GET /admin/listings/coverage — per-chain listing coverage query.

Tests call _get_listing_coverage() directly with the test DB session so they
don't need a running HTTP server or mocked admin dependencies.

#49c read-migration gate: coverage_pct == 100 AND conflicted == 0
(ready_for_read_migration == True) for every expected chain.
"""

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Product, ProductRetailerListing
from app.routers.admin import _get_listing_coverage

# ── helpers ────────────────────────────────────────────────────────────────────


def _product(
    db: Session,
    name: str,
    external_source: str = "catalog_coles",
    external_id: str | None = "SKU-001",
) -> Product:
    p = Product(id=uuid4(), name=name, external_source=external_source, external_id=external_id)
    db.add(p)
    db.flush()
    return p


def _listing(
    db: Session,
    product_id,
    chain: str = "coles",
    external_id: str = "SKU-001",
) -> ProductRetailerListing:
    lst = ProductRetailerListing(
        product_id=product_id,
        chain=chain,
        external_id=external_id,
        external_source=f"catalog_{chain}",
        external_name="Test",
        in_stock=True,
        is_online_only=False,
    )
    db.add(lst)
    db.flush()
    return lst


def _chain_row(rows, chain: str):
    return next((r for r in rows if r.chain == chain), None)


# ── 1. Fully covered ───────────────────────────────────────────────────────────


class TestCoverageFullyCovered:
    @pytest.mark.integration
    def test_correctly_mapped_when_listing_matches(self, test_db_session: Session):
        p = _product(test_db_session, "Milk", external_id="COV-001")
        _listing(test_db_session, product_id=p.id, external_id="COV-001")
        test_db_session.commit()

        rows = _get_listing_coverage(test_db_session)
        row = _chain_row(rows, "coles")

        assert row is not None
        assert row.eligible == 1
        assert row.correctly_mapped == 1
        assert row.missing == 0
        assert row.conflicted == 0
        assert row.coverage_pct == 100.0
        assert row.ready_for_read_migration is True


# ── 2. Missing ─────────────────────────────────────────────────────────────────


class TestCoverageMissing:
    @pytest.mark.integration
    def test_missing_when_no_listing_exists(self, test_db_session: Session):
        _product(test_db_session, "Bread", external_id="COV-002")
        test_db_session.commit()

        rows = _get_listing_coverage(test_db_session)
        row = _chain_row(rows, "coles")

        assert row is not None
        assert row.eligible == 1
        assert row.correctly_mapped == 0
        assert row.missing == 1
        assert row.conflicted == 0
        assert row.coverage_pct == 0.0
        assert row.ready_for_read_migration is False


# ── 3. Conflict — the critical regression case ─────────────────────────────────


class TestCoverageConflict:
    @pytest.mark.integration
    def test_conflict_when_listing_product_id_differs(self, test_db_session: Session):
        """Listing exists for (coles, SKU) but points at Product B, not Product A.
        Must show conflicted=1, correctly_mapped=0, coverage_pct=0.
        This is the bug the reviewer caught: old query would have counted it as backfilled.
        """
        product_a = _product(test_db_session, "Product A", external_id="COV-003")
        product_b = Product(id=uuid4(), name="Product B")
        test_db_session.add(product_b)
        test_db_session.flush()

        # Listing points at product_b, but product_a is the eligible row
        _listing(test_db_session, product_id=product_b.id, external_id="COV-003")
        test_db_session.commit()

        rows = _get_listing_coverage(test_db_session)
        row = _chain_row(rows, "coles")

        assert row is not None
        assert row.eligible == 1
        assert row.correctly_mapped == 0
        assert row.missing == 0
        assert row.conflicted == 1
        assert row.coverage_pct == 0.0
        assert row.ready_for_read_migration is False


# ── 4. Mixed ───────────────────────────────────────────────────────────────────


class TestCoverageMixed:
    @pytest.mark.integration
    def test_mixed_correctly_mapped_missing_and_conflicted(self, test_db_session: Session):
        # correctly_mapped: product + matching listing
        p_good = _product(test_db_session, "Good", external_id="MIX-001")
        _listing(test_db_session, product_id=p_good.id, external_id="MIX-001")

        # missing: product with no listing
        _product(test_db_session, "Missing", external_id="MIX-002")

        # conflicted: product but listing points elsewhere
        p_conflict = _product(test_db_session, "Conflict Source", external_id="MIX-003")
        product_other = Product(id=uuid4(), name="Other Canonical")
        test_db_session.add(product_other)
        test_db_session.flush()
        _listing(test_db_session, product_id=product_other.id, external_id="MIX-003")

        test_db_session.commit()

        rows = _get_listing_coverage(test_db_session)
        row = _chain_row(rows, "coles")

        assert row is not None
        assert row.eligible == 3
        assert row.correctly_mapped == 1
        assert row.missing == 1
        assert row.conflicted == 1
        assert row.coverage_pct == pytest.approx(33.3, abs=0.2)
        assert row.ready_for_read_migration is False


# ── 5. Per-chain isolation ─────────────────────────────────────────────────────


class TestCoveragePerChainIsolation:
    @pytest.mark.integration
    def test_chains_reported_separately(self, test_db_session: Session):
        # Coles: fully covered
        p_coles = _product(
            test_db_session, "Coles Milk", external_source="catalog_coles", external_id="ISO-C"
        )
        _listing(test_db_session, product_id=p_coles.id, chain="coles", external_id="ISO-C")

        # Woolworths: missing
        _product(
            test_db_session,
            "WW Bread",
            external_source="catalog_woolworths",
            external_id="ISO-W",
        )

        test_db_session.commit()

        rows = _get_listing_coverage(test_db_session)

        coles = _chain_row(rows, "coles")
        ww = _chain_row(rows, "woolworths")

        assert coles is not None
        assert coles.eligible == 1
        assert coles.correctly_mapped == 1
        assert coles.conflicted == 0
        assert coles.ready_for_read_migration is True

        assert ww is not None
        assert ww.eligible == 1
        assert ww.correctly_mapped == 0
        assert ww.missing == 1
        assert ww.ready_for_read_migration is False

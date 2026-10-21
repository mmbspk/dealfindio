"""
Tests for scripts/audit_package_sizes.py — Story #101.

Uses SQLite in-memory (via the shared test_db_session fixture) for fast,
isolated integration tests.  Pure-Python helper functions are tested as
unit tests with no DB required.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_retailer_listing import ProductRetailerListing
from scripts.audit_package_sizes import (
    _gtin_check_digit_valid,
    _is_multipack_name,
    _is_multipack_norm,
    audit_cross_retailer_conflicts,
    audit_gtin_integrity,
    audit_name_vs_norm,
    audit_raw_vs_canonical,
    audit_single_multipack_collision,
    run_audit,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _product(
    session: Session, *, name: str, gtin: str | None = None, norm: str | None = None
) -> Product:
    p = Product(id=uuid4(), name=name, gtin=gtin, package_size_norm=norm)
    session.add(p)
    session.flush()
    return p


def _listing(
    session: Session,
    *,
    product: Product,
    chain: str,
    external_id: str,
    raw: str | None = None,
    ext_name: str | None = None,
) -> ProductRetailerListing:
    lst = ProductRetailerListing(
        id=uuid4(),
        product_id=product.id,
        chain=chain,
        external_id=external_id,
        package_size_raw=raw,
        external_name=ext_name,
    )
    session.add(lst)
    session.flush()
    return lst


# ── unit tests: detection helpers ─────────────────────────────────────────────


class TestIsMultipackName:
    @pytest.mark.parametrize(
        "text",
        [
            "12 Pack Soft Drink Cans 375mL",
            "Coca-Cola 6 Pack 375mL",
            "6pk Cans",
            "6x375mL",
            "6 × 375mL",
            "Multipack Variety",
            "24 ct water",
        ],
    )
    def test_multipack_names_detected(self, text):
        assert _is_multipack_name(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Coca-Cola 375mL",
            "Milk 2L",
            "500g Chicken Breast",
            None,
            "",
        ],
    )
    def test_single_names_not_detected(self, text):
        assert _is_multipack_name(text) is False


class TestIsMultipackNorm:
    @pytest.mark.parametrize("norm", ["6x375mL", "24x375mL", "12x30g", "4x1.25L"])
    def test_multipack_norms_detected(self, norm):
        assert _is_multipack_norm(norm) is True

    @pytest.mark.parametrize("norm", ["375mL", "1.5L", "500g", "2.5kg", None, ""])
    def test_single_norms_not_detected(self, norm):
        assert _is_multipack_norm(norm) is False


class TestGtinCheckDigit:
    @pytest.mark.parametrize(
        "gtin",
        [
            "5000112637922",  # valid EAN-13 (Coca-Cola 500 mL UK)
            "5901234123457",  # valid EAN-13 (GS1 reference example)
            "00012345678905",  # valid GTIN-14
            "96385074",  # valid EAN-8
        ],
    )
    def test_valid_gtins_pass(self, gtin):
        assert _gtin_check_digit_valid(gtin) is True

    @pytest.mark.parametrize(
        "gtin",
        [
            "9300617006553",  # check digit off by 1
            "1234567890123",  # random digits — check digit wrong
            "abc12345678",  # non-numeric
            "123456",  # wrong length
        ],
    )
    def test_invalid_gtins_fail(self, gtin):
        assert _gtin_check_digit_valid(gtin) is False


# ── integration tests: category 1 (name vs norm) ─────────────────────────────


class TestAuditNameVsNorm:
    def test_class_a_detected_when_raw_has_pack_count(self, test_db_session: Session):
        """Class A: raw already has pack count; new normalizer gives multipack."""
        p = _product(test_db_session, name="Soft Drink 375mL", norm="375mL")
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            external_id="coles-001",
            raw="375mL 12 Pack",
            ext_name="12 Pack Soft Drink Cans 375mL",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        findings = audit_name_vs_norm(products_by_id, listings_by_pid)

        assert len(findings) == 1
        f = findings[0]
        assert f["subtype"] == "class_a"
        assert f["computed_norm"] == "12x375mL"
        assert f["product_package_size_norm"] == "375mL"

    def test_class_b_detected_when_raw_lacks_pack_count(self, test_db_session: Session):
        """Class B: raw has no pack count; name still signals multipack."""
        p = _product(test_db_session, name="Soft Drink 375mL", norm="375mL")
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            external_id="coles-002",
            raw="375mL",
            ext_name="12 Pack Soft Drink Cans 375mL",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        findings = audit_name_vs_norm(products_by_id, listings_by_pid)

        assert len(findings) == 1
        assert findings[0]["subtype"] == "class_b"
        assert findings[0]["computed_norm"] == "375mL"

    def test_no_finding_when_norm_already_multipack(self, test_db_session: Session):
        """Product already has a multipack norm — not reported."""
        p = _product(test_db_session, name="Soft Drink 375mL", norm="12x375mL")
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            external_id="coles-003",
            raw="375mL 12 Pack",
            ext_name="12 Pack Soft Drink Cans 375mL",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_name_vs_norm(products_by_id, listings_by_pid) == []

    def test_no_finding_when_name_has_no_multipack_signal(self, test_db_session: Session):
        """Single-unit name with single-unit norm — clean product."""
        p = _product(test_db_session, name="Coca-Cola 375mL", norm="375mL")
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            external_id="coles-004",
            raw="375mL",
            ext_name="Coca-Cola 375mL",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_name_vs_norm(products_by_id, listings_by_pid) == []

    def test_no_finding_when_norm_is_none(self, test_db_session: Session):
        """Product with NULL norm and single-unit name — not a multipack conflict."""
        p = _product(test_db_session, name="Coca-Cola 375mL", norm=None)
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            external_id="ww-001",
            raw="375mL",
            ext_name="Coca-Cola 375mL",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_name_vs_norm(products_by_id, listings_by_pid) == []


# ── integration tests: category 2 (raw vs canonical) ─────────────────────────


class TestAuditRawVsCanonical:
    def test_stale_norm_detected(self, test_db_session: Session):
        """Stored norm is stale; new normalizer produces a different value."""
        p = _product(test_db_session, name="Soft Drink", norm="375mL")
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            external_id="coles-010",
            raw="375mL 12 Pack",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        findings = audit_raw_vs_canonical(products_by_id, listings_by_pid)

        assert len(findings) == 1
        assert findings[0]["computed_norm"] == "12x375mL"
        assert findings[0]["product_package_size_norm"] == "375mL"

    def test_no_finding_when_norms_match(self, test_db_session: Session):
        """Raw norm matches stored norm — nothing to report."""
        p = _product(test_db_session, name="Milk 2L", norm="2L")
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            external_id="ww-010",
            raw="2L",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_raw_vs_canonical(products_by_id, listings_by_pid) == []

    def test_no_finding_when_product_norm_is_none(self, test_db_session: Session):
        """Product with no stored norm — category 2 skips null norms."""
        p = _product(test_db_session, name="Assorted", norm=None)
        _listing(
            test_db_session,
            product=p,
            chain="aldi",
            external_id="aldi-010",
            raw="assorted",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_raw_vs_canonical(products_by_id, listings_by_pid) == []

    def test_no_finding_when_raw_unparseable(self, test_db_session: Session):
        """Unparseable raw string → new_norm is None → not reported."""
        p = _product(test_db_session, name="Chips", norm="100g")
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            external_id="coles-011",
            raw="assorted",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_raw_vs_canonical(products_by_id, listings_by_pid) == []


# ── integration tests: category 3 (cross-retailer conflicts) ──────────────────


class TestAuditCrossRetailerConflicts:
    def test_conflict_detected_across_two_chains(self, test_db_session: Session):
        """Two listings on one product with different norms → conflict row per listing."""
        p = _product(test_db_session, name="Soft Drink Cans", norm="375mL")
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            external_id="coles-020",
            raw="375mL",
        )
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            external_id="ww-020",
            raw="375mL 12 Pack",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        findings = audit_cross_retailer_conflicts(products_by_id, listings_by_pid)

        assert len(findings) == 2  # one row per conflicting listing
        chains = {f["listing_chain"] for f in findings}
        assert chains == {"coles", "woolworths"}
        norms_field = findings[0]["all_norms_on_product"]
        assert "375mL" in norms_field
        assert "12x375mL" in norms_field

    def test_no_conflict_when_all_listings_agree(self, test_db_session: Session):
        """All listings produce the same norm → no finding."""
        p = _product(test_db_session, name="Milk 2L", norm="2L")
        _listing(test_db_session, product=p, chain="coles", external_id="coles-021", raw="2L")
        _listing(test_db_session, product=p, chain="woolworths", external_id="ww-021", raw="2 L")

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_cross_retailer_conflicts(products_by_id, listings_by_pid) == []

    def test_no_conflict_with_single_listing(self, test_db_session: Session):
        """Only one parseable listing per product → not reported (no comparison possible)."""
        p = _product(test_db_session, name="Chips 200g", norm="200g")
        _listing(test_db_session, product=p, chain="aldi", external_id="aldi-020", raw="200g")

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_cross_retailer_conflicts(products_by_id, listings_by_pid) == []


# ── integration tests: category 4 (single/multipack collision) ───────────────


class TestAuditSingleMultipackCollision:
    def test_collision_detected(self, test_db_session: Session):
        """One listing gives single norm, another gives multipack norm."""
        p = _product(test_db_session, name="Coke Can", norm="375mL")
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            external_id="coles-030",
            raw="375mL",
            ext_name="Coca-Cola 375mL",
        )
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            external_id="ww-030",
            raw="375mL 6 Pack",
            ext_name="Coca-Cola 6 Pack 375mL",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        findings = audit_single_multipack_collision(products_by_id, listings_by_pid)

        assert len(findings) == 2
        types = {f["listing_type"] for f in findings}
        assert types == {"single", "multipack"}

        for f in findings:
            assert f["single_norms"] == "375mL"
            assert f["multi_norms"] == "6x375mL"

    def test_no_collision_when_all_single(self, test_db_session: Session):
        """Two single-unit listings → no collision."""
        p = _product(test_db_session, name="Milk 2L", norm="2L")
        _listing(test_db_session, product=p, chain="coles", external_id="coles-031", raw="2L")
        _listing(test_db_session, product=p, chain="woolworths", external_id="ww-031", raw="2 L")

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_single_multipack_collision(products_by_id, listings_by_pid) == []

    def test_no_collision_when_all_multipack(self, test_db_session: Session):
        """Two multipack listings (same count) → no collision."""
        p = _product(test_db_session, name="Coke 375mL 24 Pack", norm="24x375mL")
        _listing(test_db_session, product=p, chain="coles", external_id="coles-032", raw="24x375mL")
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            external_id="ww-032",
            raw="375mL 24 Pack",
        )

        products_by_id, listings_by_pid = _make_dicts(test_db_session)
        assert audit_single_multipack_collision(products_by_id, listings_by_pid) == []


# ── integration tests: category 5 (GTIN integrity) ───────────────────────────


class TestAuditGtinIntegrity:
    def test_bad_check_digit_flagged(self, test_db_session: Session):
        """GTIN with incorrect check digit → reported."""
        # "5000112637923" — differs by 1 from valid "5000112637922"
        p = _product(test_db_session, name="Milk", gtin="5000112637923")
        products_by_id = {str(p.id): p}
        findings = audit_gtin_integrity(products_by_id)

        assert len(findings) == 1
        assert findings[0]["failure_reason"] == "bad-check-digit"
        assert findings[0]["gtin"] == "5000112637923"

    def test_valid_gtin_not_flagged(self, test_db_session: Session):
        """GTIN with correct check digit → not reported."""
        p = _product(test_db_session, name="Milk", gtin="5000112637922")
        products_by_id = {str(p.id): p}
        assert audit_gtin_integrity(products_by_id) == []

    def test_null_gtin_not_flagged(self, test_db_session: Session):
        """Products with no GTIN are skipped."""
        p = _product(test_db_session, name="Chips", gtin=None)
        products_by_id = {str(p.id): p}
        assert audit_gtin_integrity(products_by_id) == []

    def test_non_numeric_gtin_flagged(self, test_db_session: Session):
        """GTIN containing non-digit characters → reported with 'non-numeric'."""
        p = _product(test_db_session, name="Product", gtin="ABC1234567890")
        products_by_id = {str(p.id): p}
        findings = audit_gtin_integrity(products_by_id)
        assert len(findings) == 1
        assert findings[0]["failure_reason"] == "non-numeric"

    def test_wrong_length_gtin_flagged(self, test_db_session: Session):
        """GTIN with non-standard length → reported with 'invalid-length-N'."""
        p = _product(test_db_session, name="Product", gtin="123456")
        products_by_id = {str(p.id): p}
        findings = audit_gtin_integrity(products_by_id)
        assert len(findings) == 1
        assert findings[0]["failure_reason"] == "invalid-length-6"


# ── integration tests: run_audit end-to-end ───────────────────────────────────


class TestRunAudit:
    def test_returns_all_five_categories(self, test_db_session: Session):
        """run_audit always returns all five category keys."""
        findings = run_audit(test_db_session)
        assert set(findings.keys()) == {
            "1_name_vs_norm",
            "2_raw_vs_canonical",
            "3_cross_retailer",
            "4_single_multipack",
            "5_gtin",
        }

    def test_empty_db_gives_no_findings(self, test_db_session: Session):
        """Empty database → all categories return empty lists."""
        findings = run_audit(test_db_session)
        for key, rows in findings.items():
            assert rows == [], f"Expected no findings for {key}, got {rows}"

    def test_chain_filter_restricts_listings(self, test_db_session: Session):
        """Passing chains= excludes listings from other chains."""
        p = _product(test_db_session, name="Soft Drink", norm="375mL")
        # Woolworths listing has the stale raw — should be excluded by the filter
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            external_id="ww-100",
            raw="375mL 12 Pack",
        )
        # Coles listing is clean
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            external_id="coles-100",
            raw="375mL",
        )

        # Audit only coles → the woolworths raw-conflict finding is absent
        findings = run_audit(test_db_session, chains={"coles"})
        raw_conflicts = findings["2_raw_vs_canonical"]
        for f in raw_conflicts:
            assert (
                f["listing_chain"] == "coles"
            ), f"Expected only coles listings but got: {f['listing_chain']}"

    def test_chains_filter_also_restricts_gtin_audit(self, test_db_session: Session):
        """GTIN findings are only reported for products with listings in specified chains."""
        # Product A: woolworths-only, bad GTIN — must NOT appear when filtering for coles
        pa = _product(test_db_session, name="Product A Wool", gtin="5000112637923")
        _listing(test_db_session, product=pa, chain="woolworths", external_id="ww-300", raw="375mL")

        # Product B: coles-only, bad GTIN — MUST appear when filtering for coles
        pb = _product(test_db_session, name="Product B Coles", gtin="5000112637930")
        _listing(test_db_session, product=pb, chain="coles", external_id="coles-300", raw="375mL")

        findings = run_audit(test_db_session, chains={"coles"})
        gtin_findings = findings["5_gtin"]

        assert len(gtin_findings) == 1
        assert gtin_findings[0]["product_name"] == "Product B Coles"

    def test_limit_caps_products_analysed(self, test_db_session: Session):
        """Passing limit=1 analyses at most 1 product across all categories."""
        for i in range(3):
            p = _product(test_db_session, name=f"Drink {i}", norm="375mL")
            _listing(
                test_db_session,
                product=p,
                chain="coles",
                external_id=f"coles-limit-{i}",
                raw="375mL 12 Pack",
            )

        findings = run_audit(test_db_session, limit=1)
        # Category 2 raw-vs-canonical should have at most 1 finding (1 product × 1 listing)
        assert len(findings["2_raw_vs_canonical"]) <= 1

    def test_limit_also_restricts_gtin_audit(self, test_db_session: Session):
        """limit= cap applies to the GTIN audit as well as listing audits."""
        for gtin in ("5000112637923", "5000112637930"):
            p = _product(test_db_session, name=f"Product {gtin}", gtin=gtin)
            _listing(
                test_db_session, product=p, chain="coles", external_id=f"coles-{gtin}", raw="375mL"
            )

        # Both products have bad GTINs but limit=1 → only 1 product audited
        findings = run_audit(test_db_session, limit=1)
        assert len(findings["5_gtin"]) <= 1

    def test_limit_selection_is_deterministic(self, test_db_session: Session):
        """limit= always picks the same products across runs (IDs are ordered)."""
        for i in range(3):
            p = _product(test_db_session, name=f"Drink {i}", norm="375mL")
            _listing(
                test_db_session,
                product=p,
                chain="coles",
                external_id=f"coles-det-{i}",
                raw="375mL 12 Pack",
            )

        findings_a = run_audit(test_db_session, limit=1)
        findings_b = run_audit(test_db_session, limit=1)

        assert findings_a["2_raw_vs_canonical"] == findings_b["2_raw_vs_canonical"]


# ── shared helper ─────────────────────────────────────────────────────────────


def _make_dicts(session: Session) -> tuple[dict, dict]:
    """Build (products_by_id, listings_by_product_id) from the current session state."""
    from collections import defaultdict

    from sqlalchemy import select

    products = session.execute(select(Product)).scalars().all()
    products_by_id = {str(p.id): p for p in products}

    listings = session.execute(select(ProductRetailerListing)).scalars().all()
    listings_by_product_id: dict[str, list] = defaultdict(list)
    for lst in listings:
        listings_by_product_id[str(lst.product_id)].append(lst)

    return products_by_id, dict(listings_by_product_id)

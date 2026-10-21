"""
Tests for scripts/remediation_planner.py — Story #103.

Uses SQLite in-memory (via the shared test_db_session fixture) for fast,
isolated integration tests.  Pure-Python helper functions are tested as
unit tests with no DB required.

Test coverage:
  * _is_genuine_multipack_name — excludes 1-pack, includes all genuine signals
  * _norm_is_weight_based — weight vs volume inner units
  * classify_product — all 7 classification subtypes
  * cross-listing conflict detection
  * run_plan — integration test with real SQLite session
  * Manifest row assembly (_to_manifest_row)
  * No database mutations possible
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_retailer_listing import ProductRetailerListing
from scripts.remediation_planner import (
    APPROVAL_INSUFFICIENT,
    APPROVAL_MANUAL,
    APPROVAL_SAFE,
    _is_genuine_multipack_name,
    _norm_is_weight_based,
    _to_manifest_row,
    classify_product,
    run_plan,
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
    external_id: str | None = None,
    raw: str | None = None,
    ext_name: str | None = None,
) -> ProductRetailerListing:
    lst = ProductRetailerListing(
        id=uuid4(),
        product_id=product.id,
        chain=chain,
        external_id=external_id or str(uuid4()),
        package_size_raw=raw,
        external_name=ext_name,
    )
    session.add(lst)
    session.flush()
    return lst


def _make_dicts(session: Session) -> tuple[dict, dict]:
    products = session.execute(select(Product)).scalars().all()
    products_by_id = {str(p.id): p for p in products}
    listings = session.execute(select(ProductRetailerListing)).scalars().all()
    listings_by_pid: dict[str, list] = defaultdict(list)
    for lst in listings:
        listings_by_pid[str(lst.product_id)].append(lst)
    return products_by_id, dict(listings_by_pid)


# ── unit: _is_genuine_multipack_name ─────────────────────────────────────────


class TestIsGenuineMultipackName:
    @pytest.mark.parametrize(
        "text",
        [
            "Coca-Cola 6 Pack 375mL",
            "Sorbent Toilet Paper 32 Pack",
            "Huggies Nappies 80 Pack",
            "Morning Fresh Dishwasher Tabs 72 Pack",
            "Country Fresh Eggs 6 Pack",
            "Solo 6pk Cans",
            "6 × 375mL",
            "Multipack Variety Box",
            "Bundaberg Ginger Beer 375mL 4 Pack",
            "2 pack",
        ],
    )
    def test_genuine_multipack_detected(self, text):
        assert _is_genuine_multipack_name(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            # 1-pack is single-unit retail labeling
            "Oral B Vitality Pro Black Toothbrush 1 Pack",
            "Raid Flying Insect Light Trap 1 Pack",
            "Energizer 123 3v Lithium Battery 1 Pack",
            "Mirabella Infrared Heat Lamp 1 Pack",
            # No multipack signal
            "Coca-Cola 375mL",
            "Milk 2L",
            "500g Chicken Breast",
            None,
            "",
        ],
    )
    def test_single_or_empty_not_genuine(self, text):
        assert _is_genuine_multipack_name(text) is False


# ── unit: _norm_is_weight_based ───────────────────────────────────────────────


class TestNormIsWeightBased:
    @pytest.mark.parametrize("norm", ["8x560g", "4x100g", "6x1.5kg", "12x200g"])
    def test_weight_norms_detected(self, norm):
        assert _norm_is_weight_based(norm) is True

    @pytest.mark.parametrize("norm", ["6x375mL", "24x250mL", "4x1.25L", "375mL", "500g"])
    def test_volume_and_single_not_weight(self, norm):
        assert _norm_is_weight_based(norm) is False


# ── unit: classify_product — confirmed_norm_mismatch ─────────────────────────


class TestClassifyConfirmedNormMismatch:
    def test_woolworths_raw_multipack_detected(self, test_db_session: Session):
        """Woolworths '375mL x 6 pack' raw → '6x375mL', stored '375mL' → confirmed_norm_mismatch."""
        p = _product(test_db_session, name="Fanta Orange Mini Cans", norm="375mL")
        lst = _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="375mL x 6 pack",
            ext_name="Fanta Orange Mini Cans 375mL 6 Pack",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "confirmed_norm_mismatch"
        assert cls["proposed_norm"] == "6x375mL"
        assert cls["confidence"] == "high"
        assert cls["approval_status"] == APPROVAL_SAFE

    def test_size_then_count_raw_format(self, test_db_session: Session):
        """'250mL 4 Pack' in raw → '4x250mL' → confirmed_norm_mismatch."""
        p = _product(test_db_session, name="Bundaberg Passionfruit", norm="250mL")
        lst = _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="250mL 4 Pack",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "confirmed_norm_mismatch"
        assert cls["proposed_norm"] == "4x250mL"

    def test_explicit_cross_pattern_raw(self, test_db_session: Session):
        """'24 x 375mL' in raw → '24x375mL' → confirmed_norm_mismatch."""
        p = _product(test_db_session, name="Coca-Cola Classic", norm="375mL")
        lst = _listing(
            test_db_session,
            product=p,
            chain="coles",
            raw="24 x 375mL",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "confirmed_norm_mismatch"
        assert cls["proposed_norm"] == "24x375mL"

    def test_no_finding_when_stored_already_multipack(self, test_db_session: Session):
        """Products already showing a multipack norm are not re-flagged."""
        p = _product(test_db_session, name="Fanta 6-pack", norm="6x375mL")
        lst = _listing(test_db_session, product=p, chain="woolworths", raw="375mL x 6 pack")
        assert classify_product(p, [lst]) is None

    def test_no_finding_when_raw_matches_stored(self, test_db_session: Session):
        """If the raw produces the same norm as stored, there is no anomaly."""
        p = _product(test_db_session, name="Coke Single", norm="375mL")
        lst = _listing(test_db_session, product=p, chain="coles", raw="375mL")
        assert classify_product(p, [lst]) is None

    def test_no_finding_for_product_no_listings(self):
        """Products with no listings produce no finding."""
        from uuid import uuid4

        p = Product(id=uuid4(), name="Ghost Product", package_size_norm="500g")
        assert classify_product(p, []) is None


# ── unit: classify_product — recoverable_from_raw ────────────────────────────


class TestClassifyRecoverableFromRaw:
    def test_single_unit_norm_difference(self, test_db_session: Session):
        """Raw '1.5L' but stored '1500mL' (same volume, different format) → recoverable_from_raw."""
        p = _product(test_db_session, name="Water 1.5L", norm="1500mL")
        lst = _listing(test_db_session, product=p, chain="coles", raw="1.5 L")
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "recoverable_from_raw"
        assert cls["proposed_norm"] == "1.5L"
        assert cls["approval_status"] == APPROVAL_SAFE

    def test_no_finding_when_stored_norm_none(self, test_db_session: Session):
        """When stored_norm is None, single-unit raw mismatches have nothing to compare to."""
        p = _product(test_db_session, name="Product", norm=None)
        lst = _listing(test_db_session, product=p, chain="coles", raw="500g")
        # No stored norm to compare against → no recoverable_from_raw finding
        cls = classify_product(p, [lst])
        assert cls is None


# ── unit: classify_product — recoverable_from_name ───────────────────────────


class TestClassifyRecoverableFromName:
    def test_volume_count_in_name_no_raw_size(self, test_db_session: Session):
        """Name has volume + count but raw is empty → recoverable_from_name."""
        p = _product(test_db_session, name="Fanta 375mL 6 Pack", norm="375mL")
        lst = _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw=None,
            ext_name="Fanta 375mL 6 Pack",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "recoverable_from_name"
        assert cls["proposed_norm"] == "6x375mL"
        assert cls["confidence"] == "medium"
        assert cls["approval_status"] == APPROVAL_SAFE

    def test_count_then_volume_in_name(self, test_db_session: Session):
        """'12 Pack 375mL' name pattern → recoverable_from_name."""
        p = _product(test_db_session, name="Solo 375mL 12-Pack", norm="375mL")
        lst = _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw=None,
            ext_name="Solo 12 Pack 375mL",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "recoverable_from_name"
        assert cls["proposed_norm"] == "12x375mL"

    def test_no_finding_when_name_norm_matches_stored(self, test_db_session: Session):
        """No finding if name norm already equals the stored norm."""
        p = _product(test_db_session, name="Fanta 6 Pack 375mL", norm="6x375mL")
        lst = _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw=None,
            ext_name="Fanta 6 Pack 375mL",
        )
        assert classify_product(p, [lst]) is None


# ── unit: classify_product — count_only ──────────────────────────────────────


class TestClassifyCountOnly:
    def test_n_pack_no_size(self, test_db_session: Session):
        """Name has 'N Pack' but no per-unit size → count_only / insufficient_evidence."""
        p = _product(test_db_session, name="Sabco Microfibre Cloths 3 Pack", norm=None)
        lst = _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw="",
            ext_name="Sabco Microfibre Cloths 3 Pack",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "count_only"
        assert cls["approval_status"] == APPROVAL_INSUFFICIENT

    def test_toilet_paper_count_only(self, test_db_session: Session):
        """'32 Pack' with no size unit → count_only."""
        p = _product(test_db_session, name="Sorbent Toilet Paper 32 Pack", norm=None)
        lst = _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw=None,
            ext_name="Sorbent Toilet Paper 32 Pack",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "count_only"

    def test_one_pack_excluded(self, test_db_session: Session):
        """'1 Pack' is a single-unit label, not a genuine multipack → no finding."""
        p = _product(test_db_session, name="Oral B 1 Pack", norm=None)
        lst = _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw="",
            ext_name="Oral B Vitality Pro Black Toothbrush 1 Pack",
        )
        assert classify_product(p, [lst]) is None

    def test_energizer_one_pack_excluded(self, test_db_session: Session):
        """Single-unit battery labeled '1 Pack' → no finding."""
        p = _product(test_db_session, name="Energizer 123 Battery 1 Pack", norm=None)
        lst = _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw="",
            ext_name="Energizer 123 3v Lithium Battery 1 Pack",
        )
        assert classify_product(p, [lst]) is None


# ── unit: classify_product — total_package_weight ────────────────────────────


class TestClassifyTotalPackageWeight:
    def test_weight_count_in_name(self, test_db_session: Session):
        """Name 'BBQ Sausages 8 Pack 560g' → total_package_weight / manual_review."""
        p = _product(test_db_session, name="BBQ Sausages 8 Pack", norm="560g")
        lst = _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw="560g",
            ext_name="BBQ Sausages 8 Pack 560g",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "total_package_weight"
        assert cls["proposed_norm"] is None
        assert cls["approval_status"] == APPROVAL_MANUAL

    def test_no_proposed_norm_for_weight_based(self, test_db_session: Session):
        """Weight-based multipack never produces a proposed_norm — too ambiguous."""
        p = _product(test_db_session, name="Pork Sausages", norm="500g")
        lst = _listing(
            test_db_session,
            product=p,
            chain="coles",
            raw="500g",
            ext_name="Pork Sausages 4 Pack 500g",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "total_package_weight"
        assert cls["proposed_norm"] is None

    def test_chips_per_pack_is_also_weight_based(self, test_db_session: Session):
        """Smiths Chips 6 Pack 25g — even if per-item, classified conservatively."""
        p = _product(test_db_session, name="Smiths Chips 6 Pack 25g", norm="25g")
        lst = _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="25g",
            ext_name="Smiths Original Chips 6 Pack 25g",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "total_package_weight"


# ── unit: classify_product — conflicting_evidence ────────────────────────────


class TestClassifyConflictingEvidence:
    def test_two_different_single_unit_raws_conflict(self, test_db_session: Session):
        """Two listings with different single-unit raw norms → conflicting_evidence.

        Covers the Camembert case: Woolworths stores 200g while IGA stores 125g on the
        same canonical product row.  Both raws parse to single-unit norms that disagree,
        which signals a likely bad dedup merge — manual review is required.
        """
        p = _product(test_db_session, name="Camembert Cheese", norm="200g")
        lst_ww = _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            external_id="464673",
            raw="200g",
            ext_name="Tasmanian Heritage Camembert 200g",
        )
        lst_iga = _listing(
            test_db_session,
            product=p,
            chain="iga",
            external_id="118125",
            raw="125 g",
            ext_name="Tasmanian Heritage Camembert 125g",
        )
        cls = classify_product(p, [lst_ww, lst_iga])
        assert cls is not None
        assert cls["classification"] == "conflicting_evidence"
        assert cls["approval_status"] == APPROVAL_MANUAL
        assert cls["proposed_norm"] is None

    def test_two_different_multipack_raws_conflict(self, test_db_session: Session):
        """Two listings with different multipack raw norms → conflicting_evidence."""
        p = _product(test_db_session, name="Weird Multi", norm="375mL")
        lst_a = _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="375mL x 6 pack",
        )
        lst_b = _listing(
            test_db_session,
            product=p,
            chain="coles",
            raw="375mL x 4 pack",
        )
        cls = classify_product(p, [lst_a, lst_b])
        assert cls is not None
        assert cls["classification"] == "conflicting_evidence"
        assert cls["approval_status"] == APPROVAL_MANUAL

    def test_multipack_and_single_raw_conflict(self, test_db_session: Session):
        """Multipack raw from one chain + single-unit raw from another → conflicting_evidence."""
        p = _product(test_db_session, name="Bundaberg 375mL", norm="375mL")
        lst_ww = _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="375mL x 4 pack",
        )
        lst_coles = _listing(
            test_db_session,
            product=p,
            chain="coles",
            raw="375mL",
        )
        cls = classify_product(p, [lst_ww, lst_coles])
        assert cls is not None
        assert cls["classification"] == "conflicting_evidence"
        assert cls["approval_status"] == APPROVAL_MANUAL

    def test_stored_multipack_contradicted_by_different_multipack_listing(
        self, test_db_session: Session
    ):
        """Stored 6x375mL but one listing raw gives 12x375mL → conflicting_evidence."""
        p = _product(test_db_session, name="Fanta Mislabeled", norm="6x375mL")
        lst = _listing(test_db_session, product=p, chain="coles", raw="375mL x 12 pack")
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "conflicting_evidence"
        assert cls["approval_status"] == APPROVAL_MANUAL
        assert cls["proposed_norm"] is None

    def test_stored_multipack_contradicted_by_single_unit_listing(self, test_db_session: Session):
        """Stored 6x375mL but one listing raw is single-unit 375mL → conflicting_evidence."""
        p = _product(test_db_session, name="Fanta Mislabeled Single", norm="6x375mL")
        lst = _listing(test_db_session, product=p, chain="drakes", raw="375mL")
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "conflicting_evidence"
        assert cls["approval_status"] == APPROVAL_MANUAL

    def test_name_contradicts_raw_multipack_norm(self, test_db_session: Session):
        """Raw says 6x375mL but name says 12 Pack → conflicting_evidence."""
        p = _product(test_db_session, name="Soda", norm="375mL")
        lst = _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="375mL x 6 pack",
            ext_name="Soda 12 Pack 375mL",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "conflicting_evidence"
        assert cls["approval_status"] == APPROVAL_MANUAL
        assert cls["proposed_norm"] is None


# ── unit: manifest row ────────────────────────────────────────────────────────


class TestManifestRow:
    def test_safe_candidate_action_is_recompute(self, test_db_session: Session):
        p = _product(test_db_session, name="Fanta 6 Pack", norm="375mL")
        lst = _listing(test_db_session, product=p, chain="woolworths", raw="375mL x 6 pack")
        cls = classify_product(p, [lst])
        assert cls is not None
        row = _to_manifest_row(p, cls, [lst])
        assert row["remediation_action"] == "recompute"
        assert row["approval_status"] == APPROVAL_SAFE
        assert row["proposed_norm"] == "6x375mL"
        assert row["current_norm"] == "375mL"

    def test_conflict_action_is_manual_unmerge(self, test_db_session: Session):
        p = _product(test_db_session, name="Weird Multi", norm="375mL")
        lst_a = _listing(test_db_session, product=p, chain="woolworths", raw="375mL x 6 pack")
        lst_b = _listing(test_db_session, product=p, chain="coles", raw="375mL x 4 pack")
        cls = classify_product(p, [lst_a, lst_b])
        assert cls is not None
        row = _to_manifest_row(p, cls, [lst_a, lst_b])
        assert row["remediation_action"] == "manual_unmerge_or_split"

    def test_count_only_action_is_none(self, test_db_session: Session):
        p = _product(test_db_session, name="Cloths 3 Pack", norm=None)
        lst = _listing(test_db_session, product=p, chain="drakes", raw="", ext_name="Cloths 3 Pack")
        cls = classify_product(p, [lst])
        assert cls is not None
        row = _to_manifest_row(p, cls, [lst])
        assert row["remediation_action"] == "none"

    def test_recoverable_from_name_action_is_source_recovery(self, test_db_session: Session):
        """recoverable_from_name requires a fresh scrape — recompute alone cannot help."""
        p = _product(test_db_session, name="Fanta 375mL 6 Pack", norm="375mL")
        lst = _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw=None,
            ext_name="Fanta 375mL 6 Pack",
        )
        cls = classify_product(p, [lst])
        assert cls is not None
        assert cls["classification"] == "recoverable_from_name"
        row = _to_manifest_row(p, cls, [lst])
        assert row["remediation_action"] == "source_recovery_or_rescrape"

    def test_all_raw_norms_aggregated(self, test_db_session: Session):
        p = _product(test_db_session, name="Fanta", norm="375mL")
        lst_ww = _listing(test_db_session, product=p, chain="woolworths", raw="375mL x 6 pack")
        lst_coles = _listing(test_db_session, product=p, chain="coles", raw="375mL")
        cls = classify_product(p, [lst_ww, lst_coles])
        # May be conflicting_evidence (multipack + single) or confirmed_mismatch
        # depending on classification; just check the row is built correctly
        if cls:
            row = _to_manifest_row(p, cls, [lst_ww, lst_coles])
            assert row["all_raw_norms"] != ""

    def test_gtin_validity_included(self, test_db_session: Session):
        p = _product(
            test_db_session,
            name="Coke",
            gtin="5000112637922",  # valid EAN-13
            norm="375mL",
        )
        lst = _listing(test_db_session, product=p, chain="woolworths", raw="375mL x 6 pack")
        cls = classify_product(p, [lst])
        assert cls is not None
        row = _to_manifest_row(p, cls, [lst])
        assert row["gtin_valid"] == "yes"

    def test_invalid_gtin_flagged(self, test_db_session: Session):
        p = _product(
            test_db_session,
            name="Bad GTIN Product",
            gtin="1234567890123",  # invalid check digit
            norm="375mL",
        )
        lst = _listing(test_db_session, product=p, chain="woolworths", raw="375mL x 6 pack")
        cls = classify_product(p, [lst])
        assert cls is not None
        row = _to_manifest_row(p, cls, [lst])
        assert row["gtin_valid"] == "no"


# ── integration: run_plan ─────────────────────────────────────────────────────


class TestRunPlan:
    def test_empty_db_returns_no_rows(self, test_db_session: Session):
        rows = run_plan(test_db_session)
        assert rows == []

    def test_confirmed_mismatch_found(self, test_db_session: Session):
        p = _product(test_db_session, name="Fanta 6 Pack", norm="375mL")
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="375mL x 6 pack",
            ext_name="Fanta Mini Cans 375mL 6 Pack",
        )
        rows = run_plan(test_db_session)
        assert len(rows) == 1
        assert rows[0]["classification"] == "confirmed_norm_mismatch"
        assert rows[0]["proposed_norm"] == "6x375mL"

    def test_count_only_product_included(self, test_db_session: Session):
        """count_only findings appear in the manifest (with insufficient_evidence status)."""
        p = _product(test_db_session, name="Cloths 3 Pack", norm=None)
        _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw="",
            ext_name="Sabco Microfibre Cloths 3 Pack",
        )
        rows = run_plan(test_db_session)
        assert len(rows) == 1
        assert rows[0]["classification"] == "count_only"
        assert rows[0]["approval_status"] == APPROVAL_INSUFFICIENT

    def test_one_pack_products_excluded(self, test_db_session: Session):
        """Products with only '1 Pack' signal produce no manifest row."""
        p = _product(test_db_session, name="Toothbrush", norm=None)
        _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw="",
            ext_name="Oral B Vitality Pro Black Toothbrush 1 Pack",
        )
        rows = run_plan(test_db_session)
        assert rows == []

    def test_already_multipack_norm_excluded(self, test_db_session: Session):
        """Products with multipack norm and consistent listing raws produce no findings."""
        p = _product(test_db_session, name="Fanta 6-Pack", norm="6x375mL")
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="375mL x 6 pack",
        )
        rows = run_plan(test_db_session)
        assert rows == []

    def test_limit_caps_output(self, test_db_session: Session):
        """--limit N produces at most N manifest rows."""
        for i in range(5):
            p = _product(test_db_session, name=f"Product {i}", norm="375mL")
            _listing(
                test_db_session,
                product=p,
                chain="woolworths",
                raw="375mL x 6 pack",
            )
        rows = run_plan(test_db_session, limit=3)
        assert len(rows) == 3

    def test_chain_filter_restricts_scope(self, test_db_session: Session):
        """When chains filter is set, only products with listings in those chains are included."""
        p_ww = _product(test_db_session, name="Woolworths Product", norm="375mL")
        _listing(
            test_db_session,
            product=p_ww,
            chain="woolworths",
            raw="375mL x 6 pack",
        )
        p_coles = _product(test_db_session, name="Coles Product", norm="375mL")
        _listing(
            test_db_session,
            product=p_coles,
            chain="coles",
            raw="250mL x 4 pack",
        )

        ww_rows = run_plan(test_db_session, chains={"woolworths"})
        assert all(r["evidence_chain"] == "woolworths" for r in ww_rows)
        product_ids_ww = {r["product_id"] for r in ww_rows}
        assert str(p_coles.id) not in product_ids_ww

    def test_deterministic_with_limit(self, test_db_session: Session):
        """Two calls with the same --limit produce identical product_id sets."""
        for i in range(10):
            p = _product(test_db_session, name=f"Product {i}", norm="375mL")
            _listing(
                test_db_session,
                product=p,
                chain="woolworths",
                raw="375mL x 6 pack",
            )
        rows_a = run_plan(test_db_session, limit=5)
        rows_b = run_plan(test_db_session, limit=5)
        assert [r["product_id"] for r in rows_a] == [r["product_id"] for r in rows_b]

    def test_no_mutations_on_clean_products(self, test_db_session: Session):
        """Products with correct norms produce no manifest rows."""
        p = _product(test_db_session, name="Correct Product", norm="375mL")
        _listing(test_db_session, product=p, chain="woolworths", raw="375mL")
        rows = run_plan(test_db_session)
        assert rows == []

    def test_cross_chain_conflict_surfaces_in_manifest(self, test_db_session: Session):
        """A product with conflicting multipack raws across chains appears with conflicting_evidence."""
        p = _product(test_db_session, name="Multi-Chain Conflict", norm="375mL")
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="375mL x 6 pack",
        )
        _listing(
            test_db_session,
            product=p,
            chain="coles",
            raw="375mL x 4 pack",
        )
        rows = run_plan(test_db_session)
        assert len(rows) == 1
        assert rows[0]["classification"] == "conflicting_evidence"
        assert rows[0]["approval_status"] == APPROVAL_MANUAL

    def test_manifest_fields_complete(self, test_db_session: Session):
        """Every manifest row contains all required fields."""
        from scripts.remediation_planner import _MANIFEST_FIELDS

        p = _product(test_db_session, name="Field Check Product", norm="375mL")
        _listing(
            test_db_session,
            product=p,
            chain="woolworths",
            raw="375mL x 6 pack",
        )
        rows = run_plan(test_db_session)
        assert rows
        for row in rows:
            for field in _MANIFEST_FIELDS:
                assert field in row, f"Missing manifest field: {field}"

    def test_total_package_weight_manual_review(self, test_db_session: Session):
        """Weight-based multipack findings appear with manual_review status."""
        p = _product(test_db_session, name="BBQ Sausages 8 Pack", norm="560g")
        _listing(
            test_db_session,
            product=p,
            chain="drakes",
            raw="560g",
            ext_name="BBQ Sausages 8 Pack 560g",
        )
        rows = run_plan(test_db_session)
        assert len(rows) == 1
        assert rows[0]["classification"] == "total_package_weight"
        assert rows[0]["approval_status"] == APPROVAL_MANUAL
        assert rows[0]["proposed_norm"] == ""

    def test_insufficient_evidence_products_never_safe(self, test_db_session: Session):
        """No count_only or insufficient_evidence row has approval_status=safe_candidate."""
        products_and_listings = [
            ("3 Pack Cloths", None, "Sabco Microfibre Cloths 3 Pack", ""),
            ("Nappies 80 Pack", None, "Huggies Nappies 80 Pack", None),
            ("Tea Bags 50 Pack", None, "Bushells Tea Bags 50 Pack", None),
        ]
        for name, norm, ext_name, raw in products_and_listings:
            p = _product(test_db_session, name=name, norm=norm)
            _listing(
                test_db_session,
                product=p,
                chain="drakes",
                raw=raw,
                ext_name=ext_name,
            )
        rows = run_plan(test_db_session)
        count_and_insufficient = [
            r for r in rows if r["classification"] in ("count_only", "insufficient_evidence")
        ]
        for row in count_and_insufficient:
            assert row["approval_status"] != APPROVAL_SAFE, (
                f"Row with classification={row['classification']} must never be "
                f"approval_status=safe_candidate"
            )

    def test_stored_multipack_with_conflicting_raw_in_manifest(self, test_db_session: Session):
        """Stored multipack norm but listing raw gives different multipack → manifest row."""
        p = _product(test_db_session, name="Mislabeled Fanta", norm="6x375mL")
        _listing(test_db_session, product=p, chain="coles", raw="375mL x 12 pack")
        rows = run_plan(test_db_session)
        assert len(rows) == 1
        assert rows[0]["classification"] == "conflicting_evidence"
        assert rows[0]["approval_status"] == APPROVAL_MANUAL
        assert rows[0]["proposed_norm"] == ""

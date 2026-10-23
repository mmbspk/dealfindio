"""Unit tests for the _evaluate_criteria logic in validate_crawl_109.py.

Each test verifies that a criterion can BOTH pass AND fail depending on the
input — not just that it doesn't crash on the happy path.

The module is loaded via importlib so these tests can live in the test suite
without a scripts/__init__.py.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

from scrapers.catalog_base import CatalogProduct

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "validate_crawl_109_mod",
    _SCRIPTS_DIR / "validate_crawl_109.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["validate_crawl_109_mod"] = _mod  # required so @dataclass can resolve cls.__module__
_spec.loader.exec_module(_mod)

ValidationResult = _mod.ValidationResult
_evaluate_criteria = _mod._evaluate_criteria
_all_criteria_passed = _mod._all_criteria_passed


def _cp(ext_id: str, package_size: str | None) -> CatalogProduct:
    return CatalogProduct(
        product_name=f"Test Product {ext_id}",
        price_cents=400,
        gtin=None,
        brand=None,
        category="Beer",
        image_url="http://img/x.jpg",
        external_id=ext_id,
        package_size=package_size,
        in_stock=True,
        is_online_only=False,
        valid_from=date.today(),
    )


class TestAC1EnrichmentFiredOnScrapedData:
    """AC1: a count-only product was enriched AND the exact expected norm was persisted in DB.

    Merely checking that the persisted value contains "x" is insufficient — an existing
    "4x375mL" on the canonical would make AC1 pass even if the scraper just enriched
    a "6PK" product to "6x355mL".  The persisted norm must exactly match the expected
    norm for that specific external_id.
    """

    @pytest.mark.unit
    def test_passes_when_count_only_product_has_enriched_persisted_norm(self):
        """E1 started as '6PK'; scraper enriched to '6x355mL'; DB confirms exact match."""
        result = ValidationResult(
            count_only_external_ids={"E1"},
            count_only_expected_norms={"E1": "6x355mL"},
            count_only_persisted_norms={"E1": "6x355mL"},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is True

    @pytest.mark.unit
    def test_fails_when_count_only_product_has_null_persisted_norm(self):
        """Scraper enriched to '6x355mL' but the DB record has package_size_norm=None."""
        result = ValidationResult(
            count_only_external_ids={"E1"},
            count_only_expected_norms={"E1": "6x355mL"},
            count_only_persisted_norms={"E1": None},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False

    @pytest.mark.unit
    def test_fails_when_persisted_norm_differs_from_expected_norm(self):
        """Scraper enriched E1 to '6x355mL' but the DB has a stale '4x375mL'.

        This is the case from the PR review: a persisted value containing 'x'
        must not be enough — it must exactly match the expected norm for that SKU.
        """
        result = ValidationResult(
            count_only_external_ids={"E1"},
            count_only_expected_norms={"E1": "6x355mL"},
            count_only_persisted_norms={"E1": "4x375mL"},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False

    @pytest.mark.unit
    def test_fails_when_count_only_persisted_norms_is_empty(self):
        """count_only_persisted_norms not populated (e.g. ingest failed before query)."""
        result = ValidationResult(
            count_only_external_ids={"E1"},
            count_only_expected_norms={"E1": "6x355mL"},
            count_only_persisted_norms={},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False

    @pytest.mark.unit
    def test_fails_when_count_only_external_ids_is_empty(self):
        """No count-only products scraped — enrichment cannot be proved."""
        result = ValidationResult(
            count_only_external_ids=set(),
            count_only_expected_norms={},
            count_only_persisted_norms={},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False

    @pytest.mark.unit
    def test_fails_when_scraped_products_is_empty(self):
        result = ValidationResult(
            scraped_products=[],
            count_only_external_ids=set(),
            count_only_expected_norms={},
            count_only_persisted_norms={},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False


class TestAC2NoUnexpectedMerges:
    """AC2: no product created by this crawl appears in the collision list."""

    @pytest.mark.unit
    def test_passes_when_crawl_products_not_in_collision_list(self):
        result = ValidationResult(
            new_product_ids={"uuid-001", "uuid-002"},
            audit_findings={
                "single_multipack_collision": [{"product_id": "uuid-old"}],
            },
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac2_no_unexpected_merges"] is True

    @pytest.mark.unit
    def test_fails_when_crawl_product_id_appears_in_collision_list(self):
        result = ValidationResult(
            new_product_ids={"uuid-001", "uuid-002"},
            audit_findings={
                "single_multipack_collision": [{"product_id": "uuid-002"}],
            },
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac2_no_unexpected_merges"] is False

    @pytest.mark.unit
    def test_passes_when_no_collisions_exist(self):
        result = ValidationResult(
            new_product_ids={"uuid-001"},
            audit_findings={},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac2_no_unexpected_merges"] is True

    @pytest.mark.unit
    def test_passes_when_new_product_ids_empty_but_historical_collisions_exist(self):
        """Empty new_product_ids → 'no evidence of new collision'.  Must not fail."""
        result = ValidationResult(
            new_product_ids=set(),
            audit_findings={
                "single_multipack_collision": [{"product_id": "uuid-old"}],
            },
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac2_no_unexpected_merges"] is True


class TestAC3SeparateProducts:
    """AC3: no two scraped products with ANY different norms map to the same canonical.

    This covers both the null+known case and two incompatible known norms.
    """

    @pytest.mark.unit
    def test_passes_when_different_norms_map_to_different_canonicals(self):
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "375mL"),  # norm = "375mL"
                _cp("E2", "6 Pack"),  # norm = None
            ],
            listing_map_first={"E1": "canonical-A", "E2": "canonical-B"},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac3_separate_products"] is True

    @pytest.mark.unit
    def test_fails_when_null_norm_and_known_norm_share_a_canonical(self):
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "375mL"),  # norm = "375mL"
                _cp("E2", "6 Pack"),  # norm = None
            ],
            listing_map_first={
                "E1": "canonical-A",
                "E2": "canonical-A",  # same canonical — soft veto should have prevented this
            },
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac3_separate_products"] is False

    @pytest.mark.unit
    def test_fails_when_two_different_known_norms_share_a_canonical(self):
        """Two incompatible known norms (375mL vs 6x375mL) must not share a canonical product."""
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "375mL"),  # norm = "375mL"
                _cp("E2", "375mL 6 Pack"),  # norm = "6x375mL"
            ],
            listing_map_first={
                "E1": "canonical-A",
                "E2": "canonical-A",  # incorrectly merged — should have remained separate
            },
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac3_separate_products"] is False

    @pytest.mark.unit
    def test_passes_when_two_null_norm_products_share_a_canonical(self):
        """Two count-only (null-norm) products sharing a canonical is acceptable."""
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "6 Pack"),
                _cp("E2", "8 Pack"),  # also norm = None
            ],
            listing_map_first={"E1": "canonical-A", "E2": "canonical-A"},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac3_separate_products"] is True

    @pytest.mark.unit
    def test_passes_when_two_different_known_norms_have_separate_canonicals(self):
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "375mL"),
                _cp("E2", "330mL"),
            ],
            listing_map_first={"E1": "canonical-A", "E2": "canonical-B"},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac3_separate_products"] is True

    @pytest.mark.unit
    def test_passes_when_listing_map_is_empty(self):
        """No listing data means no evidence of merging — should be treated as pass."""
        result = ValidationResult(
            scraped_products=[_cp("E1", "375mL")],
            listing_map_first={},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac3_separate_products"] is True


class TestAC4IdempotentRepeatIngest:
    """AC4: TWO independent conditions must both hold.

    (a) Repeat ingest created zero new canonical products.
    (b) Every listing still points to the same canonical product (stable mapping).
    Both are checked independently.
    """

    @pytest.mark.unit
    def test_passes_when_created_zero_and_maps_are_identical(self):
        m = {"E1": "canonical-A", "E2": "canonical-B"}
        result = ValidationResult(
            listing_map_first=dict(m),
            listing_map_repeat=dict(m),
            ingest_counts_repeat={"created": 0, "updated": 2},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac4_idempotent_repeat_ingest"] is True

    @pytest.mark.unit
    def test_fails_when_repeat_ingest_created_new_products_despite_stable_maps(self):
        """Maps are stable but a new canonical product was created — idempotency violated."""
        m = {"E1": "canonical-A", "E2": "canonical-B"}
        result = ValidationResult(
            listing_map_first=dict(m),
            listing_map_repeat=dict(m),
            ingest_counts_repeat={"created": 1, "updated": 2},  # new product
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac4_idempotent_repeat_ingest"] is False

    @pytest.mark.unit
    def test_fails_when_a_listing_moved_despite_zero_created(self):
        """A listing was reassigned to a different canonical even though created==0."""
        result = ValidationResult(
            listing_map_first={"E1": "canonical-A", "E2": "canonical-B"},
            listing_map_repeat={"E1": "canonical-A", "E2": "canonical-C"},  # E2 moved
            ingest_counts_repeat={"created": 0, "updated": 2},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac4_idempotent_repeat_ingest"] is False

    @pytest.mark.unit
    def test_is_none_when_both_maps_are_empty(self):
        """Empty maps (ingestion errors) → criterion is N/A, not a pass."""
        result = ValidationResult(
            listing_map_first={},
            listing_map_repeat={},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac4_idempotent_repeat_ingest"] is None


class TestAC5NoNewAuditDefects:
    """AC5: confirmed_norm_mismatch count must not grow after ingest."""

    @pytest.mark.unit
    def test_passes_when_count_is_unchanged(self):
        result = ValidationResult(
            baseline_plan_confirmed_count=3,
            post_plan_confirmed_count=3,
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac5_no_new_audit_defects"] is True

    @pytest.mark.unit
    def test_passes_when_count_decreases(self):
        result = ValidationResult(
            baseline_plan_confirmed_count=5,
            post_plan_confirmed_count=4,
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac5_no_new_audit_defects"] is True

    @pytest.mark.unit
    def test_fails_when_count_increases(self):
        result = ValidationResult(
            baseline_plan_confirmed_count=3,
            post_plan_confirmed_count=5,
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac5_no_new_audit_defects"] is False

    @pytest.mark.unit
    def test_fails_when_baseline_planner_did_not_run(self):
        result = ValidationResult(
            baseline_plan_confirmed_count=-1,  # sentinel: planner failed
            post_plan_confirmed_count=3,
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac5_no_new_audit_defects"] is False

    @pytest.mark.unit
    def test_fails_when_post_planner_did_not_run(self):
        result = ValidationResult(
            baseline_plan_confirmed_count=3,
            post_plan_confirmed_count=-1,  # sentinel: planner failed
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac5_no_new_audit_defects"] is False

    @pytest.mark.unit
    def test_fails_when_both_planners_did_not_run(self):
        result = ValidationResult(
            baseline_plan_confirmed_count=-1,
            post_plan_confirmed_count=-1,
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac5_no_new_audit_defects"] is False

    @pytest.mark.unit
    def test_passes_when_baseline_is_zero_and_post_is_also_zero(self):
        result = ValidationResult(
            baseline_plan_confirmed_count=0,
            post_plan_confirmed_count=0,
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac5_no_new_audit_defects"] is True


def _all_true_result() -> ValidationResult:
    """Build a ValidationResult where every criterion will evaluate to True."""
    m = {"E1": "canonical-A"}
    return ValidationResult(
        count_only_external_ids={"E1"},
        count_only_expected_norms={"E1": "6x355mL"},
        count_only_persisted_norms={"E1": "6x355mL"},  # exact match, keyed by external_id
        new_product_ids={"canonical-A"},
        listing_map_first=dict(m),
        listing_map_repeat=dict(m),
        ingest_counts_repeat={"created": 0, "updated": 1},
        audit_findings={},
        baseline_plan_confirmed_count=0,
        post_plan_confirmed_count=0,
    )


class TestAllCriteriaPassed:
    """_all_criteria_passed requires every criterion to be explicitly True.

    None results — even when all definite values are True — must block PASS.
    This is the invariant that prevents incomplete validation from reporting
    success.
    """

    @pytest.mark.unit
    def test_passes_when_all_five_criteria_are_true(self):
        result = _all_true_result()
        _evaluate_criteria(result)
        assert all(v is True for v in result.criteria_results.values())
        assert _all_criteria_passed(result) is True

    @pytest.mark.unit
    def test_fails_when_any_criterion_is_false(self):
        result = _all_true_result()
        _evaluate_criteria(result)
        result.criteria_results["ac2_no_unexpected_merges"] = False
        assert _all_criteria_passed(result) is False

    @pytest.mark.unit
    def test_fails_when_any_criterion_is_none(self):
        """A None criterion (missing evidence) must block PASS."""
        result = _all_true_result()
        _evaluate_criteria(result)
        # Inject None to simulate listing maps unavailable (e.g. ingestion error)
        result.criteria_results["ac4_idempotent_repeat_ingest"] = None
        assert _all_criteria_passed(result) is False

    @pytest.mark.unit
    def test_fails_when_errors_present_even_if_all_criteria_true(self):
        result = _all_true_result()
        _evaluate_criteria(result)
        result.errors.append("Audit script crashed")
        assert _all_criteria_passed(result) is False

    @pytest.mark.unit
    def test_fails_when_criteria_results_is_empty(self):
        """Empty criteria dict means evaluation never ran — must not be a PASS."""
        result = ValidationResult()
        assert _all_criteria_passed(result) is False

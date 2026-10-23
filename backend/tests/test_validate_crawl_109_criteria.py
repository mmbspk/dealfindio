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
    """AC1: at least one product that ORIGINALLY HAD A COUNT-ONLY API SIZE was enriched.

    Products whose API already supplied a complete size string (e.g. "355mL 6 Pack")
    do NOT count — only products identified via count_only_external_ids qualify.
    """

    @pytest.mark.unit
    def test_passes_when_count_only_product_was_enriched(self):
        """E1 started as '6PK' in the API (count-only) and was enriched to '355mL 6 Pack'."""
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "355mL 6 Pack"),  # was "6PK"; enriched → norm "6x355mL"
                _cp("E2", "375mL"),  # plain size; norm "375mL" (no 'x')
            ],
            count_only_external_ids={"E1"},  # E1 originally had count-only API size
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is True

    @pytest.mark.unit
    def test_fails_when_count_only_product_was_not_enriched(self):
        """E1 started as '6PK' but volume couldn't be extracted — fell back to '6 Pack'."""
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "6 Pack"),  # was "6PK"; not enriched → norm None
            ],
            count_only_external_ids={"E1"},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False

    @pytest.mark.unit
    def test_fails_when_count_only_external_ids_is_empty(self):
        """If no products had count-only API sizes, enrichment cannot be proved."""
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "355mL 6 Pack"),  # multipack norm present, but API was already complete
            ],
            count_only_external_ids=set(),  # empty — no count-only products found
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False

    @pytest.mark.unit
    def test_fails_when_api_complete_multipack_not_in_count_only_set(self):
        """E1 was '355mL 6 Pack' directly from the API — its multipack norm must not count."""
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "355mL 6 Pack"),  # norm "6x355mL" — but API gave this directly
                _cp("E2", "6 Pack"),  # count-only fallback
            ],
            count_only_external_ids={"E2"},  # only E2 was count-only; E2 was NOT enriched
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False

    @pytest.mark.unit
    def test_fails_when_scraped_products_is_empty(self):
        result = ValidationResult(scraped_products=[], count_only_external_ids=set())
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

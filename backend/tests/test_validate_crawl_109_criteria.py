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
    """AC1: at least one product FROM THIS CRAWL was enriched to a multipack norm."""

    @pytest.mark.unit
    def test_passes_when_at_least_one_multipack_norm_in_scraped_products(self):
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "355mL 6 Pack"),  # normalize → "6x355mL"
                _cp("E2", "375mL"),  # normalize → "375mL" (no 'x')
            ]
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is True

    @pytest.mark.unit
    def test_fails_when_no_scraped_product_has_multipack_norm(self):
        result = ValidationResult(
            scraped_products=[
                _cp("E1", "375mL"),  # "375mL" — no 'x'
                _cp("E2", "6 Pack"),  # normalize → None
            ]
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False

    @pytest.mark.unit
    def test_fails_when_scraped_products_is_empty(self):
        result = ValidationResult(scraped_products=[])
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is False

    @pytest.mark.unit
    def test_fails_when_all_products_have_null_package_size(self):
        result = ValidationResult(scraped_products=[_cp("E1", None), _cp("E2", None)])
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
    """AC3: no two scraped products with different norms map to the same canonical."""

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
    """AC4: listing map must be identical before and after repeat ingest."""

    @pytest.mark.unit
    def test_passes_when_maps_are_identical(self):
        m = {"E1": "canonical-A", "E2": "canonical-B"}
        result = ValidationResult(
            listing_map_first=dict(m),
            listing_map_repeat=dict(m),
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac4_idempotent_repeat_ingest"] is True

    @pytest.mark.unit
    def test_fails_when_a_listing_moved_to_a_different_canonical(self):
        result = ValidationResult(
            listing_map_first={"E1": "canonical-A", "E2": "canonical-B"},
            listing_map_repeat={"E1": "canonical-A", "E2": "canonical-C"},  # E2 moved
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac4_idempotent_repeat_ingest"] is False

    @pytest.mark.unit
    def test_is_none_when_both_maps_are_empty(self):
        """Empty maps (e.g. dry-run) → criterion is N/A, not a pass."""
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

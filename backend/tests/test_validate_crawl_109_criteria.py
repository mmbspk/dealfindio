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

    @pytest.mark.unit
    def test_not_exercised_when_count_only_found_but_all_unenrichable(self):
        """Count-only products exist but no product name contains volume evidence.

        This is the real-world tea-bag case: PackageSize='100 Pack' but the name
        'Dilmah Tea Bags' has no per-bag volume, so combine_count_with_volume returns None.
        AC1 must be None (not exercised), not False (failure) — the enrichment path is
        not broken; there simply is no qualifying product in the sampled category.
        """
        result = ValidationResult(
            count_only_external_ids={"E1", "E2"},
            count_only_expected_norms={"E1": None, "E2": None},  # no volume in names
            count_only_persisted_norms={},
        )
        _evaluate_criteria(result)
        assert result.criteria_results["ac1_enrichment_fired_on_scraped_data"] is None

    @pytest.mark.unit
    def test_fails_when_expected_norms_empty_despite_count_only_ids(self):
        """Empty count_only_expected_norms must not qualify as 'not exercised'.

        all(v is None for v in {}.values()) is True for an empty dict, so without
        an explicit non-empty guard, missing evidence would be misclassified as a
        legitimate not-exercised result.  An empty mapping means the norm lookup
        never ran — treat that as a failure, not as evidence of correct behaviour.
        """
        result = ValidationResult(
            count_only_external_ids={"E1"},
            count_only_expected_norms={},  # empty — evidence was never populated
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


# ── Regression: _identify_count_only_external_ids ─────────────────────────────

_identify_count_only = _mod._identify_count_only_external_ids


class TestCountOnlyIdentification:
    """_identify_count_only_external_ids must match all count-only formats the scraper uses.

    The scraper's regex is r"^(\\d+)\\s*(?:ea|each|pk|pack)$" (catalog_woolworths.py ~line 605).
    This class ensures the validator's detection is symmetric with it, including the count > 1
    filter (count=1 is suppressed to None by the scraper, not enriched).
    """

    @pytest.mark.unit
    def test_matches_uppercase_pk(self):
        assert "100" in _identify_count_only([{"Stockcode": "100", "PackageSize": "6PK"}])

    @pytest.mark.unit
    def test_matches_spaced_pack(self):
        assert "100" in _identify_count_only([{"Stockcode": "100", "PackageSize": "6 Pack"}])

    @pytest.mark.unit
    def test_matches_lowercase_pack(self):
        assert "100" in _identify_count_only([{"Stockcode": "100", "PackageSize": "30 pack"}])

    @pytest.mark.unit
    def test_matches_large_count(self):
        assert "100" in _identify_count_only([{"Stockcode": "100", "PackageSize": "100 pack"}])

    @pytest.mark.unit
    def test_matches_ea(self):
        assert "100" in _identify_count_only([{"Stockcode": "100", "PackageSize": "6EA"}])

    @pytest.mark.unit
    def test_matches_each(self):
        assert "100" in _identify_count_only([{"Stockcode": "100", "PackageSize": "6 each"}])

    @pytest.mark.unit
    def test_excludes_count_one_ea(self):
        """count=1 is suppressed to None by the scraper — must not be a candidate."""
        assert _identify_count_only([{"Stockcode": "100", "PackageSize": "1EA"}]) == set()

    @pytest.mark.unit
    def test_excludes_count_one_pack(self):
        assert _identify_count_only([{"Stockcode": "100", "PackageSize": "1 Pack"}]) == set()

    @pytest.mark.unit
    def test_excludes_pre_enriched_size(self):
        """'375mL x 6 pack' already has volume — not a count-only raw value."""
        assert (
            _identify_count_only([{"Stockcode": "100", "PackageSize": "375mL x 6 pack"}]) == set()
        )

    @pytest.mark.unit
    def test_excludes_volume_only_size(self):
        assert _identify_count_only([{"Stockcode": "100", "PackageSize": "375mL"}]) == set()

    @pytest.mark.unit
    def test_excludes_missing_package_size(self):
        assert _identify_count_only([{"Stockcode": "100"}]) == set()

    @pytest.mark.unit
    def test_excludes_null_package_size(self):
        assert _identify_count_only([{"Stockcode": "100", "PackageSize": None}]) == set()

    @pytest.mark.unit
    def test_returns_multiple_matches(self):
        items = [
            {"Stockcode": "A", "PackageSize": "6PK"},
            {"Stockcode": "B", "PackageSize": "30 pack"},
            {"Stockcode": "C", "PackageSize": "375mL"},
        ]
        assert _identify_count_only(items) == {"A", "B"}

    @pytest.mark.unit
    def test_skips_item_with_no_stockcode(self):
        assert _identify_count_only([{"PackageSize": "6PK"}]) == set()


# ── Regression: runtime bugs fixed in PR #111 ─────────────────────────────────


class TestRuntimeBugRegressions:
    """Guards against the two live-run bugs discovered post-merge of PR #110."""

    @pytest.mark.unit
    def test_session_local_not_imported_from_app_deps(self):
        """_SessionLocal must NOT be imported from app.deps.

        The FastAPI app lifecycle never starts in a standalone script, so
        app.deps._SessionLocal is None until init_database() is called.  The
        fix in PR #111 creates its own engine+sessionmaker inside main().
        This test verifies the import-time side-effect is gone.
        """
        import ast
        import pathlib

        script = pathlib.Path(__file__).parent.parent / "scripts" / "validate_crawl_109.py"
        tree = ast.parse(script.read_text())

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "app.deps" in node.module:
                    imported = [alias.name for alias in node.names]
                    assert "_SessionLocal" not in imported, (
                        f"validate_crawl_109.py still imports _SessionLocal from {node.module} "
                        "— this causes a NoneType error in standalone (non-FastAPI) execution"
                    )

    @pytest.mark.unit
    async def test_parse_products_receives_full_dict_not_list(self):
        """parse_products expects raw: dict with a 'products' key, not a bare list.

        The pre-fix code called parse_products(raw_products, target) where
        raw_products was the list already extracted from raw.get("products", []).
        The scraper calls raw.get("products", []) internally, so passing the list
        raised AttributeError: 'list' object has no attribute 'get'.
        """
        from unittest.mock import MagicMock

        from scrapers.catalog_woolworths import WoolworthsCatalogScraper

        mock_config = MagicMock()
        mock_config.min_delay_s = 0
        mock_config.max_delay_s = 0
        mock_config.burst_limit = 100
        mock_config.max_pages_per_category = 1

        scraper = WoolworthsCatalogScraper(chain="woolworths", config=mock_config)
        mock_category = MagicMock()
        mock_category.name = "Test"
        mock_category.id = "test-id"

        # Passing a bare list must raise AttributeError — the regression test
        # confirms we no longer call it that way.
        with pytest.raises(AttributeError, match="get"):
            await scraper.parse_products([], mock_category)

        # Passing the correct full dict must succeed (even if products list is empty).
        result = await scraper.parse_products({"products": []}, mock_category)
        assert result == []

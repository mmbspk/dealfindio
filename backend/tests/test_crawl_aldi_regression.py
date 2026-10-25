"""
Story #115 — ALDI Fresh Crawl Validation: CI regression fixtures.

Three test classes:

  TestALDIRawExtraction — verifies _extract_raw_items correctly handles the
    ALDI nested {"pages": [{"data": [...]}]} structure vs Woolworths flat
    {"products": [...]}. Chain field mappings via _CHAIN_ITEM_FIELDS.

  TestALDICountOnlyIdentification — verifies _identify_count_only_external_ids
    uses the correct ALDI fields ("sku", "sellingSize") and correctly identifies
    count-only items from real fixture data.

  TestALDIScraperParsingRegression — runs each fixture scenario through
    _product_from_api and asserts on package_size + normalisation outcome.
    Covers single-unit suppression, count-without-volume fallback,
    count-with-volume enrichment, plain sizes, null sizes, and skip cases.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.services.package_size import normalize_package_size
from scrapers.catalog_aldi import _product_from_api

# ── Module import for validator internals ─────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "validate_crawl_109_mod_aldi",
    _SCRIPTS_DIR / "validate_crawl_109.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["validate_crawl_109_mod_aldi"] = _mod
_spec.loader.exec_module(_mod)

_extract_raw_items = _mod._extract_raw_items
_identify_count_only_external_ids = _mod._identify_count_only_external_ids

# ── Fixtures ──────────────────────────────────────────────────────────────────

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "crawl_109" / "aldi_raw_items.json"


@pytest.fixture(scope="module")
def aldi_raw_items() -> list[dict]:
    return json.loads(_FIXTURE_PATH.read_text())


def _find(items: list[dict], scenario: str) -> dict:
    return next(i for i in items if i["_scenario"] == scenario)


# ── Raw extraction ────────────────────────────────────────────────────────────


class TestALDIRawExtraction:
    """_extract_raw_items must handle ALDI's nested pages structure.

    ALDI crawl_category returns: {"pages": [{"data": [item, ...]}, ...]}
    Woolworths returns: {"products": [item, ...]}

    The extractor must flatten all pages for ALDI and never confuse the two.
    """

    @pytest.mark.unit
    def test_aldi_single_page_extracted(self) -> None:
        raw = {"pages": [{"data": [{"sku": "A1"}, {"sku": "A2"}]}]}
        items = _extract_raw_items(raw, "aldi")
        assert [i["sku"] for i in items] == ["A1", "A2"]

    @pytest.mark.unit
    def test_aldi_multi_page_flattened(self) -> None:
        raw = {
            "pages": [
                {"data": [{"sku": "A1"}, {"sku": "A2"}]},
                {"data": [{"sku": "A3"}]},
            ]
        }
        items = _extract_raw_items(raw, "aldi")
        assert [i["sku"] for i in items] == ["A1", "A2", "A3"]

    @pytest.mark.unit
    def test_aldi_empty_pages_returns_empty_list(self) -> None:
        assert _extract_raw_items({"pages": []}, "aldi") == []

    @pytest.mark.unit
    def test_aldi_missing_pages_key_returns_empty(self) -> None:
        assert _extract_raw_items({}, "aldi") == []

    @pytest.mark.unit
    def test_woolworths_flat_products_unaffected(self) -> None:
        raw = {"products": [{"Stockcode": "W1"}, {"Stockcode": "W2"}]}
        items = _extract_raw_items(raw, "woolworths")
        assert [i["Stockcode"] for i in items] == ["W1", "W2"]

    @pytest.mark.unit
    def test_aldi_raw_does_not_fall_through_to_woolworths_path(self) -> None:
        """ALDI raw given Woolworths chain must not match 'pages' structure."""
        raw = {"pages": [{"data": [{"sku": "A1"}]}]}
        # With chain="woolworths" the extractor looks for "products" key
        items = _extract_raw_items(raw, "woolworths")
        assert items == [], "Woolworths extractor must not read ALDI 'pages' structure"


# ── Count-only identification (ALDI fields) ───────────────────────────────────


class TestALDICountOnlyIdentification:
    """_identify_count_only_external_ids must use sku + sellingSize for ALDI.

    The validator uses _CHAIN_ITEM_FIELDS to select id_field and size_field
    per chain. For ALDI: id_field='sku', size_field='sellingSize'.
    """

    @pytest.mark.unit
    def test_count_only_identified_by_aldi_fields(self, aldi_raw_items) -> None:
        """Items with count-only sellingSize (N≥2) must be identified by sku."""
        count_only_ids = _identify_count_only_external_ids(aldi_raw_items, "aldi")
        # Garlic 3 Pack, Hass Avocados 5 Pack, Mango 4 Pack, Heineken 6 each
        assert "000000000000380071" in count_only_ids  # Garlic 3 Pack
        assert "000000000000380094" in count_only_ids  # Hass Avocados 5 Pack
        assert "000000000000380116" in count_only_ids  # Kensington Pride Mango 4 Pack
        assert "000000000000380300" in count_only_ids  # Heineken 6 each

    @pytest.mark.unit
    def test_single_unit_excluded_from_count_only(self, aldi_raw_items) -> None:
        """'1 each' items must not appear in count_only_external_ids."""
        count_only_ids = _identify_count_only_external_ids(aldi_raw_items, "aldi")
        assert "000000000000364043" not in count_only_ids  # Lemon Each (1 each)

    @pytest.mark.unit
    def test_plain_size_items_not_count_only(self, aldi_raw_items) -> None:
        """Items with weight/volume sizes must not appear in count_only_external_ids."""
        count_only_ids = _identify_count_only_external_ids(aldi_raw_items, "aldi")
        assert "000000000000364045" not in count_only_ids  # Strawberries 250 g
        assert "000000000000380200" not in count_only_ids  # Full Cream Milk 2 L

    @pytest.mark.unit
    def test_null_selling_size_not_count_only(self, aldi_raw_items) -> None:
        count_only_ids = _identify_count_only_external_ids(aldi_raw_items, "aldi")
        assert "000000000000364044" not in count_only_ids  # per-kg null sellingSize

    @pytest.mark.unit
    def test_aldi_chain_does_not_match_woolworths_fields(self) -> None:
        """Items using Woolworths field names must yield no results under ALDI chain."""
        woolworths_items = [{"Stockcode": "W1", "PackageSize": "6PK"}]
        ids = _identify_count_only_external_ids(woolworths_items, "aldi")
        assert ids == set(), "ALDI extractor must not read Woolworths 'Stockcode'/'PackageSize'"

    @pytest.mark.unit
    def test_woolworths_chain_does_not_match_aldi_fields(self) -> None:
        """Items using ALDI field names must yield no results under Woolworths chain."""
        aldi_items = [{"sku": "A1", "sellingSize": "6 each"}]
        ids = _identify_count_only_external_ids(aldi_items, "woolworths")
        assert ids == set(), "Woolworths extractor must not read ALDI 'sku'/'sellingSize'"


# ── ALDI scraper parsing regression ──────────────────────────────────────────


class TestALDIScraperParsingRegression:
    """Regression: _product_from_api must correctly handle all ALDI sellingSize variants.

    ALDI sellingSize formats encountered in the wild:
      - "N each" / "N ea" / "N pack" (count-only, N>1) → enriched or "N Pack"
      - "1 each" (single unit) → package_size=None
      - "250 g", "2 L" (plain size) → pass through unchanged
      - null (per-kg loose items) → package_size=None
    """

    @pytest.mark.unit
    def test_count_without_volume_falls_back_to_n_pack(self, aldi_raw_items) -> None:
        item = _find(aldi_raw_items, "count_without_volume_produce")
        cp = _product_from_api(item, "Fruits & Vegetables")
        assert cp is not None
        assert cp.package_size == "3 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_count_5each_falls_back_to_5_pack(self, aldi_raw_items) -> None:
        item = _find(aldi_raw_items, "count_without_volume_5pack")
        cp = _product_from_api(item, "Fruits & Vegetables")
        assert cp is not None
        assert cp.package_size == "5 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_count_4each_falls_back_to_4_pack(self, aldi_raw_items) -> None:
        item = _find(aldi_raw_items, "count_without_volume_4pack")
        cp = _product_from_api(item, "Fruits & Vegetables")
        assert cp is not None
        assert cp.package_size == "4 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_single_unit_suppressed(self, aldi_raw_items) -> None:
        """'1 each' must produce package_size=None — same as Woolworths '1EA'."""
        item = _find(aldi_raw_items, "single_unit_suppressed")
        cp = _product_from_api(item, "Fruits & Vegetables")
        assert cp is not None
        assert cp.package_size is None

    @pytest.mark.unit
    def test_plain_weight_size_preserved(self, aldi_raw_items) -> None:
        item = _find(aldi_raw_items, "plain_weight_size")
        cp = _product_from_api(item, "Dairy")
        assert cp is not None
        assert cp.package_size == "250 g"
        assert normalize_package_size(cp.package_size) == "250g"

    @pytest.mark.unit
    def test_plain_volume_size_preserved(self, aldi_raw_items) -> None:
        item = _find(aldi_raw_items, "plain_volume_size")
        cp = _product_from_api(item, "Dairy")
        assert cp is not None
        assert cp.package_size == "2 L"
        assert normalize_package_size(cp.package_size) == "2L"

    @pytest.mark.unit
    def test_null_selling_size_produces_null_package_size(self, aldi_raw_items) -> None:
        item = _find(aldi_raw_items, "null_selling_size_per_kg")
        cp = _product_from_api(item, "Fruits & Vegetables")
        assert cp is not None
        assert cp.package_size is None

    @pytest.mark.unit
    def test_discontinued_item_skipped(self, aldi_raw_items) -> None:
        item = _find(aldi_raw_items, "discontinued_skipped")
        cp = _product_from_api(item, "Specials")
        assert cp is None, "_product_from_api must return None for discontinued items"

    @pytest.mark.unit
    def test_no_sku_item_skipped(self, aldi_raw_items) -> None:
        item = _find(aldi_raw_items, "no_sku_skipped")
        cp = _product_from_api(item, "Test")
        assert cp is None, "_product_from_api must return None when sku is absent"

    @pytest.mark.unit
    def test_count_with_volume_in_name_enriched(self, aldi_raw_items) -> None:
        """'6 each' + '330mL' in name → enriched to '330mL 6 Pack'; norm='6x330mL'."""
        item = _find(aldi_raw_items, "count_with_volume_in_name")
        cp = _product_from_api(item, "Alcohol > Beer")
        assert cp is not None
        assert cp.package_size == "330mL 6 Pack"
        assert normalize_package_size(cp.package_size) == "6x330mL"

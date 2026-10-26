"""
Story #117 — Coles Fresh Crawl Validation: CI regression fixtures.

Three test classes:

  TestColesRawExtraction — verifies _extract_raw_items correctly handles the
    Coles nested {"pages": [{"pageProps": {"searchResults": {"results": [...]}}}]}
    structure vs ALDI's pages/data, IGA's pages/items, and Woolworths' products.

  TestColesCountOnlyIdentification — verifies _identify_count_only_external_ids
    detects items where item["size"] is a bare count (e.g. "18 Pack", "6PK"),
    excludes items with unit sizes (e.g. "375mL", "2L"), and does not bleed into
    other chain field mappings.

  TestColesScraperParsingRegression — runs each fixture scenario through
    _parse_item and asserts on package_size + normalisation outcome.
    Covers _augment_with_pack_count enrichment (unit size + pack count from name),
    plain volume/weight sizes, bare count-only sizes, null sizes, and skip cases.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from app.services.package_size import normalize_package_size
from scrapers.catalog_base import CategoryNode
from scrapers.catalog_coles import _parse_item

# ── Module import for validator internals ─────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "validate_crawl_109_mod_coles",
    _SCRIPTS_DIR / "validate_crawl_109.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["validate_crawl_109_mod_coles"] = _mod
_spec.loader.exec_module(_mod)

_extract_raw_items = _mod._extract_raw_items
_identify_count_only_external_ids = _mod._identify_count_only_external_ids

# ── Fixtures ──────────────────────────────────────────────────────────────────

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "crawl_109" / "coles_raw_items.json"

_TODAY = date.today()
_EMPTY_L2_MAP: dict = {}


def _cat(name: str) -> CategoryNode:
    return CategoryNode(id=name.lower().replace(" ", "-"), name=name)


@pytest.fixture(scope="module")
def coles_raw_items() -> list[dict]:
    return json.loads(_FIXTURE_PATH.read_text())


def _find(items: list[dict], scenario: str) -> dict:
    return next(i for i in items if i["_scenario"] == scenario)


def _wrap_page(items: list[dict]) -> dict:
    """Wrap a flat item list in the Coles crawl_category page structure."""
    return {
        "pageProps": {
            "searchResults": {
                "results": items,
                "total": len(items),
            }
        }
    }


# ── Raw extraction ────────────────────────────────────────────────────────────


class TestColesRawExtraction:
    """_extract_raw_items must handle Coles' nested pageProps/searchResults/results.

    Coles crawl_category returns:
      {"pages": [{"pageProps": {"searchResults": {"results": [item, ...]}}}, ...]}

    ALDI:       {"pages": [{"data": [item, ...]}]}
    IGA:        {"pages": [{"items": [item, ...]}]}
    Woolworths: {"products": [item, ...]}
    """

    @pytest.mark.unit
    def test_coles_single_page_extracted(self) -> None:
        raw = {"pages": [_wrap_page([{"id": 1}, {"id": 2}])]}
        items = _extract_raw_items(raw, "coles")
        assert [i["id"] for i in items] == [1, 2]

    @pytest.mark.unit
    def test_coles_multi_page_flattened(self) -> None:
        raw = {
            "pages": [
                _wrap_page([{"id": 1}, {"id": 2}]),
                _wrap_page([{"id": 3}]),
            ]
        }
        items = _extract_raw_items(raw, "coles")
        assert [i["id"] for i in items] == [1, 2, 3]

    @pytest.mark.unit
    def test_coles_empty_pages_returns_empty_list(self) -> None:
        assert _extract_raw_items({"pages": []}, "coles") == []

    @pytest.mark.unit
    def test_coles_missing_pages_key_returns_empty(self) -> None:
        assert _extract_raw_items({}, "coles") == []

    @pytest.mark.unit
    def test_woolworths_flat_products_unaffected(self) -> None:
        raw = {"products": [{"Stockcode": "W1"}, {"Stockcode": "W2"}]}
        items = _extract_raw_items(raw, "woolworths")
        assert [i["Stockcode"] for i in items] == ["W1", "W2"]

    @pytest.mark.unit
    def test_coles_raw_does_not_use_iga_items_key(self) -> None:
        """Coles pages use pageProps/searchResults/results, not 'items'."""
        raw = {
            "pages": [
                {"items": [{"id": 99}], "pageProps": {"searchResults": {"results": [{"id": 1}]}}}
            ]
        }
        items = _extract_raw_items(raw, "coles")
        assert [i["id"] for i in items] == [1], "Coles extractor must read searchResults.results"


# ── Count-only identification (Coles flat string size) ───────────────────────


class TestColesCountOnlyIdentification:
    """_identify_count_only_external_ids must use id + size fields for Coles.

    Coles raw items have a flat string size field.  The count-only regex
    (_RE_COUNT_ONLY_API_SIZE) applies directly — same path as Woolworths/ALDI.
    """

    @pytest.mark.unit
    def test_bare_count_in_size_identified(self, coles_raw_items) -> None:
        """Items with size='18 Pack' must be identified by id."""
        count_only_ids = _identify_count_only_external_ids(coles_raw_items, "coles")
        assert "1111003" in count_only_ids  # 18 Pack (enrichable via name volume)
        assert "1111009" in count_only_ids  # 18 Pack (contradictory count — falls back)
        assert "1111010" in count_only_ids  # 6 Pack (multiple volumes — falls back)
        assert "1111011" in count_only_ids  # 8 Pack (weight-only name — falls back)

    @pytest.mark.unit
    def test_unit_size_items_not_count_only(self, coles_raw_items) -> None:
        """Items with volume/weight sizes must not appear in count_only_external_ids."""
        count_only_ids = _identify_count_only_external_ids(coles_raw_items, "coles")
        assert "1111001" not in count_only_ids  # 375mL
        assert "1111002" not in count_only_ids  # 2L
        assert "1111005" not in count_only_ids  # 200g
        assert "1111006" not in count_only_ids  # 1kg

    @pytest.mark.unit
    def test_multipack_size_not_count_only(self, coles_raw_items) -> None:
        """Items with size='6x375mL' are NOT count-only (already a multipack norm)."""
        count_only_ids = _identify_count_only_external_ids(coles_raw_items, "coles")
        assert "1111004" not in count_only_ids  # 6x375mL

    @pytest.mark.unit
    def test_null_size_not_count_only(self, coles_raw_items) -> None:
        """Items with null size field must not appear in count_only_external_ids."""
        count_only_ids = _identify_count_only_external_ids(coles_raw_items, "coles")
        assert "1111007" not in count_only_ids  # null size
        assert "1111008" not in count_only_ids  # null size

    @pytest.mark.unit
    def test_coles_chain_does_not_match_woolworths_fields(self) -> None:
        """Items using Woolworths field names must yield no results under coles chain."""
        ww_items = [{"Stockcode": "W1", "PackageSize": "6PK"}]
        ids = _identify_count_only_external_ids(ww_items, "coles")
        assert ids == set(), "Coles extractor must not read Woolworths 'Stockcode'/'PackageSize'"

    @pytest.mark.unit
    def test_woolworths_chain_does_not_match_coles_fields(self) -> None:
        """Items using Coles field names must yield no results under woolworths chain."""
        coles_items = [{"id": 123, "size": "18 Pack"}]
        ids = _identify_count_only_external_ids(coles_items, "woolworths")
        assert ids == set(), "Woolworths extractor must not read Coles 'id'/'size'"


# ── Coles scraper parsing regression ─────────────────────────────────────────


class TestColesScraperParsingRegression:
    """Regression: _parse_item must correctly handle all Coles size variants.

    Coles size formats encountered in the wild:
      item["size"] = "375mL"  + pack count in name  → "375mL 24 Pack" via _augment_with_pack_count
      item["size"] = "6x375mL"                      → "6x375mL" (already multipack)
      item["size"] = "18 Pack" + volume in name     → "375mL 18 Pack" via combine_count_with_volume
      item["size"] = "18 Pack", contradictory count → "18 Pack" unchanged (conservative fallback)
      item["size"] = "6 Pack", multiple volumes     → "6 Pack" unchanged (ambiguous, conservative)
      item["size"] = "8 Pack", weight-only name     → "8 Pack" unchanged (no mL/L to combine)
      item["size"] = "2L" / "200g" / "1kg"         → plain size passes through
      item["size"] = null + size in name/desc       → "375mL 6 Pack" (name-fallback path)
      item["size"] = null, no size anywhere         → package_size=None
      item["_type"] = "SINGLE_TILE_AD"              → returns None
      pricing.now = null                            → returns None
    """

    @pytest.mark.unit
    def test_volume_with_pack_in_name_augmented(self, coles_raw_items) -> None:
        """size='375mL' + '24 Pack' in name → '375mL 24 Pack'; norm='24x375mL'."""
        item = _find(coles_raw_items, "volume_with_pack_in_name")
        cp = _parse_item(item, _cat("Drinks"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "375mL 24 Pack"
        assert normalize_package_size(cp.package_size) == "24x375mL"

    @pytest.mark.unit
    def test_single_volume_no_pack(self, coles_raw_items) -> None:
        """size='2L' with no pack count — passes through unchanged; norm='2L'."""
        item = _find(coles_raw_items, "single_volume_no_pack")
        cp = _parse_item(item, _cat("Drinks"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "2L"
        assert normalize_package_size(cp.package_size) == "2L"

    @pytest.mark.unit
    def test_bare_count_enriched_from_name(self, coles_raw_items) -> None:
        """size='18 Pack' + name has '375mL' → combine_count_with_volume → '375mL 18 Pack'; norm='18x375mL'."""
        item = _find(coles_raw_items, "bare_count_in_size_field")
        cp = _parse_item(item, _cat("Drinks"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "375mL 18 Pack"
        assert normalize_package_size(cp.package_size) == "18x375mL"

    @pytest.mark.unit
    def test_bare_count_contradictory_count_falls_back(self, coles_raw_items) -> None:
        """size='18 Pack' but name says '12 Pack' → contradictory count → falls back to '18 Pack'; norm=None."""
        item = _find(coles_raw_items, "bare_count_contradictory_count")
        cp = _parse_item(item, _cat("Drinks"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "18 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_bare_count_multiple_volumes_falls_back(self, coles_raw_items) -> None:
        """size='6 Pack' + name has two different volumes → ambiguous → falls back to '6 Pack'; norm=None."""
        item = _find(coles_raw_items, "bare_count_multiple_volumes")
        cp = _parse_item(item, _cat("Drinks"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "6 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_bare_count_weight_only_name_falls_back(self, coles_raw_items) -> None:
        """size='8 Pack' + name has only 560g (weight) → no mL/L → falls back to '8 Pack'; norm=None."""
        item = _find(coles_raw_items, "bare_count_weight_only_name")
        cp = _parse_item(item, _cat("Deli"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "8 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_multipack_already_in_size(self, coles_raw_items) -> None:
        """size='6x375mL' (already canonical) → '6x375mL'; norm='6x375mL'."""
        item = _find(coles_raw_items, "multipack_already_in_size")
        cp = _parse_item(item, _cat("Liquorland"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "6x375mL"
        assert normalize_package_size(cp.package_size) == "6x375mL"

    @pytest.mark.unit
    def test_gram_weight_size(self, coles_raw_items) -> None:
        """size='200g' — plain weight; norm='200g'."""
        item = _find(coles_raw_items, "gram_weight_size")
        cp = _parse_item(item, _cat("Dairy, Eggs & Fridge"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "200g"
        assert normalize_package_size(cp.package_size) == "200g"

    @pytest.mark.unit
    def test_kilogram_weight_size(self, coles_raw_items) -> None:
        """size='1kg' — plain kilogram; norm='1kg'."""
        item = _find(coles_raw_items, "kilogram_weight_size")
        cp = _parse_item(item, _cat("Pantry"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "1kg"
        assert normalize_package_size(cp.package_size) == "1kg"

    @pytest.mark.unit
    def test_null_size_pack_from_name(self, coles_raw_items) -> None:
        """Null size field — scraper parses '375mL 6 Pack' from name; norm='6x375mL'."""
        item = _find(coles_raw_items, "null_size_pack_from_name")
        cp = _parse_item(item, _cat("Drinks"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size == "375mL 6 Pack"
        assert normalize_package_size(cp.package_size) == "6x375mL"

    @pytest.mark.unit
    def test_null_size_no_fallback_produces_none(self, coles_raw_items) -> None:
        """Null size with no parseable size in name/description → package_size=None."""
        item = _find(coles_raw_items, "null_size_no_fallback")
        cp = _parse_item(item, _cat("Deli"), _TODAY, _EMPTY_L2_MAP)
        assert cp is not None
        assert cp.package_size is None

    @pytest.mark.unit
    def test_ad_tile_skipped(self, coles_raw_items) -> None:
        """_type='SINGLE_TILE_AD' — _parse_item must return None."""
        item = _find(coles_raw_items, "ad_tile_skipped")
        cp = _parse_item(item, _cat("Drinks"), _TODAY, _EMPTY_L2_MAP)
        assert cp is None, "_parse_item must return None for non-PRODUCT _type"

    @pytest.mark.unit
    def test_no_price_skipped(self, coles_raw_items) -> None:
        """pricing.now=null — _parse_item must return None when price is absent."""
        item = _find(coles_raw_items, "no_price_skipped")
        cp = _parse_item(item, _cat("Drinks"), _TODAY, _EMPTY_L2_MAP)
        assert cp is None, "_parse_item must return None when pricing.now is null"

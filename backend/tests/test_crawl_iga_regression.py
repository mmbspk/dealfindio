"""
Story #116 — IGA Fresh Crawl Validation: CI regression fixtures.

Three test classes:

  TestIGARawExtraction — verifies _extract_raw_items correctly handles the
    IGA nested {"pages": [{"total": N, "count": N, "items": [...]}]} structure
    vs ALDI's pages/data and Woolworths' flat products key.

  TestIGACountOnlyIdentification — verifies _identify_count_only_external_ids
    uses the IGA-specific path (_is_iga_count_only) for the nested unitOfSize
    dict instead of the flat-string regex used for Woolworths/ALDI.

  TestIGAScraperParsingRegression — runs each fixture scenario through
    _parse_item and asserts on package_size + normalisation outcome.
    Covers single-unit suppression, count-without-volume fallback,
    count-with-volume enrichment, plain weight/volume sizes, null sizes,
    and the no-productId skip case.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from app.services.package_size import normalize_package_size
from scrapers.catalog_iga import _parse_item

# ── Module import for validator internals ─────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "validate_crawl_109_mod_iga",
    _SCRIPTS_DIR / "validate_crawl_109.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["validate_crawl_109_mod_iga"] = _mod
_spec.loader.exec_module(_mod)

_extract_raw_items = _mod._extract_raw_items
_identify_count_only_external_ids = _mod._identify_count_only_external_ids
_is_iga_count_only = _mod._is_iga_count_only

# ── Fixtures ──────────────────────────────────────────────────────────────────

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "crawl_109" / "iga_raw_items.json"

_TODAY = date.today()


@pytest.fixture(scope="module")
def iga_raw_items() -> list[dict]:
    return json.loads(_FIXTURE_PATH.read_text())


def _find(items: list[dict], scenario: str) -> dict:
    return next(i for i in items if i["_scenario"] == scenario)


# ── Raw extraction ────────────────────────────────────────────────────────────


class TestIGARawExtraction:
    """_extract_raw_items must handle IGA's nested pages/items structure.

    IGA crawl_category returns: {"pages": [{"total": N, "count": N, "items": [...]}]}
    ALDI returns: {"pages": [{"data": [...]}]}
    Woolworths returns: {"products": [...]}

    The extractor must flatten all pages correctly for each chain.
    """

    @pytest.mark.unit
    def test_iga_single_page_extracted(self) -> None:
        raw = {
            "pages": [{"total": 2, "count": 2, "items": [{"productId": "A"}, {"productId": "B"}]}]
        }
        items = _extract_raw_items(raw, "iga")
        assert [i["productId"] for i in items] == ["A", "B"]

    @pytest.mark.unit
    def test_iga_multi_page_flattened(self) -> None:
        raw = {
            "pages": [
                {"total": 3, "count": 2, "items": [{"productId": "A"}, {"productId": "B"}]},
                {"total": 3, "count": 1, "items": [{"productId": "C"}]},
            ]
        }
        items = _extract_raw_items(raw, "iga")
        assert [i["productId"] for i in items] == ["A", "B", "C"]

    @pytest.mark.unit
    def test_iga_empty_pages_returns_empty_list(self) -> None:
        assert _extract_raw_items({"pages": []}, "iga") == []

    @pytest.mark.unit
    def test_iga_missing_pages_key_returns_empty(self) -> None:
        assert _extract_raw_items({}, "iga") == []

    @pytest.mark.unit
    def test_woolworths_flat_products_unaffected(self) -> None:
        raw = {"products": [{"Stockcode": "W1"}, {"Stockcode": "W2"}]}
        items = _extract_raw_items(raw, "woolworths")
        assert [i["Stockcode"] for i in items] == ["W1", "W2"]

    @pytest.mark.unit
    def test_iga_raw_does_not_use_aldi_data_key(self) -> None:
        """IGA pages use 'items', not 'data' — ALDI's key must not bleed through."""
        raw = {"pages": [{"data": [{"productId": "A"}], "items": [{"productId": "B"}]}]}
        items = _extract_raw_items(raw, "iga")
        assert [i["productId"] for i in items] == [
            "B"
        ], "IGA extractor must read 'items', not 'data'"


# ── Count-only identification (IGA nested unitOfSize) ─────────────────────────


class TestIGACountOnlyIdentification:
    """_identify_count_only_external_ids must use the IGA-specific dict path.

    IGA's size is unitOfSize={"size": N, "type": "each"/"ea"/"pk"/"pack"}.
    The flat-string regex path used for Woolworths/ALDI cannot apply here.
    """

    @pytest.mark.unit
    def test_count_only_identified_by_iga_fields(self, iga_raw_items) -> None:
        """Items with count-only unitOfSize (N≥2 each/pk) must be identified by productId."""
        count_only_ids = _identify_count_only_external_ids(iga_raw_items, "iga")
        assert "BV001" in count_only_ids  # 6pk beer
        assert "FV001" in count_only_ids  # 3 each bananas
        assert "FV002" in count_only_ids  # 5 each apples

    @pytest.mark.unit
    def test_single_unit_excluded_from_count_only(self, iga_raw_items) -> None:
        """unitOfSize size=1 must not appear in count_only_external_ids."""
        count_only_ids = _identify_count_only_external_ids(iga_raw_items, "iga")
        assert "FV003" not in count_only_ids  # 1 each lemon

    @pytest.mark.unit
    def test_plain_size_items_not_count_only(self, iga_raw_items) -> None:
        """Items with weight/volume types (gram/litre/millilitre/kilogram) must not be count-only."""
        count_only_ids = _identify_count_only_external_ids(iga_raw_items, "iga")
        assert "BV002" not in count_only_ids  # 375 millilitre
        assert "DA001" not in count_only_ids  # 250 gram
        assert "DA002" not in count_only_ids  # 2 litre
        assert "BK001" not in count_only_ids  # 1 kilogram

    @pytest.mark.unit
    def test_null_unit_of_size_not_count_only(self, iga_raw_items) -> None:
        count_only_ids = _identify_count_only_external_ids(iga_raw_items, "iga")
        assert "HM001" not in count_only_ids  # null unitOfSize

    @pytest.mark.unit
    def test_is_iga_count_only_rejects_non_dict_uos(self) -> None:
        """A string unitOfSize (should never happen in IGA) must not be treated as count-only."""
        item = {"productId": "X1", "unitOfSize": "6 each"}
        assert not _is_iga_count_only(item)

    @pytest.mark.unit
    def test_iga_chain_does_not_match_woolworths_fields(self) -> None:
        """Items using Woolworths field names must yield no results under iga chain."""
        ww_items = [{"Stockcode": "W1", "PackageSize": "6PK"}]
        ids = _identify_count_only_external_ids(ww_items, "iga")
        assert ids == set(), "IGA extractor must not read Woolworths 'Stockcode'/'PackageSize'"


# ── IGA scraper parsing regression ───────────────────────────────────────────


class TestIGAScraperParsingRegression:
    """Regression: _parse_item must correctly handle all IGA unitOfSize variants.

    IGA unitOfSize formats encountered in the wild:
      {"size": N, "type": "each"|"ea"|"pk"|"pack"} → count-only (N>1) or None (N=1)
      {"size": N, "type": "millilitre"}             → "N mL" (with space)
      {"size": N, "type": "gram"}                   → "N g"
      {"size": N, "type": "litre"}                  → "N L"
      {"size": N, "type": "kilogram"}               → "N kg"
      null                                          → package_size=None
    """

    @pytest.mark.unit
    def test_count_with_volume_in_name_enriched(self, iga_raw_items) -> None:
        """'6 pk' + '375mL' in name → enriched to '375mL 6 Pack'; norm='6x375mL'."""
        item = _find(iga_raw_items, "count_with_volume_in_name")
        cp = _parse_item(item, "Beer, Wine & Spirits", _TODAY)
        assert cp is not None
        assert cp.package_size == "375mL 6 Pack"
        assert normalize_package_size(cp.package_size) == "6x375mL"

    @pytest.mark.unit
    def test_count_without_volume_falls_back_to_n_pack(self, iga_raw_items) -> None:
        item = _find(iga_raw_items, "count_without_volume_produce")
        cp = _parse_item(item, "Fruit and Vegetable", _TODAY)
        assert cp is not None
        assert cp.package_size == "3 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_count_without_volume_5pack(self, iga_raw_items) -> None:
        item = _find(iga_raw_items, "count_without_volume_5pack")
        cp = _parse_item(item, "Fruit and Vegetable", _TODAY)
        assert cp is not None
        assert cp.package_size == "5 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_single_unit_suppressed(self, iga_raw_items) -> None:
        """size=1 each must produce package_size=None — same as Woolworths '1EA'."""
        item = _find(iga_raw_items, "single_unit_suppressed")
        cp = _parse_item(item, "Fruit and Vegetable", _TODAY)
        assert cp is not None
        assert cp.package_size is None

    @pytest.mark.unit
    def test_millilitre_size_preserved(self, iga_raw_items) -> None:
        """type='millilitre' → '375 mL'; norm='375mL'."""
        item = _find(iga_raw_items, "millilitre_size")
        cp = _parse_item(item, "Drinks", _TODAY)
        assert cp is not None
        assert cp.package_size == "375 mL"
        assert normalize_package_size(cp.package_size) == "375mL"

    @pytest.mark.unit
    def test_gram_size_preserved(self, iga_raw_items) -> None:
        """type='gram' → '250 g'; norm='250g'."""
        item = _find(iga_raw_items, "gram_size")
        cp = _parse_item(item, "Dairy, Eggs & Fridge", _TODAY)
        assert cp is not None
        assert cp.package_size == "250 g"
        assert normalize_package_size(cp.package_size) == "250g"

    @pytest.mark.unit
    def test_litre_size_preserved(self, iga_raw_items) -> None:
        """type='litre' → '2 L'; norm='2L'."""
        item = _find(iga_raw_items, "litre_size")
        cp = _parse_item(item, "Dairy, Eggs & Fridge", _TODAY)
        assert cp is not None
        assert cp.package_size == "2 L"
        assert normalize_package_size(cp.package_size) == "2L"

    @pytest.mark.unit
    def test_kilogram_size_preserved(self, iga_raw_items) -> None:
        """type='kilogram' → '1 kg'; norm='1kg'."""
        item = _find(iga_raw_items, "kilogram_size")
        cp = _parse_item(item, "Pantry", _TODAY)
        assert cp is not None
        assert cp.package_size == "1 kg"
        assert normalize_package_size(cp.package_size) == "1kg"

    @pytest.mark.unit
    def test_null_unit_of_size_produces_null_package_size(self, iga_raw_items) -> None:
        item = _find(iga_raw_items, "null_unit_of_size")
        cp = _parse_item(item, "Deli & Meals", _TODAY)
        assert cp is not None
        assert cp.package_size is None

    @pytest.mark.unit
    def test_no_product_id_item_skipped(self, iga_raw_items) -> None:
        item = _find(iga_raw_items, "no_product_id_skipped")
        cp = _parse_item(item, "Test", _TODAY)
        assert cp is None, "_parse_item must return None when productId and sku are both absent"

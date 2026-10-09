"""
Unit tests for category_normaliser.normalise_category.

Verifies:
- Known exact mappings
- Root-only matching (strip " > SUFFIX")
- Unknown categories → None (never returned verbatim)
- Promotional / layout labels → None
- Empty / null input → None
- Closed-set invariant: every non-None result is in CANONICAL_CATEGORIES
- All new CATEGORY_MAP additions introduced in Story #85
"""

import pytest

from app.services.category_normaliser import CANONICAL_CATEGORIES, normalise_category


class TestKnownExactMappings:
    @pytest.mark.unit
    def test_bare_canonical_passes_through(self):
        assert normalise_category("Baby") == "Baby"
        assert normalise_category("Pantry") == "Pantry"
        assert normalise_category("Frozen") == "Frozen"

    @pytest.mark.unit
    def test_full_path_maps_to_canonical(self):
        assert normalise_category("Baby > GROCERIES") == "Baby"
        assert normalise_category("Pantry > FRESH CONVENIENCE") == "Pantry"
        assert normalise_category("Beer, Wine & Spirits > LIQUOR") == "Beer, Wine & Spirits"

    @pytest.mark.unit
    def test_retailer_variant_maps_correctly(self):
        assert normalise_category("Dairy, Eggs and Fridge") == "Dairy, Eggs & Fridge"
        assert normalise_category("Fruit & Veg") == "Fruit & Vegetables"
        assert normalise_category("Poultry, Meat & Seafood") == "Meat & Seafood"
        assert normalise_category("Freezer") == "Frozen"

    @pytest.mark.unit
    def test_ready_meals_self_maps(self):
        # "Ready Meals" must be in CATEGORY_MAP as a key so it does not fall
        # through to the (now removed) verbatim fallback.
        assert normalise_category("Ready Meals") == "Ready Meals"

    @pytest.mark.unit
    def test_dinner_maps_to_ready_meals(self):
        assert normalise_category("Dinner") == "Ready Meals"
        assert normalise_category("Dinner > FRESH CONVENIENCE") == "Ready Meals"


class TestRootStripFallback:
    @pytest.mark.unit
    def test_known_root_with_unknown_suffix_maps(self):
        # Root is in map, unknown suffix is ignored
        assert normalise_category("Pantry > SOME_NEW_DEPT") == "Pantry"
        assert normalise_category("Frozen > UNKNOWN_SECTION") == "Frozen"
        assert normalise_category("Pet > NEW_SUFFIX") == "Pet"

    @pytest.mark.unit
    def test_root_mapping_for_health_dietary(self):
        # IGA category — new mapping in Story #85
        assert normalise_category("Health & Dietary") == "Health & Beauty"


class TestUnknownCategoriesReturnNone:
    @pytest.mark.unit
    def test_completely_unknown_raw_returns_none(self):
        assert normalise_category("SomeRandomRetailerBucket") is None
        assert normalise_category("New Arrivals") is None
        assert normalise_category("Staff Picks") is None

    @pytest.mark.unit
    def test_unknown_root_with_suffix_returns_none(self):
        # The old code returned the root verbatim; new code returns None.
        assert normalise_category("Higher Protein Food and Drink") is None
        assert normalise_category("Higher Protein Food and Drink > SECTION") is None

    @pytest.mark.unit
    def test_malformed_leading_arrow_returns_none(self):
        # Malformed categories seen in dev DB: no root before " > "
        assert normalise_category("> GROCERIES") is None
        assert normalise_category("> LIQUOR") is None
        assert normalise_category("> GENERAL MERCHANDISE") is None
        assert normalise_category("> PROPRIETARY BAKERY") is None
        assert normalise_category("> FRESH CONVENIENCE") is None


class TestPromoLayoutLabels:
    @pytest.mark.unit
    def test_explicit_none_entries_return_none(self):
        assert normalise_category("Specials") is None
        assert normalise_category("Other") is None
        assert normalise_category("Front of Store") is None
        assert normalise_category("Container Bonus Credits") is None
        assert normalise_category("Christmas") is None
        assert normalise_category("General Merchandise") is None

    @pytest.mark.unit
    def test_new_promo_entries_return_none(self):
        # Story #85 additions
        assert normalise_category("Bonus Credit Products") is None
        assert normalise_category("Halloween") is None
        assert normalise_category("Back to School") is None
        assert normalise_category("Higher Protein Food and Drink") is None


class TestEmptyAndNull:
    @pytest.mark.unit
    def test_none_input_returns_none(self):
        assert normalise_category(None) is None

    @pytest.mark.unit
    def test_empty_string_returns_none(self):
        assert normalise_category("") is None

    @pytest.mark.unit
    def test_whitespace_only_returns_none(self):
        assert normalise_category("   ") is None


class TestClosedSetInvariant:
    @pytest.mark.unit
    def test_all_map_values_produce_valid_result(self):
        """Every CATEGORY_MAP key must yield a result in CANONICAL_CATEGORIES or None."""
        from app.services.category_normaliser import CATEGORY_MAP

        for raw in CATEGORY_MAP:
            result = normalise_category(raw)
            assert result is None or result in CANONICAL_CATEGORIES, (
                f"normalise_category({raw!r}) returned {result!r} which is "
                f"not in CANONICAL_CATEGORIES"
            )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "raw",
        [
            "Baby",
            "Bakery",
            "Beer, Wine & Spirits",
            "Cleaning & Laundry",
            "Dairy, Eggs & Fridge",
            "Deli & Fresh",
            "Drinks",
            "Frozen",
            "Fruit & Vegetables",
            "Health & Beauty",
            "Health & Dietary",
            "Home & Garden",
            "Meat & Seafood",
            "Pantry",
            "Pet",
            "Ready Meals",
            "Vitamins & Supplements",
            "Fruit & Veg > FRUIT AND VEG",
            "Poultry, Meat & Seafood > DELI SERVICE",
            "Cleaning & Maintenance > GROCERIES",
        ],
    )
    def test_result_is_in_canonical_or_none(self, raw: str):
        result = normalise_category(raw)
        assert result is None or result in CANONICAL_CATEGORIES

    @pytest.mark.unit
    def test_canonical_categories_is_correct_size(self):
        # 16 canonical categories as of Story #85
        assert len(CANONICAL_CATEGORIES) == 16

    @pytest.mark.unit
    def test_canonical_categories_contents(self):
        expected = {
            "Baby",
            "Bakery",
            "Beer, Wine & Spirits",
            "Cleaning & Laundry",
            "Dairy, Eggs & Fridge",
            "Deli & Fresh",
            "Drinks",
            "Frozen",
            "Fruit & Vegetables",
            "Health & Beauty",
            "Home & Garden",
            "Meat & Seafood",
            "Pantry",
            "Pet",
            "Ready Meals",
            "Vitamins & Supplements",
        }
        assert CANONICAL_CATEGORIES == expected

"""
Unit tests for normalize_package_size() — Story #50.

Covers all acceptance criteria from the issue spec plus edge cases.
"""

import pytest

from app.services.package_size import normalize_package_size


class TestVolumeMillilitres:
    """Volume < 1000 mL → 'NmL'."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("375mL", "375mL"),
            ("375 mL", "375mL"),
            ("375ml", "375mL"),
            ("375 ml", "375mL"),
            ("375ML", "375mL"),
            ("250mL", "250mL"),
            ("200mL", "200mL"),
            ("750mL", "750mL"),
            ("330ml", "330mL"),
            ("473mL", "473mL"),
        ],
    )
    def test_millilitres(self, raw, expected):
        assert normalize_package_size(raw) == expected


class TestVolumeLitres:
    """Volume ≥ 1000 mL → 'N.NL'."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("1L", "1L"),
            ("1 L", "1L"),
            ("1l", "1L"),
            ("2L", "2L"),
            ("1.5L", "1.5L"),
            ("1.5 L", "1.5L"),
            ("1.5 LITRE", "1.5L"),
            ("1.5 LITRES", "1.5L"),
            ("1.5 litre", "1.5L"),
            ("1.5 liter", "1.5L"),
            ("1.5 liters", "1.5L"),
            ("1000mL", "1L"),
            ("1000 ml", "1L"),
            ("1500mL", "1.5L"),
            ("1500 ml", "1.5L"),
            ("2000mL", "2L"),
            ("2000 mL", "2L"),
            ("3L", "3L"),
        ],
    )
    def test_litres(self, raw, expected):
        assert normalize_package_size(raw) == expected


class TestWeightGrams:
    """Weight < 1000 g → 'Ng'."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("500g", "500g"),
            ("500 g", "500g"),
            ("500G", "500g"),
            ("500 G", "500g"),
            ("200g", "200g"),
            ("100g", "100g"),
            ("85g", "85g"),
            ("130 g", "130g"),
            ("250g", "250g"),
            ("500gm", "500g"),
            ("500 gram", "500g"),
            ("500 grams", "500g"),
        ],
    )
    def test_grams(self, raw, expected):
        assert normalize_package_size(raw) == expected


class TestWeightKilograms:
    """Weight ≥ 1000 g → 'N.Nkg'."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("2.5kg", "2.5kg"),
            ("2.5 kg", "2.5kg"),
            ("2.5 KG", "2.5kg"),
            ("2.5 kgs", "2.5kg"),
            ("2.5 kilogram", "2.5kg"),
            ("2.5 kilograms", "2.5kg"),
            ("2500g", "2.5kg"),
            ("2500 g", "2.5kg"),
            ("1kg", "1kg"),
            ("1 kg", "1kg"),
            ("1000g", "1kg"),
            ("1000 g", "1kg"),
            ("2kg", "2kg"),
        ],
    )
    def test_kilograms(self, raw, expected):
        assert normalize_package_size(raw) == expected


class TestMultipack:
    """Multipack formats → 'NxNunit'."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("24 x 375mL", "24x375mL"),
            ("24x375mL", "24x375mL"),
            ("6 x 375mL", "6x375mL"),
            ("6x375mL", "6x375mL"),
            ("6 x 355mL", "6x355mL"),
            ("375mL 24 Pack", "24x375mL"),
            ("375mL 6 pack", "6x375mL"),
            ("375mL 6pk", "6x375mL"),
            ("12 x 30g", "12x30g"),
            ("4 x 250g", "4x250g"),
            ("6 x 1.25L", "6x1.25L"),
        ],
    )
    def test_multipack(self, raw, expected):
        assert normalize_package_size(raw) == expected


class TestUnparseable:
    """Strings with no recognisable size → None."""

    @pytest.mark.parametrize(
        "raw",
        [
            "assorted",
            "ea",
            "each",
            "variety",
            "random",
            "",
        ],
    )
    def test_unparseable_returns_none(self, raw):
        assert normalize_package_size(raw) is None

    def test_none_input_returns_none(self):
        assert normalize_package_size(None) is None


class TestEdgeCases:
    """Formatting edge cases: no trailing zeros, integers stay integers."""

    def test_1000ml_formats_as_1L_not_1_point_0L(self):
        assert normalize_package_size("1000mL") == "1L"

    def test_2000ml_formats_as_2L(self):
        assert normalize_package_size("2000mL") == "2L"

    def test_1500ml_formats_as_1_point_5L(self):
        assert normalize_package_size("1500mL") == "1.5L"

    def test_1000g_formats_as_1kg(self):
        assert normalize_package_size("1000g") == "1kg"

    def test_range_takes_first_value(self):
        # "260g - 280g" — the regex finds 260g first
        result = normalize_package_size("260g - 280g")
        assert result == "260g"


class TestImperialRejection:
    """oz / fl oz are not supported → None."""

    @pytest.mark.parametrize(
        "raw",
        [
            "12 oz",
            "12oz",
            "12 OZ",
            "1.5 oz",
            "16 oz",
            "6 ounces",
            "6 ounce",
        ],
    )
    def test_oz_returns_none(self, raw):
        assert normalize_package_size(raw) is None


class TestNumericValidation:
    """Zero values and fractional pack counts → None."""

    def test_zero_volume_returns_none(self):
        assert normalize_package_size("0mL") is None

    def test_zero_weight_returns_none(self):
        assert normalize_package_size("0g") is None

    def test_zero_litre_returns_none(self):
        assert normalize_package_size("0L") is None

    def test_zero_pack_count_returns_none(self):
        assert normalize_package_size("0 x 375mL") is None

    def test_fractional_pack_count_returns_none(self):
        # "1.5 x 375mL" — fractional count is nonsensical
        assert normalize_package_size("1.5 x 375mL") is None

    def test_zero_pack_size_in_multipack_returns_none(self):
        assert normalize_package_size("6 x 0mL") is None

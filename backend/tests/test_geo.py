"""
Unit tests for the shared great-circle distance helper.

These directly cover the bug class that shipped: the original inline Haversine
in the stores router used a wrong formula that produced ``complex`` numbers for
some inputs, crashing the nearby-stores endpoint with a 500. Re-introducing that
bug must make these tests fail.
"""

import math

import pytest

from app.services.geo import EARTH_RADIUS_KM, haversine_km


class TestHaversineKm:
    @pytest.mark.unit
    def test_same_point_is_zero(self):
        assert haversine_km(-33.87, 151.21, -33.87, 151.21) == 0.0

    @pytest.mark.unit
    def test_sydney_to_melbourne(self):
        # Sydney (-33.8688, 151.2093) -> Melbourne (-37.8136, 144.9631)
        d = haversine_km(-33.8688, 151.2093, -37.8136, 144.9631)
        assert d == pytest.approx(713, abs=10)

    @pytest.mark.unit
    def test_sydney_to_perth(self):
        # Sydney -> Perth (-31.9523, 115.8613) ~= 3290 km
        d = haversine_km(-33.8688, 151.2093, -31.9523, 115.8613)
        assert d == pytest.approx(3290, abs=30)

    @pytest.mark.unit
    def test_symmetric(self):
        a = haversine_km(-33.87, 151.21, -37.81, 144.96)
        b = haversine_km(-37.81, 144.96, -33.87, 151.21)
        assert a == pytest.approx(b)

    @pytest.mark.unit
    def test_result_is_real_finite_nonnegative(self):
        # Sweep a range of point pairs; every result must be a plain float,
        # finite, and >= 0 (never complex, nan, or negative).
        pairs = [
            (0, 0, 0, 0),
            (-33.87, 151.21, -33.8700001, 151.2100001),  # near-identical
            (0, 0, 0, 180),      # antipodal-ish across dateline
            (90, 0, -90, 0),     # pole to pole
            (-33.87, 179.99, -33.87, -179.99),  # dateline crossing
            (10, 20, -10, -20),  # equator crossing
        ]
        for lat1, lon1, lat2, lon2 in pairs:
            d = haversine_km(lat1, lon1, lat2, lon2)
            assert isinstance(d, float)
            assert not isinstance(d, complex)
            assert math.isfinite(d)
            assert d >= 0.0

    @pytest.mark.unit
    def test_near_identical_points_no_complex(self):
        # The exact regression: tiny separations previously drove the term
        # negative -> sqrt -> complex. Must be ~0 and a real float.
        d = haversine_km(-33.87, 151.21, -33.87 + 1e-9, 151.21 + 1e-9)
        assert isinstance(d, float)
        assert d >= 0.0
        assert d < 0.001  # under a metre

    @pytest.mark.unit
    def test_max_distance_bounded_by_half_circumference(self):
        # No pair can be farther than half the Earth's circumference.
        half_circumference = math.pi * EARTH_RADIUS_KM
        d = haversine_km(0, 0, 0, 180)
        assert d == pytest.approx(half_circumference, abs=1)
        assert d <= half_circumference + 1

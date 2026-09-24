"""
Geographic distance helpers.

Single source of truth for great-circle distance so the stores and
price-reports routers don't each carry their own (previously buggy) copy.
"""

from __future__ import annotations

import math

# Mean Earth radius in kilometres (WGS-84 spherical approximation).
EARTH_RADIUS_KM: float = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance between two lat/lon points, in kilometres.

    Uses the Haversine formula. The intermediate term ``a`` is clamped to
    [0, 1] before ``sqrt`` so floating-point error near-identical points can
    never produce a negative value (which previously yielded a ``complex``
    result and crashed callers).

    Args:
        lat1: Latitude of point 1 in decimal degrees.
        lon1: Longitude of point 1 in decimal degrees.
        lat2: Latitude of point 2 in decimal degrees.
        lon2: Longitude of point 2 in decimal degrees.

    Returns:
        Non-negative distance in kilometres (a real, finite float).
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    # Guard against tiny negative/over-one values from float error.
    a = min(1.0, max(0.0, a))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_KM * c

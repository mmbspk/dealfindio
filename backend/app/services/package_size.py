"""
Canonical package-size normalizer.

Converts raw retailer size strings (e.g. "375 mL", "1.5 LITRE", "24 x 375mL")
to a consistent format used in Product.package_size_norm:

  Volume < 1000 mL  → "NmL"      e.g. "375mL", "250mL"
  Volume ≥ 1000 mL  → "N.NL"     e.g. "1L", "1.5L", "2L"
  Weight < 1000 g   → "Ng"       e.g. "500g", "200g"
  Weight ≥ 1000 g   → "N.Nkg"    e.g. "1kg", "2.5kg"
  Multipack         → "NxNmL"    e.g. "6x375mL", "24x375mL"
  Unparseable       → None

Imperial units (oz, fl oz) are not supported and return None.
"""

from __future__ import annotations

import re

# ── unit aliases ──────────────────────────────────────────────────────────────

_ML = re.compile(r"millilitres?|milliliters?|mls?", re.I)
_L = re.compile(r"litres?|liters?|(?<!\w)l(?!\w)", re.I)
_G = re.compile(r"grams?|(?<!\w)gm(?!\w)|(?<!\w)g(?!\w)", re.I)
_KG = re.compile(r"kilograms?|kgs?", re.I)

# ── capture patterns ─────────────────────────────────────────────────────────

_NUM = r"\d+(?:\.\d+)?"
_UNIT = (
    r"(?:millilitres?|milliliters?|mls?"
    r"|litres?|liters?"
    r"|kilograms?|kgs?"
    r"|grams?|gm"
    r"|[lL](?!\w)"
    r"|[gG](?!\w))"
)

# Count group uses _NUM (allows float) so fractional counts like "1.5x375mL" are
# detected and explicitly rejected rather than falling through to single-size matching.
# Negative lookbehind (?<![\d.]) ensures the count is not part of a longer decimal.
_RE_MULTI_EXPLICIT = re.compile(
    rf"(?<![\d.])({_NUM})\s*[xX×]\s*({_NUM})\s*({_UNIT})",
    re.I,
)

# "375mL 24 Pack" — size first, count after
_RE_SIZE_THEN_COUNT = re.compile(
    rf"({_NUM})\s*({_UNIT})\s+(\d+)\s*(?:pack|pk|ct|count|pcs?|pieces?)\b",
    re.I,
)

# "12 Pack 375mL" — bare count word before size (adjacent)
# Handles "12 Pack 375mL", "12pk 1.25L", "4 Pack 100g"
_RE_COUNT_THEN_SIZE = re.compile(
    rf"(?<![\d.])(\d+)\s*(?:pack|pk|ct|count|pcs?|pieces?)\b\s*({_NUM})\s*({_UNIT})",
    re.I,
)

# "375mL × 12" or "375mL x 12" — unit then explicit × count
# The negative lookahead prevents matching the size half of "24 × 375mL × 6" (handled by
# _RE_MULTI_EXPLICIT first); also prevents false matches like "375mL x 1.5 kg" where the
# token after × is itself followed by a unit.
_RE_SIZE_THEN_EXPLICIT_COUNT = re.compile(
    rf"({_NUM})\s*({_UNIT})\s*[xX×]\s*(\d+)\b(?!\s*\.?\d*\s*(?:{_UNIT}))",
    re.I,
)

# Simple "375mL" or "1.5 L"
_RE_SINGLE = re.compile(rf"({_NUM})\s*({_UNIT})", re.I)


# ── helpers ───────────────────────────────────────────────────────────────────


def _normalise_unit(raw_unit: str) -> str:
    """Return canonical unit key: 'ml', 'l', 'g', 'kg'."""
    u = raw_unit.strip()
    if _ML.fullmatch(u):
        return "ml"
    if _L.fullmatch(u):
        return "l"
    if _G.fullmatch(u):
        return "g"
    if _KG.fullmatch(u):
        return "kg"
    return ""


def _fmt(val: float) -> str:
    """Format a number: no trailing zeros, no trailing decimal point."""
    if val == int(val):
        return str(int(val))
    # Up to 3 significant decimals, strip trailing zeros
    return f"{val:.3f}".rstrip("0").rstrip(".")


def _format_volume_ml(ml: float) -> str:
    if ml < 1000:
        return f"{_fmt(ml)}mL"
    return f"{_fmt(ml / 1000)}L"


def _format_weight_g(g: float) -> str:
    if g < 1000:
        return f"{_fmt(g)}g"
    return f"{_fmt(g / 1000)}kg"


def _to_ml(value: float, unit: str) -> float | None:
    if unit == "ml":
        return value
    if unit == "l":
        return value * 1000
    return None


def _to_g(value: float, unit: str) -> float | None:
    if unit == "g":
        return value
    if unit == "kg":
        return value * 1000
    return None


def _format_single(value: float, unit: str) -> str | None:
    ml = _to_ml(value, unit)
    if ml is not None:
        return _format_volume_ml(ml)
    g = _to_g(value, unit)
    if g is not None:
        return _format_weight_g(g)
    return None


# ── public API ────────────────────────────────────────────────────────────────

# Volume-only extraction pattern for name-based pack augmentation.
# Intentionally excludes weight units (g, kg) — a weight in a product name
# may describe the total package, not the per-unit mass (e.g. "8 Pack 560g").
_RE_NAME_VOLUME = re.compile(
    r"(\d+(?:\.\d+)?)\s*(millilitr(?:e|er)s?|mls?|litr(?:e|er)s?|l(?!\w))",
    re.I,
)


def combine_count_with_volume(name: str, count: int) -> str | None:
    """Return a raw size string combining *count* with the first volume unit found
    in *name*, or None if no unambiguous volume unit is present.

    Example: name="Corona Beer Bottle 6 Pack 355mL", count=6 → "355mL 6 Pack"
    The caller should pass the result to normalize_package_size, which turns it
    into the canonical form "6x355mL".

    Only volume units (mL, L) are matched.  Weight units (g, kg) are never
    combined because a name like "8 Pack Sausages 560g" may describe the total
    package weight rather than a per-unit mass.
    """
    if not name or count < 2:
        return None
    m = _RE_NAME_VOLUME.search(name)
    if not m:
        return None
    return f"{m.group(1)}{m.group(2)} {count} Pack"


def normalize_package_size(raw: str | None) -> str | None:
    """
    Return a canonical package-size string for *raw*, or None if unparseable.

    Examples:
      "375 mL"       → "375mL"
      "1.5 LITRE"    → "1.5L"
      "1500mL"       → "1.5L"
      "500 g"        → "500g"
      "2500g"        → "2.5kg"
      "24 x 375mL"   → "24x375mL"
      "375mL 24 Pack"→ "24x375mL"
      "12 Pack 375mL"→ "12x375mL"
      "375mL × 12"   → "12x375mL"
      "12 oz"        → None (imperial not supported)
      "assorted"     → None
    """
    if not raw:
        return None
    s = raw.strip()

    # 1. Explicit multipack: "24 x 375mL", "24 × 375mL"
    m = _RE_MULTI_EXPLICIT.search(s)
    if m:
        count_f = float(m.group(1))
        size_val = float(m.group(2))
        unit = _normalise_unit(m.group(3))
        # Reject fractional pack count (e.g. "1.5 x 375mL") without falling through.
        if count_f != int(count_f):
            return None
        count = int(count_f)
        if not unit or count == 0 or size_val == 0:
            return None
        inner = _format_single(size_val, unit)
        if inner is None:
            return None
        return f"{count}x{inner}"

    # 2. Size-then-count: "375mL 24 Pack"
    m = _RE_SIZE_THEN_COUNT.search(s)
    if m:
        size_val = float(m.group(1))
        unit = _normalise_unit(m.group(2))
        count = int(m.group(3))
        if not unit or count == 0 or size_val == 0:
            return None
        inner = _format_single(size_val, unit)
        if inner is None:
            return None
        return f"{count}x{inner}"

    # 3. Count-before-unit (adjacent): "12 Pack 375mL", "4 Pack 100g"
    m = _RE_COUNT_THEN_SIZE.search(s)
    if m:
        count = int(m.group(1))
        size_val = float(m.group(2))
        unit = _normalise_unit(m.group(3))
        if not unit or count == 0 or size_val == 0:
            return None
        inner = _format_single(size_val, unit)
        if inner is None:
            return None
        return f"{count}x{inner}"

    # 4. Unit-then-count via × / x: "375mL × 12", "1.25L x 4"
    m = _RE_SIZE_THEN_EXPLICIT_COUNT.search(s)
    if m:
        size_val = float(m.group(1))
        unit = _normalise_unit(m.group(2))
        count_f = float(m.group(3))
        if count_f != int(count_f):
            return None
        count = int(count_f)
        if not unit or count == 0 or size_val == 0:
            return None
        inner = _format_single(size_val, unit)
        if inner is None:
            return None
        return f"{count}x{inner}"

    # 5. Single size: "375mL", "1.5 L", "500g"
    m = _RE_SINGLE.search(s)
    if m:
        size_val = float(m.group(1))
        unit = _normalise_unit(m.group(2))
        if not unit or size_val == 0:
            return None
        return _format_single(size_val, unit)

    return None

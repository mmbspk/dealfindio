"""
Unit tests for the typed config store helpers (config_store.py):
``get_runtime_typed`` coercion + fallback and ``validate_value`` validation.

No DB required — a stub session backs ``get_config``. The typed keys under test
(AI_DEDUP_*, OFF_GTIN_BACKFILL_*) are registered in KNOWN_KEYS with type metadata.
"""
from __future__ import annotations

import pytest

from app.services import config_store


class _StubSession:
    """Minimal stand-in for a SQLAlchemy Session backing get_config via .get()."""

    def __init__(self, stored: dict[str, str] | None = None):
        self._stored = stored or {}

    def get(self, model, key):
        val = self._stored.get(key)
        if val is None:
            return None
        # config_store.get_config reads .value_enc / .is_secret off the row.
        from types import SimpleNamespace

        return SimpleNamespace(value_enc=val, is_secret=False)


# ── get_runtime_typed: coercion ───────────────────────────────────────────────


class TestGetRuntimeTyped:
    def test_bool_true_variants(self):
        for raw in ("true", "1", "yes", "on", "TRUE", "  True "):
            s = _StubSession({"AI_DEDUP_ENABLED": raw})
            assert config_store.get_runtime_typed("AI_DEDUP_ENABLED", s) is True, raw

    def test_bool_false_variants(self):
        for raw in ("false", "0", "no", "off", "nonsense"):
            s = _StubSession({"AI_DEDUP_ENABLED": raw})
            assert config_store.get_runtime_typed("AI_DEDUP_ENABLED", s) is False, raw

    def test_float_coercion(self):
        s = _StubSession({"AI_DEDUP_CONFIDENCE_MIN": "0.9"})
        assert config_store.get_runtime_typed("AI_DEDUP_CONFIDENCE_MIN", s) == pytest.approx(0.9)

    def test_int_coercion(self):
        s = _StubSession({"AI_DEDUP_MAX_TOKENS": "256"})
        assert config_store.get_runtime_typed("AI_DEDUP_MAX_TOKENS", s) == 256

    def test_enum_passthrough(self):
        s = _StubSession({"AI_DEDUP_MODEL": "claude-haiku-4-5-20251001"})
        assert config_store.get_runtime_typed("AI_DEDUP_MODEL", s) == "claude-haiku-4-5-20251001"

    def test_unset_falls_back_to_settings_default(self):
        # Nothing stored → the typed Settings attribute (its default) is returned.
        s = _StubSession({})
        assert config_store.get_runtime_typed("AI_DEDUP_ENABLED", s) is False  # Settings default
        assert config_store.get_runtime_typed("AI_DEDUP_MAX_TOKENS", s) == 512
        assert config_store.get_runtime_typed("AI_DEDUP_MODEL", s) == "claude-sonnet-4-6"

    def test_bad_stored_number_falls_back_to_default(self):
        # A malformed stored int must not crash a run — fall back to the setting.
        s = _StubSession({"AI_DEDUP_MAX_TOKENS": "not-a-number"})
        assert config_store.get_runtime_typed("AI_DEDUP_MAX_TOKENS", s) == 512


# ── validate_value ────────────────────────────────────────────────────────────


class TestValidateValue:
    def test_bool_normalizes(self):
        assert config_store.validate_value("AI_DEDUP_ENABLED", "yes") == "true"
        assert config_store.validate_value("AI_DEDUP_ENABLED", "0") == "false"

    def test_bool_rejects_garbage(self):
        with pytest.raises(ValueError):
            config_store.validate_value("AI_DEDUP_ENABLED", "maybe")

    def test_enum_accepts_option(self):
        assert config_store.validate_value("AI_DEDUP_MODEL", "claude-opus-5") == "claude-opus-5"

    def test_enum_rejects_non_option(self):
        with pytest.raises(ValueError):
            config_store.validate_value("AI_DEDUP_MODEL", "gpt-4")

    def test_float_within_range(self):
        assert config_store.validate_value("AI_DEDUP_CONFIDENCE_MIN", "0.75") == "0.75"

    def test_float_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            config_store.validate_value("AI_DEDUP_CONFIDENCE_MIN", "2.0")
        with pytest.raises(ValueError):
            config_store.validate_value("AI_DEDUP_CONFIDENCE_MIN", "-0.1")

    def test_int_non_numeric_rejected(self):
        with pytest.raises(ValueError):
            config_store.validate_value("AI_DEDUP_MAX_TOKENS", "lots")

    def test_int_below_min_rejected(self):
        with pytest.raises(ValueError):
            config_store.validate_value("OFF_GTIN_BACKFILL_LIMIT_PER_RUN", "0")

    def test_plain_string_passthrough(self):
        # Unknown / plain-string keys are returned unchanged.
        assert (
            config_store.validate_value("ANTHROPIC_BASE_URL", "https://proxy.example.com")
            == "https://proxy.example.com"
        )
        assert config_store.validate_value("EMAIL_FROM_NAME", "DealFindio") == "DealFindio"

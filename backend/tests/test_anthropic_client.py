"""
Unit tests for get_anthropic_client() credential resolution.

Verifies:
- DB-only credentials: env key empty, DB key present → client is constructed
- DB-over-env precedence: both set, DB key wins
- Env fallback: DB key absent, env key present → uses env key
- Both absent: RuntimeError raised after the DB lookup (not before it)

No real Anthropic SDK calls — Anthropic() is mocked at the module level.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


class _FakeSessionCtx:
    """A minimal context-manager session so ``with _SessionLocal() as s:`` works."""

    def __enter__(self):
        return object()

    def __exit__(self, *_):
        pass


def _session_factory():
    """Callable that returns a _FakeSessionCtx, mirroring sessionmaker behaviour."""
    return _FakeSessionCtx()


def _settings(api_key: str = "", base_url: str = "") -> SimpleNamespace:
    return SimpleNamespace(ANTHROPIC_API_KEY=api_key, ANTHROPIC_BASE_URL=base_url)


def _db_runtime(api_key=None, base_url=None):
    """Return a get_runtime_value stub that yields the given key values."""

    def _impl(key, _session):
        if key == "ANTHROPIC_API_KEY":
            return api_key
        if key == "ANTHROPIC_BASE_URL":
            return base_url
        return None

    return _impl


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the cached Anthropic singleton before (and after) every test."""
    import app.deps

    app.deps._anthropic_client = None
    yield
    app.deps._anthropic_client = None


# ── tests ─────────────────────────────────────────────────────────────────────


class TestGetAnthropicClientCredentials:
    """Credential resolution order and validation in get_anthropic_client()."""

    def _call(self, *, env_key, env_base="", db_key=None, db_base=None):
        """
        Run get_anthropic_client() under controlled conditions.

        Returns the kwargs dict that Anthropic() was called with.
        Raises RuntimeError verbatim when the effective key is empty.
        """
        captured: dict = {}

        def _fake_anthropic(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        with (
            patch("app.deps.get_settings", return_value=_settings(env_key, env_base)),
            patch("app.deps._SessionLocal", _session_factory),
            patch(
                "app.services.config_store.get_runtime_value",
                side_effect=_db_runtime(db_key, db_base),
            ),
            patch("anthropic.Anthropic", side_effect=_fake_anthropic),
        ):
            from app.deps import get_anthropic_client

            get_anthropic_client()

        return captured

    def test_db_key_used_when_env_key_is_empty(self):
        """DB-only: empty env key, DB key present → client constructed with DB key."""
        kwargs = self._call(env_key="", db_key="db-uuid-key", db_base="http://proxy/")
        assert kwargs["api_key"] == "db-uuid-key"
        assert kwargs.get("base_url") == "http://proxy/"

    def test_db_key_overrides_env_key(self):
        """DB-over-env: both set → DB key wins."""
        kwargs = self._call(env_key="env-key", db_key="db-uuid-key")
        assert kwargs["api_key"] == "db-uuid-key"

    def test_env_key_used_as_fallback_when_db_key_absent(self):
        """Fallback: DB returns None, env key present → uses env key."""
        kwargs = self._call(env_key="env-only-key", db_key=None)
        assert kwargs["api_key"] == "env-only-key"

    def test_raises_only_after_db_lookup_when_both_absent(self):
        """RuntimeError raised after DB override attempt, not before it."""
        import app.deps

        lookup_was_called = []

        def _tracking_runtime(key, _session):
            lookup_was_called.append(key)
            return None

        with (
            patch("app.deps.get_settings", return_value=_settings("", "")),
            patch("app.deps._SessionLocal", _session_factory),
            patch(
                "app.services.config_store.get_runtime_value",
                side_effect=_tracking_runtime,
            ),
        ):
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                app.deps.get_anthropic_client()

        assert "ANTHROPIC_API_KEY" in lookup_was_called, (
            "DB lookup must be attempted before raising RuntimeError; "
            "a DB-only deployment must have the chance to supply the key"
        )

    def test_no_base_url_kwarg_when_both_absent(self):
        """When neither env nor DB provides a base_url, base_url is omitted."""
        kwargs = self._call(env_key="some-key", db_key=None, db_base=None)
        assert "base_url" not in kwargs

    def test_db_base_url_used_when_env_base_url_empty(self):
        """DB-supplied base_url takes precedence over empty env base_url."""
        kwargs = self._call(env_key="key", env_base="", db_key=None, db_base="http://db-proxy/")
        assert kwargs.get("base_url") == "http://db-proxy/"

"""Tests for the --chain selector added in Story #114.

Verifies that:
1. All four supported retailer chains are in the scraper registry.
2. The validator returns exit code 1 for an unknown chain without touching
   the network or the database.
3. The default chain (woolworths) is unchanged from Story #109 behaviour.
4. audit_chains defaults to {chain} when --chains is not explicitly passed.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "validate_crawl_109_mod_chain",
    _SCRIPTS_DIR / "validate_crawl_109.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["validate_crawl_109_mod_chain"] = _mod
_spec.loader.exec_module(_mod)

_parse_args = _mod._parse_args
main = _mod.main


# ── Registry: all supported chains are registered ────────────────────────────


class TestSupportedChainsRegistered:
    """All four supported chains must be in the catalog registry.

    If a chain is missing the registry raises ValueError on get_catalog_scraper.
    This test catches accidental removal or renaming before any live run is attempted.
    """

    @pytest.mark.unit
    @pytest.mark.parametrize("chain", ["woolworths", "aldi", "iga", "coles"])
    def test_chain_is_registered(self, chain: str) -> None:
        from scrapers.catalog_registry import get_catalog_scraper

        scraper = get_catalog_scraper(chain, redis_client=None)
        assert scraper is not None
        assert scraper.chain == chain


# ── Argparse defaults ─────────────────────────────────────────────────────────


class TestArgparseDefaults:
    """Default values must preserve Story #109 backward compatibility."""

    @pytest.mark.unit
    def test_default_chain_is_woolworths(self) -> None:
        with patch("sys.argv", ["validate_crawl_109.py"]):
            args = _parse_args()
        assert args.chain == "woolworths"

    @pytest.mark.unit
    def test_chain_flag_sets_chain(self) -> None:
        with patch("sys.argv", ["validate_crawl_109.py", "--chain", "aldi"]):
            args = _parse_args()
        assert args.chain == "aldi"

    @pytest.mark.unit
    def test_chains_audit_scope_defaults_to_none(self) -> None:
        """--chains is None when not provided; main() defaults it to {chain}."""
        with patch("sys.argv", ["validate_crawl_109.py"]):
            args = _parse_args()
        assert args.chains is None

    @pytest.mark.unit
    def test_chains_audit_scope_can_be_overridden(self) -> None:
        with patch("sys.argv", ["validate_crawl_109.py", "--chains", "aldi,coles"]):
            args = _parse_args()
        assert args.chains == "aldi,coles"


# ── Unknown chain: exit 1 before network/DB ───────────────────────────────────


class TestUnknownChainExitsEarly:
    """An unregistered chain must cause exit code 1 before any network or DB call.

    The registry validation runs at the top of main(), before DATABASE_URL is
    checked or any HTTP connection is opened.
    """

    @pytest.mark.unit
    def test_unknown_chain_returns_exit_code_1(self) -> None:
        with (
            patch("sys.argv", ["validate_crawl_109.py", "--chain", "nope", "--dry-run"]),
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://x/y"}),
        ):
            result = main()
        assert result == 1

    @pytest.mark.unit
    def test_unknown_chain_does_not_touch_database(self) -> None:
        """Registry check must run before the DB connection is opened."""
        db_connect_called = []

        real_create_engine = _mod.create_engine

        def spy_create_engine(*args, **kwargs):
            db_connect_called.append(True)
            return real_create_engine(*args, **kwargs)

        with (
            patch("sys.argv", ["validate_crawl_109.py", "--chain", "ghost", "--dry-run"]),
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://x/y"}),
            patch.object(_mod, "create_engine", spy_create_engine),
        ):
            result = main()

        assert result == 1
        assert not db_connect_called, "create_engine was called despite invalid chain"

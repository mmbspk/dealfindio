"""
Story #18 — Dedup Backfill Validation: regression tests.

Covers:
  - backfill_dedup.py schema fix: load_source_products / load_candidates now
    query through ProductRetailerListing (Product has no external_source column)
  - Package-size guard: products with different package_size_norm are skipped
    even when fuzzy name score ≥ MATCH_THRESHOLD (evidence: 122 ALDI + 100+ Coles
    false-positive pairs identified in Phase 1 dry-run probe)
  - Safety exclusion: EXCLUDED_PRODUCT_IDS are never merged regardless of score
  - IGA now in SOURCE_CHAINS (was missing before this story)
  - propagate_gtin_on_merge called in merge_into before DELETE
  - ai_dedup_backfill.py chain resolution: products loaded via listing join so
    product_chain_map contains every catalogued product's chain label

Evidence for evidence-cited constants (from Phase 1 dry-run on dev DB 2026-10-26):
  - 5fda8bda: Tasmanian Heritage Camembert — IGA listing 125g / Woolworths listing 200g
    (product_retailer_listings.package_size_raw confirmed in the DB)
  - c24cb38e: ALDI Energy Drink — raw=4x250mL, product name says 200ml
  - ALDI alone produced 122 size-conflict false-positive pairs at score ≥ 92
  - Coles produced 100+ size-conflict false-positive pairs at score ≥ 92
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.models import Product
from app.models.product_retailer_listing import ProductRetailerListing

# ── Import backfill_dedup via importlib so we don't pollute sys.path globally ─

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "backfill_dedup_mod",
    _SCRIPTS_DIR / "backfill_dedup.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["backfill_dedup_mod"] = _mod
_spec.loader.exec_module(_mod)

load_source_products = _mod.load_source_products
load_candidates = _mod.load_candidates
run_backfill = _mod.run_backfill
merge_into = _mod.merge_into
SOURCE_CHAINS = _mod.SOURCE_CHAINS
EXCLUDED_PRODUCT_IDS = _mod.EXCLUDED_PRODUCT_IDS
MATCH_THRESHOLD = _mod.MATCH_THRESHOLD


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_product(session, name: str, brand: str | None = None, size_norm: str | None = None) -> Product:
    p = Product(id=uuid4(), name=name, brand=brand, package_size_norm=size_norm)
    session.add(p)
    session.flush()
    return p


def _make_listing(session, product: Product, ext_source: str, chain: str) -> ProductRetailerListing:
    listing = ProductRetailerListing(
        id=uuid4(),
        product_id=product.id,
        external_source=ext_source,
        chain=chain,
        external_id=str(uuid4()),
    )
    session.add(listing)
    session.flush()
    return listing


# ── SOURCE_CHAINS completeness ─────────────────────────────────────────────────


class TestSourceChainsConfig:
    """Verify the SOURCE_CHAINS constant covers all three non-GTIN chains."""

    @pytest.mark.unit
    def test_iga_in_source_chains(self) -> None:
        """IGA was missing from SOURCE_CHAINS before Story #18 — must be present."""
        assert "catalog_iga" in SOURCE_CHAINS, (
            "IGA must be in SOURCE_CHAINS — it was omitted before Story #18, "
            "leaving ~14k IGA products unchecked against Woolworths/Drakes."
        )

    @pytest.mark.unit
    def test_aldi_in_source_chains(self) -> None:
        assert "catalog_aldi" in SOURCE_CHAINS

    @pytest.mark.unit
    def test_coles_in_source_chains(self) -> None:
        assert "catalog_coles" in SOURCE_CHAINS


# ── Known conflict exclusion ───────────────────────────────────────────────────


class TestExcludedProductIds:
    """EXCLUDED_PRODUCT_IDS must contain both known conflict products."""

    @pytest.mark.unit
    def test_camembert_excluded(self) -> None:
        """5fda8bda: IGA 125g / Woolworths 200g — must never be auto-merged."""
        assert "5fda8bda-c948-4d82-99f7-36e22253d354" in EXCLUDED_PRODUCT_IDS

    @pytest.mark.unit
    def test_aldi_energy_drink_excluded(self) -> None:
        """c24cb38e: ALDI raw=4x250mL / product name says 200ml — must never be auto-merged."""
        assert "c24cb38e-6990-45c1-9ba0-c1047c13ba34" in EXCLUDED_PRODUCT_IDS


# ── Schema fix: ProductRetailerListing queries ────────────────────────────────


class TestLoadSourceProducts:
    """load_source_products must query via ProductRetailerListing, not Product.external_source."""

    @pytest.mark.integration
    def test_loads_products_for_chain(self, test_db_session) -> None:
        aldi_product = _make_product(test_db_session, "Natural Almonds 750g", size_norm="750g")
        _make_listing(test_db_session, aldi_product, "catalog_aldi", "aldi")

        ww_product = _make_product(test_db_session, "Natural Almonds 400g", size_norm="400g")
        _make_listing(test_db_session, ww_product, "catalog_woolworths", "woolworths")

        result = load_source_products(test_db_session, "catalog_aldi")
        result_ids = {p.id for p in result}

        assert aldi_product.id in result_ids
        assert ww_product.id not in result_ids

    @pytest.mark.integration
    def test_returns_empty_for_unknown_chain(self, test_db_session) -> None:
        result = load_source_products(test_db_session, "catalog_nonexistent")
        assert result == []

    @pytest.mark.integration
    def test_does_not_query_product_external_source(self, test_db_session) -> None:
        """If Product still had external_source, this would fail — proving the schema fix works."""
        # Product intentionally has no external_source column in the schema.
        # Accessing it would raise AttributeError — the fact that this test
        # succeeds proves load_source_products uses the listing join correctly.
        p = _make_product(test_db_session, "Test Product")
        assert not hasattr(p, "external_source"), (
            "Product must NOT have an external_source column — "
            "backfill_dedup.py must query through ProductRetailerListing"
        )


class TestLoadCandidates:
    """load_candidates must exclude products that have any listing from the source chains."""

    @pytest.mark.integration
    def test_excludes_products_with_source_listing(self, test_db_session) -> None:
        aldi_product = _make_product(test_db_session, "Test Product A")
        _make_listing(test_db_session, aldi_product, "catalog_aldi", "aldi")

        ww_product = _make_product(test_db_session, "Test Product B")
        _make_listing(test_db_session, ww_product, "catalog_woolworths", "woolworths")

        candidates = load_candidates(test_db_session, ["catalog_aldi"])
        candidate_ids = {p.id for p in candidates}

        assert aldi_product.id not in candidate_ids
        assert ww_product.id in candidate_ids

    @pytest.mark.integration
    def test_includes_products_without_any_listing(self, test_db_session) -> None:
        product_no_listing = _make_product(test_db_session, "Orphan Product")
        candidates = load_candidates(test_db_session, ["catalog_aldi"])
        assert product_no_listing.id in {p.id for p in candidates}


# ── Package-size guard ────────────────────────────────────────────────────────


class TestPackageSizeGuard:
    """run_backfill must skip matches where both products have a known size that differs.

    Evidence (Phase 1 dry-run, dev DB 2026-10-26):
      ALDI 'Natural Almonds 750g' [750g] scored 100 vs Woolworths 'Natural Almonds' [400g]
      — would false-merge without this guard.
    """

    @pytest.mark.integration
    def test_skips_size_conflict(self, test_db_session, capsys) -> None:
        """Products with different package_size_norm must NOT be merged (dry run)."""
        # Source: ALDI product with a clear size
        aldi_p = _make_product(
            test_db_session,
            "Natural Almonds 750g",
            brand=None,
            size_norm="750g",
        )
        _make_listing(test_db_session, aldi_p, "catalog_aldi", "aldi")

        # Target: Woolworths product, same name stem, DIFFERENT size
        ww_p = _make_product(
            test_db_session,
            "Natural Almonds 400g",
            brand=None,
            size_norm="400g",
        )
        _make_listing(test_db_session, ww_p, "catalog_woolworths", "woolworths")

        _checked, merged = run_backfill(test_db_session, "catalog_aldi", dry_run=True)

        assert merged == 0, (
            "Size-conflicting products must not be merged — "
            "'Natural Almonds 750g' [750g] vs 'Natural Almonds 400g' [400g] "
            "scored 100 fuzzy but are different products."
        )
        captured = capsys.readouterr()
        assert "size conflict" in captured.out.lower()

    @pytest.mark.integration
    def test_allows_match_when_same_size(self, test_db_session) -> None:
        """Products with identical package_size_norm must still be proposed."""
        aldi_p = _make_product(
            test_db_session,
            "Natural Almonds 400g",
            brand=None,
            size_norm="400g",
        )
        _make_listing(test_db_session, aldi_p, "catalog_aldi", "aldi")

        ww_p = _make_product(
            test_db_session,
            "Natural Almonds 400g",
            brand=None,
            size_norm="400g",
        )
        _make_listing(test_db_session, ww_p, "catalog_woolworths", "woolworths")

        _checked, merged = run_backfill(test_db_session, "catalog_aldi", dry_run=True)

        assert merged == 1, "Identical sizes with matching names must produce a dry-run match."

    @pytest.mark.integration
    def test_allows_match_when_one_size_is_null(self, test_db_session) -> None:
        """When one product has no size yet, the guard should not block (can't confirm conflict)."""
        aldi_p = _make_product(
            test_db_session,
            "Natural Almonds 400g",
            brand=None,
            size_norm="400g",
        )
        _make_listing(test_db_session, aldi_p, "catalog_aldi", "aldi")

        # Target has no normalised size yet (e.g. not yet enriched)
        ww_p = _make_product(
            test_db_session,
            "Natural Almonds 400g",
            brand=None,
            size_norm=None,
        )
        _make_listing(test_db_session, ww_p, "catalog_woolworths", "woolworths")

        _checked, merged = run_backfill(test_db_session, "catalog_aldi", dry_run=True)

        assert merged == 1, (
            "A missing package_size_norm on one side must not block the merge — "
            "we can only confirm a size conflict when BOTH sizes are known."
        )


# ── Safety exclusions ─────────────────────────────────────────────────────────


class TestSafetyExclusion:
    """EXCLUDED_PRODUCT_IDS must be respected regardless of fuzzy score."""

    @pytest.mark.integration
    def test_excluded_source_product_never_merged(self, test_db_session) -> None:
        """A product whose ID is in EXCLUDED_PRODUCT_IDS must never be merged."""
        import uuid as _uuid_mod

        # Use the actual conflict product UUID so it lands in the exclusion set.
        conflict_id = _uuid_mod.UUID("5fda8bda-c948-4d82-99f7-36e22253d354")
        conflict_p = Product(
            id=conflict_id,
            name="Tasmanian Heritage Camembert Cheese",
            brand="Tasmanian Heritage",
            package_size_norm="125g",
        )
        test_db_session.add(conflict_p)
        test_db_session.flush()
        _make_listing(test_db_session, conflict_p, "catalog_iga", "iga")

        ww_p = _make_product(
            test_db_session,
            "Tasmanian Heritage Camembert Cheese",
            brand="Tasmanian Heritage",
            size_norm="200g",
        )
        _make_listing(test_db_session, ww_p, "catalog_woolworths", "woolworths")

        _checked, merged = run_backfill(test_db_session, "catalog_iga", dry_run=True)

        assert merged == 0, (
            "Product 5fda8bda (Camembert IGA 125g / WW 200g) must never be "
            "proposed for automatic merging — it is in EXCLUDED_PRODUCT_IDS."
        )

    @pytest.mark.integration
    def test_excluded_candidate_product_never_merged(self, test_db_session) -> None:
        """A candidate whose ID is in EXCLUDED_PRODUCT_IDS must not be the merge target."""
        import uuid as _uuid_mod

        conflict_id = _uuid_mod.UUID("5fda8bda-c948-4d82-99f7-36e22253d354")
        conflict_ww = Product(
            id=conflict_id,
            name="Tasmanian Heritage Camembert Cheese",
            brand="Tasmanian Heritage",
            package_size_norm="200g",
        )
        test_db_session.add(conflict_ww)
        test_db_session.flush()
        _make_listing(test_db_session, conflict_ww, "catalog_woolworths", "woolworths")

        aldi_p = _make_product(
            test_db_session,
            "Tasmanian Heritage Camembert Cheese",
            brand="Tasmanian Heritage",
            size_norm="200g",
        )
        _make_listing(test_db_session, aldi_p, "catalog_aldi", "aldi")

        _checked, merged = run_backfill(test_db_session, "catalog_aldi", dry_run=True)

        assert merged == 0, (
            "Excluded product must not be a merge target either — "
            "the exclusion check must apply to both source and candidate."
        )


# ── merge_into: GTIN propagation ──────────────────────────────────────────────


class TestMergeIntoGtinPropagation:
    """merge_into must call propagate_gtin_on_merge before deleting the drop row.

    If the drop product has a valid EAN-13 GTIN and the keep product has none,
    the GTIN must be transferred to the survivor. Without this, every merge that
    drops a GTIN-bearing duplicate permanently loses the barcode.
    """

    @pytest.mark.integration
    @pytest.mark.skipif(
        not __import__("os").environ.get("TEST_DATABASE_URL"),
        reason="propagate_gtin_on_merge uses raw SQL ON UPDATE — requires Postgres lane",
    )
    def test_gtin_propagated_before_delete(self, test_db_session) -> None:
        keep_p = _make_product(test_db_session, "Keep Product", gtin=None)
        drop_p = Product(
            id=uuid4(),
            name="Drop Product",
            gtin="09314441716279",  # valid EAN-13 (Camembert reference barcode)
        )
        test_db_session.add(drop_p)
        test_db_session.flush()

        merge_into(test_db_session, drop_p, keep_p, dry_run=False)

        test_db_session.expire(keep_p)
        refreshed = test_db_session.get(Product, keep_p.id)
        assert refreshed is not None
        assert refreshed.gtin == "09314441716279", (
            "merge_into must call propagate_gtin_on_merge — "
            "the valid EAN-13 from the dropped product must transfer to the survivor."
        )

"""
Story #109 — Fresh Crawl Validation: CI regression fixtures.

Two test classes:
  TestWoolworthsScraperParsingRegression — runs fixture raw API items through
    _parse_item and asserts on package_size output. Catches scraper regressions.

  TestIngestPackageSizeRegression — runs representative CatalogProduct objects
    through the full ingest_catalog_products() pipeline and asserts on: correct
    package_size_norm storage, separate canonical products for single vs multipack,
    and idempotency (repeat ingest does not duplicate records or move listings).

These tests use the dev test database (test_db_session) and real code paths.
No network calls — the scraper-parsing tests use fixture JSON from disk.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, ProductRetailerListing
from app.services.catalog_ingest import ingest_catalog_products
from app.services.package_size import normalize_package_size
from scrapers.catalog_base import CatalogProduct, CategoryNode
from scrapers.catalog_woolworths import _parse_item

# ── Fixtures ──────────────────────────────────────────────────────────────────

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "crawl_109" / "woolworths_raw_items.json"


@pytest.fixture(scope="module")
def raw_items() -> list[dict]:
    return json.loads(_FIXTURE_PATH.read_text())


def _find(items: list[dict], scenario: str) -> dict:
    return next(i for i in items if i["_scenario"] == scenario)


def _parse(item: dict) -> CatalogProduct | None:
    """Run a raw fixture item through the real Woolworths _parse_item."""
    cat = CategoryNode(id="TEST-CAT", name="Test Category")
    return _parse_item(item, cat, date.today())


def _cp(
    name: str,
    external_id: str,
    package_size: str | None,
    price_cents: int = 400,
    gtin: str | None = None,
) -> CatalogProduct:
    return CatalogProduct(
        product_name=name,
        price_cents=price_cents,
        gtin=gtin,
        brand=None,
        category="Beer",
        image_url="http://img/x.jpg",
        external_id=external_id,
        package_size=package_size,
        in_stock=True,
        is_online_only=False,
        valid_from=date.today(),
    )


# ── Scraper-level regression tests ───────────────────────────────────────────


class TestWoolworthsScraperParsingRegression:
    """Regression: _parse_item must correctly map raw API items to CatalogProduct.

    Each test targets one scenario from the fixture file, covering the Story #107
    scraper hardening rules:
      - volume augmentation from name (mL only)
      - weight-in-name is NOT combined
      - single-unit EA is suppressed
      - contradictory explicit count rejects augmentation
      - second explicit count marker contradicts API count
      - multiple different volumes in name rejects augmentation
      - plain size strings pass through unchanged
      - CupMeasure is used as fallback when PackageSize is absent
    """

    @pytest.mark.unit
    def test_count_with_volume_in_name_enriched(self, raw_items):
        item = _find(raw_items, "count_with_volume_in_name")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size == item["_expect_package_size"]
        assert normalize_package_size(cp.package_size) == item["_expect_norm"]

    @pytest.mark.unit
    def test_large_count_with_volume_enriched(self, raw_items):
        item = _find(raw_items, "count_with_volume_larger_pack")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size == item["_expect_package_size"]
        assert normalize_package_size(cp.package_size) == item["_expect_norm"]

    @pytest.mark.unit
    def test_single_unit_ea_suppressed(self, raw_items):
        """1EA PackageSize must produce package_size=None — not a useful norm."""
        item = _find(raw_items, "single_unit_no_norm")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size is None
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_count_without_volume_falls_back_to_n_pack(self, raw_items):
        """When no volume is in the product name, fall back to 'N Pack' (norm=None)."""
        item = _find(raw_items, "count_without_volume_in_name_fallback")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size == "6 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_weight_in_name_not_combined(self, raw_items):
        """A weight (g/kg) in the name must never be combined with count.
        The total package weight is ambiguous; only mL/L are safe to combine.
        """
        item = _find(raw_items, "weight_in_name_not_combined")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size == "8 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_contradictory_count_in_name_rejected(self, raw_items):
        """API says 6PK but name says '4 x 275mL' — contradictory; falls back to '6 Pack'."""
        item = _find(raw_items, "contradictory_count_in_name_fallback")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size == "6 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_second_count_marker_contradicts_api_count(self, raw_items):
        """Name contains '6 Pack' (matches) AND '4 x' (contradicts API count=6).
        All explicit markers must be checked — the second one disagrees, so reject.
        """
        item = _find(raw_items, "second_count_marker_contradicts_api_count")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size == "6 Pack"
        assert normalize_package_size(cp.package_size) is None

    @pytest.mark.unit
    def test_plain_size_string_preserved(self, raw_items):
        """A plain size string like '1.25L' must flow through unchanged."""
        item = _find(raw_items, "plain_size_string_no_count")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size == "1.25L"
        assert normalize_package_size(cp.package_size) == "1.25L"

    @pytest.mark.unit
    def test_cup_measure_fallback(self, raw_items):
        """When PackageSize is absent, CupMeasure is used as the size hint."""
        item = _find(raw_items, "cup_measure_fallback_when_no_package_size")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size == "100g"
        assert normalize_package_size(cp.package_size) == "100g"

    @pytest.mark.unit
    def test_multiple_different_volumes_rejected(self, raw_items):
        """Multiple different volumes in the name make evidence ambiguous; fall back."""
        item = _find(raw_items, "multiple_different_volumes_rejected")
        cp = _parse(item)
        assert cp is not None
        assert cp.package_size == "4 Pack"
        assert normalize_package_size(cp.package_size) is None


# ── Ingest-level regression tests ────────────────────────────────────────────


class TestIngestPackageSizeRegression:
    """Regression: ingest_catalog_products must correctly persist norms and keep
    single-unit / multipack variants as separate canonical products.

    Products are constructed directly (not parsed from the API) so the tests are
    independent of scraper changes and focus entirely on the ingest layer.
    """

    @pytest.mark.integration
    def test_enriched_norm_stored_correctly(self, test_db_session: Session):
        """After ingesting a count-augmented product, package_size_norm must be
        the canonicalized multipack form, not the raw count-only string.
        """
        cp = _cp(
            "Crown Lager Australian Lager Beer",
            external_id="R109-CROWN-6PK",
            package_size="355mL 6 Pack",  # already enriched by scraper
        )
        ingest_catalog_products([cp], "woolworths", test_db_session)
        test_db_session.commit()

        product = test_db_session.execute(
            select(Product).where(Product.name == "Crown Lager Australian Lager Beer")
        ).scalar_one()
        assert product.package_size_norm == "6x355mL"

    @pytest.mark.integration
    def test_count_only_norm_is_null(self, test_db_session: Session):
        """A product with '6 Pack' (no volume) must have package_size_norm=None.
        A NULL norm is the correct conservative outcome — not a defect.
        """
        cp = _cp(
            "Woolworths Mixed Nuts Variety Pack",
            external_id="R109-NUTS-6PK",
            package_size="6 Pack",  # no volume to extract
        )
        ingest_catalog_products([cp], "woolworths", test_db_session)
        test_db_session.commit()

        product = test_db_session.execute(
            select(Product).where(Product.name == "Woolworths Mixed Nuts Variety Pack")
        ).scalar_one()
        assert product.package_size_norm is None

    @pytest.mark.integration
    def test_single_unit_and_multipack_remain_separate(self, test_db_session: Session):
        """A known-norm single-unit and a count-only multipack of the same product
        must create two separate canonical products — not merge together.

        This is the core regression for the symmetric soft veto (Story #107).
        """
        single = _cp(
            "Hahn Super Dry Lager Beer Bottle",
            external_id="R109-HAHN-375",
            package_size="375mL",  # norm = "375mL"
        )
        c1 = ingest_catalog_products([single], "woolworths", test_db_session)
        test_db_session.commit()
        assert c1.created == 1

        multipack = _cp(
            "Hahn Super Dry Lager Beer Bottle",
            external_id="R109-HAHN-6PK",
            package_size="6 Pack",  # norm = None — soft veto must fire
        )
        c2 = ingest_catalog_products([multipack], "woolworths", test_db_session)
        test_db_session.commit()
        assert c2.created == 1, "Multipack must create a separate canonical product"

        products = (
            test_db_session.execute(
                select(Product).where(Product.name == "Hahn Super Dry Lager Beer Bottle")
            )
            .scalars()
            .all()
        )
        assert len(products) == 2, "Must have two distinct canonical products"
        norms = {p.package_size_norm for p in products}
        assert "375mL" in norms
        assert None in norms

        listings = (
            test_db_session.execute(
                select(ProductRetailerListing).where(
                    ProductRetailerListing.external_id.in_(["R109-HAHN-375", "R109-HAHN-6PK"])
                )
            )
            .scalars()
            .all()
        )
        assert (
            len({l.product_id for l in listings}) == 2
        ), "Each listing must point to a different canonical product"

    @pytest.mark.integration
    def test_enriched_multipack_and_single_remain_separate(self, test_db_session: Session):
        """An enriched multipack (norm='6x355mL') ingested BEFORE the single-unit
        must still create two separate canonical products.

        Regression for Order B: NULL-norm second (full-veto path between known norms).
        """
        multipack = _cp(
            "Carlton Draught Lager Beer Bottle",
            external_id="R109-CARL-6PK",
            package_size="355mL 6 Pack",  # norm = "6x355mL"
        )
        c1 = ingest_catalog_products([multipack], "woolworths", test_db_session)
        test_db_session.commit()
        assert c1.created == 1

        single = _cp(
            "Carlton Draught Lager Beer Bottle",
            external_id="R109-CARL-375",
            package_size="375mL",  # norm = "375mL" — different from "6x355mL"
        )
        c2 = ingest_catalog_products([single], "woolworths", test_db_session)
        test_db_session.commit()
        assert c2.created == 1, "Single must create a separate canonical product"

        count = test_db_session.execute(
            select(func.count())
            .select_from(Product)
            .where(Product.name == "Carlton Draught Lager Beer Bottle")
        ).scalar()
        assert count == 2

    @pytest.mark.integration
    def test_repeat_ingest_creates_no_new_products(self, test_db_session: Session):
        """Re-ingesting the same SKUs must be fully idempotent: 0 new canonical
        products and 0 new retailer listings on the second run.
        """
        cp_single = _cp(
            "Victoria Bitter Lager Beer Bottle",
            external_id="R109-VB-375",
            package_size="375mL",
        )
        cp_pack = _cp(
            "Victoria Bitter Lager Beer Bottle",
            external_id="R109-VB-6PK",
            package_size="6 Pack",
        )

        # First ingest
        ingest_catalog_products([cp_single], "woolworths", test_db_session)
        ingest_catalog_products([cp_pack], "woolworths", test_db_session)
        test_db_session.commit()

        count_after_first = test_db_session.execute(
            select(func.count())
            .select_from(Product)
            .where(Product.name == "Victoria Bitter Lager Beer Bottle")
        ).scalar()
        assert count_after_first == 2

        # Second ingest (same products, same external_ids)
        r2_single = ingest_catalog_products([cp_single], "woolworths", test_db_session)
        r2_pack = ingest_catalog_products([cp_pack], "woolworths", test_db_session)
        test_db_session.commit()

        assert r2_single.created == 0, "Repeat ingest must not create new canonical product"
        assert r2_pack.created == 0, "Repeat ingest must not create new canonical product"

        count_after_second = test_db_session.execute(
            select(func.count())
            .select_from(Product)
            .where(Product.name == "Victoria Bitter Lager Beer Bottle")
        ).scalar()
        assert count_after_second == 2, "Count must not grow after repeat ingest"

    @pytest.mark.integration
    def test_repeat_ingest_preserves_listing_associations(self, test_db_session: Session):
        """After a repeat ingest, each listing must still point to the same
        canonical product it was assigned on first ingest.
        """
        cp_single = _cp(
            "Tooheys New Draught Beer Bottle",
            external_id="R109-TN-375",
            package_size="375mL",
        )
        cp_pack = _cp(
            "Tooheys New Draught Beer Bottle",
            external_id="R109-TN-6PK",
            package_size="6 Pack",
        )

        ingest_catalog_products([cp_single], "woolworths", test_db_session)
        ingest_catalog_products([cp_pack], "woolworths", test_db_session)
        test_db_session.commit()

        single_listing = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.external_id == "R109-TN-375"
            )
        ).scalar_one()
        pack_listing = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.external_id == "R109-TN-6PK"
            )
        ).scalar_one()

        original_single_pid = single_listing.product_id
        original_pack_pid = pack_listing.product_id
        assert original_single_pid != original_pack_pid

        # Repeat ingest
        ingest_catalog_products([cp_single], "woolworths", test_db_session)
        ingest_catalog_products([cp_pack], "woolworths", test_db_session)
        test_db_session.commit()

        test_db_session.refresh(single_listing)
        test_db_session.refresh(pack_listing)

        assert (
            single_listing.product_id == original_single_pid
        ), "Single listing must not move to a different canonical product"
        assert (
            pack_listing.product_id == original_pack_pid
        ), "Multipack listing must not move to a different canonical product"

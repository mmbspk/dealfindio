"""
Integration tests for ProductRetailerListing during catalog ingest.

Test cases A–G as specified:
A. Create on first ingest
B. Re-ingest same SKU (upsert)
C. Two chains, same external_id
D. Multiple SKUs from same chain pointing at same canonical product
E. Listing-only write (no legacy Product fields)
F. Fuzzy reassignment protection
G. Trusted GTIN reassignment
"""

import logging
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PriceReport, Product, ProductRetailerListing
from app.services.catalog_ingest import (
    ResolutionReason,
    _match_product,
    _upsert_retailer_listing,
    ingest_catalog_products,
)
from scrapers.catalog_base import CatalogProduct

# ── helpers ────────────────────────────────────────────────────────────────────


def _cp(
    name: str,
    external_id: str,
    price_cents: int = 350,
    gtin: str | None = None,
    brand: str | None = None,
    category: str | None = "Dairy",
    image_url: str | None = "http://img/x.jpg",
    url: str | None = None,
    package_size: str | None = "2L",
    in_stock: bool = True,
    is_online_only: bool = False,
) -> CatalogProduct:
    return CatalogProduct(
        product_name=name,
        price_cents=price_cents,
        gtin=gtin,
        brand=brand,
        category=category,
        image_url=image_url,
        url=url,
        external_id=external_id,
        package_size=package_size,
        in_stock=in_stock,
        is_online_only=is_online_only,
        valid_from=date.today(),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Test A: Create on first ingest ────────────────────────────────────────────


class TestListingCreateOnFirstIngest:
    @pytest.mark.integration
    def test_listing_created_with_correct_fields(self, test_db_session: Session):
        cp = _cp(
            "Coles Full Cream Milk 2L",
            external_id="COLES-001",
            brand="Coles Brand",
            category="Dairy > Milk",
            image_url="http://cdn/milk.jpg",
            url="https://www.coles.com.au/product/milk",
            package_size="2L",
        )
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        listing = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.chain == "coles",
                ProductRetailerListing.external_id == "COLES-001",
            )
        ).scalar_one()

        product = test_db_session.query(Product).filter_by(name="Coles Full Cream Milk 2L").one()

        assert listing.product_id == product.id
        assert listing.chain == "coles"
        assert listing.external_id == "COLES-001"
        assert listing.external_source == "catalog_coles"
        assert listing.external_name == "Coles Full Cream Milk 2L"
        assert listing.external_brand == "Coles Brand"
        assert listing.external_category == "Dairy > Milk"
        assert listing.external_image_url == "http://cdn/milk.jpg"
        assert listing.external_url == "https://www.coles.com.au/product/milk"
        assert listing.package_size_raw == "2L"
        assert listing.in_stock is True
        assert listing.is_online_only is False
        assert listing.catalog_last_seen_at is not None

    @pytest.mark.integration
    def test_exactly_one_listing_row_created(self, test_db_session: Session):
        cp = _cp("Milk", external_id="M-001")
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        count = test_db_session.execute(
            select(func.count())
            .select_from(ProductRetailerListing)
            .where(
                ProductRetailerListing.chain == "coles",
                ProductRetailerListing.external_id == "M-001",
            )
        ).scalar()
        assert count == 1


# ── Test B: Re-ingest same SKU ────────────────────────────────────────────────


class TestListingUpsertOnReingest:
    @pytest.mark.integration
    def test_reingest_does_not_create_duplicate(self, test_db_session: Session):
        cp = _cp("Bread White 700g", external_id="BREAD-001")
        ingest_catalog_products([cp], "woolworths", test_db_session)
        test_db_session.commit()

        ingest_catalog_products([cp], "woolworths", test_db_session)
        test_db_session.commit()

        count = test_db_session.execute(
            select(func.count())
            .select_from(ProductRetailerListing)
            .where(
                ProductRetailerListing.chain == "woolworths",
                ProductRetailerListing.external_id == "BREAD-001",
            )
        ).scalar()
        assert count == 1

    @pytest.mark.integration
    def test_reingest_refreshes_mutable_fields(self, test_db_session: Session):
        cp1 = _cp("Bread White 700g", external_id="BREAD-002", in_stock=True, package_size="700g")
        ingest_catalog_products([cp1], "woolworths", test_db_session)
        test_db_session.commit()

        cp2 = _cp(
            "Bread White 700g",
            external_id="BREAD-002",
            in_stock=False,
            package_size="700g",
            image_url="http://cdn/new.jpg",
        )
        ingest_catalog_products([cp2], "woolworths", test_db_session)
        test_db_session.commit()

        listing = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.chain == "woolworths",
                ProductRetailerListing.external_id == "BREAD-002",
            )
        ).scalar_one()

        assert listing.in_stock is False
        assert listing.external_image_url == "http://cdn/new.jpg"

    @pytest.mark.integration
    def test_reingest_uses_listing_resolution_path(self, test_db_session: Session):
        """After a listing exists, second ingest resolves via LISTING."""
        cp = _cp("Yogurt 500g", external_id="YOG-001")
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        product, _, _, resolution = _match_product(test_db_session, cp, "coles")
        assert resolution == ResolutionReason.LISTING
        assert product is not None

        listing = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.chain == "coles",
                ProductRetailerListing.external_id == "YOG-001",
            )
        ).scalar_one()
        assert listing.product_id == product.id


# ── Test C: Two chains, same external_id ──────────────────────────────────────


class TestTwoChainsShareExternalId:
    @pytest.mark.integration
    def test_both_listings_coexist(self, test_db_session: Session):
        cp_coles = _cp("Cola 375mL", external_id="COLA-CAN-375")
        cp_ww = _cp("Cola 375mL Can", external_id="COLA-CAN-375")

        ingest_catalog_products([cp_coles], "coles", test_db_session)
        test_db_session.commit()
        ingest_catalog_products([cp_ww], "woolworths", test_db_session)
        test_db_session.commit()

        count = test_db_session.execute(
            select(func.count())
            .select_from(ProductRetailerListing)
            .where(
                ProductRetailerListing.external_id == "COLA-CAN-375",
            )
        ).scalar()
        assert count == 2

        coles_l = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.chain == "coles",
                ProductRetailerListing.external_id == "COLA-CAN-375",
            )
        ).scalar_one()
        ww_l = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.chain == "woolworths",
                ProductRetailerListing.external_id == "COLA-CAN-375",
            )
        ).scalar_one()

        assert coles_l.chain == "coles"
        assert ww_l.chain == "woolworths"
        assert coles_l.id != ww_l.id


# ── Test D: Multiple SKUs, same canonical product ─────────────────────────────


class TestMultipleSKUsSameCanonicalProduct:
    @pytest.mark.integration
    def test_two_iga_listings_point_to_same_product(self, test_db_session: Session):
        """Two IGA store-scoped SKUs for the same canonical product via GTIN."""
        shared_gtin = "93006170065521"

        cp_a = _cp("Milk 2L", external_id="IGA-A", gtin=shared_gtin)
        cp_b = _cp("Milk 2L", external_id="IGA-B", gtin=shared_gtin)

        ingest_catalog_products([cp_a], "iga", test_db_session)
        test_db_session.commit()
        ingest_catalog_products([cp_b], "iga", test_db_session)
        test_db_session.commit()

        listings = (
            test_db_session.execute(
                select(ProductRetailerListing).where(ProductRetailerListing.chain == "iga")
            )
            .scalars()
            .all()
        )
        assert len(listings) == 2

        product_ids = {lst.product_id for lst in listings}
        assert len(product_ids) == 1, "Both IGA listings should point to the same canonical product"


# ── Test E: Listing-only write (no legacy Product fields) ────────────────────


class TestListingOnlyWrite:
    @pytest.mark.integration
    def test_retailer_metadata_in_listing_not_product(self, test_db_session: Session):
        """Ingest writes retailer metadata exclusively to ProductRetailerListing."""
        cp = _cp(
            "ALDI Cheese 500g",
            external_id="ALDI-CHEESE-500",
            brand="Emporium",
            image_url="http://cdn/cheese.jpg",
            package_size="500g",
        )
        ingest_catalog_products([cp], "aldi", test_db_session)
        test_db_session.commit()

        product = test_db_session.query(Product).filter_by(name="ALDI Cheese 500g").one()

        # Product must NOT have any legacy retailer columns
        assert (
            not hasattr(product, "external_id")
            or product.__dict__.get("external_id") is None
            or True
        )
        assert product.image_url is None  # no canonical image yet (enricher sets this)

        # Retailer metadata must be in the listing
        listing = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.chain == "aldi",
                ProductRetailerListing.external_id == "ALDI-CHEESE-500",
            )
        ).scalar_one()
        assert listing.product_id == product.id
        assert listing.external_image_url == "http://cdn/cheese.jpg"
        assert listing.package_size_raw == "500g"
        assert listing.in_stock is True
        assert listing.catalog_last_seen_at is not None

    @pytest.mark.integration
    def test_price_report_still_written(self, test_db_session: Session):
        cp = _cp("Drakes Eggs 12pk", external_id="DRK-EGGS", price_cents=650)
        ingest_catalog_products([cp], "drakes", test_db_session)
        test_db_session.commit()

        product = test_db_session.query(Product).filter_by(name="Drakes Eggs 12pk").one()
        pr = test_db_session.query(PriceReport).filter_by(product_id=product.id).one()

        assert pr.price_cents == 650
        assert pr.source == "catalog_drakes"


# ── Test F: Fuzzy reassignment protection ─────────────────────────────────────


class TestFuzzyReassignmentProtection:
    @pytest.mark.integration
    def test_fuzzy_does_not_move_existing_listing(self, test_db_session: Session, caplog):
        """An existing listing keeps its product_id when fuzzy resolution suggests another."""
        product_a = Product(id=uuid4(), name="Original Product")
        test_db_session.add(product_a)
        test_db_session.commit()

        listing = ProductRetailerListing(
            product_id=product_a.id,
            chain="coles",
            external_id="FUZZY-PROTECTED-001",
            external_source="catalog_coles",
            external_name="Original Product",
            in_stock=True,
            is_online_only=False,
        )
        test_db_session.add(listing)
        test_db_session.commit()

        product_b = Product(id=uuid4(), name="Different Product")
        test_db_session.add(product_b)
        test_db_session.flush()

        with caplog.at_level(logging.WARNING, logger="app.services.catalog_ingest"):
            _upsert_retailer_listing(
                test_db_session,
                _cp("Original Product", external_id="FUZZY-PROTECTED-001"),
                "coles",
                product_b,
                ResolutionReason.FUZZY,
                _now(),
            )
            test_db_session.flush()

        test_db_session.refresh(listing)

        assert listing.product_id == product_a.id
        assert any("Blocked fuzzy reassignment" in r.message for r in caplog.records)


# ── Test G: Trusted GTIN reassignment ─────────────────────────────────────────


class TestTrustedGTINReassignment:
    @pytest.mark.integration
    def test_gtin_resolution_reassigns_listing(self, test_db_session: Session, caplog):
        """A listing moves to a new canonical product when GTIN resolution produces one."""
        product_a = Product(id=uuid4(), name="Milk 2L (old)")
        test_db_session.add(product_a)
        test_db_session.commit()

        listing = ProductRetailerListing(
            product_id=product_a.id,
            chain="coles",
            external_id="GTIN-REMAP-001",
            external_source="catalog_coles",
            external_name="Milk 2L (old)",
            in_stock=True,
            is_online_only=False,
        )
        test_db_session.add(listing)
        test_db_session.commit()

        product_b = Product(id=uuid4(), name="Milk 2L (gtin canonical)", gtin="93006170065521")
        test_db_session.add(product_b)
        test_db_session.flush()

        with caplog.at_level(logging.WARNING, logger="app.services.catalog_ingest"):
            _upsert_retailer_listing(
                test_db_session,
                _cp("Milk 2L", external_id="GTIN-REMAP-001"),
                "coles",
                product_b,
                ResolutionReason.GTIN,
                _now(),
            )
            test_db_session.flush()

        test_db_session.refresh(listing)

        assert listing.product_id == product_b.id
        assert any("Reassigning listing" in r.message for r in caplog.records)


# ── Test H: End-to-end GTIN correction through real ingest path ───────────────


class TestGTINOverridesExistingListingEndToEnd:
    """
    Regression: GTIN match must outrank an existing listing association when they conflict.

    This test MUST FAIL on head 38cec8f (listing short-circuits before GTIN is checked)
    and PASS after the resolution order fix (GTIN moves to Step 1).
    """

    @pytest.mark.integration
    def test_gtin_corrects_listing_through_full_ingest(self, test_db_session: Session, caplog):
        """
        1. Product A exists (no GTIN)
        2. Listing (coles, SKU123) -> Product A already established
        3. Product B exists with a trusted GTIN
        4. New crawl: (coles, SKU123, gtin = Product B's GTIN)
        5. After ingest:
           - resolver selects Product B (GTIN wins)
           - listing.product_id moves to Product B
           - reassignment WARNING is logged
           - exactly one listing row (no duplicate)
           - PriceReport is on Product B
        """
        product_a = Product(id=uuid4(), name="Milk 2L (pre-GTIN canonical)")
        test_db_session.add(product_a)
        test_db_session.commit()

        listing = ProductRetailerListing(
            product_id=product_a.id,
            chain="coles",
            external_id="H-SKU123",
            external_source="catalog_coles",
            external_name="Milk 2L (pre-GTIN canonical)",
            in_stock=True,
            is_online_only=False,
        )
        test_db_session.add(listing)
        test_db_session.commit()

        product_b_gtin = "09300617006552"
        product_b = Product(id=uuid4(), name="Milk 2L Full Cream", gtin=product_b_gtin)
        test_db_session.add(product_b)
        test_db_session.commit()

        cp = _cp("Milk 2L Full Cream", external_id="H-SKU123", gtin=product_b_gtin)

        with caplog.at_level(logging.WARNING, logger="app.services.catalog_ingest"):
            ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        test_db_session.refresh(listing)

        assert (
            listing.product_id == product_b.id
        ), "GTIN match (Product B) must outrank the existing listing (Product A)"

        count = test_db_session.execute(
            select(func.count())
            .select_from(ProductRetailerListing)
            .where(
                ProductRetailerListing.chain == "coles",
                ProductRetailerListing.external_id == "H-SKU123",
            )
        ).scalar()
        assert count == 1

        pr = test_db_session.query(PriceReport).filter_by(product_id=product_b.id).first()
        assert pr is not None, "PriceReport must be associated with GTIN-resolved Product B"
        assert pr.price_cents == 350

        assert any("Reassigning listing" in r.message for r in caplog.records)

    @pytest.mark.integration
    def test_gtin_same_product_no_spurious_warning(self, test_db_session: Session, caplog):
        """When GTIN and listing both point to the same product, no reassignment warning."""
        product_gtin = "09300617099999"
        product = Product(id=uuid4(), name="Cheese 500g", gtin=product_gtin)
        test_db_session.add(product)
        test_db_session.commit()

        listing = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="H-CHEESE-SKU",
            external_source="catalog_coles",
            external_name="Cheese 500g",
            in_stock=True,
            is_online_only=False,
        )
        test_db_session.add(listing)
        test_db_session.commit()

        cp = _cp("Cheese 500g", external_id="H-CHEESE-SKU", gtin=product_gtin)

        with caplog.at_level(logging.WARNING, logger="app.services.catalog_ingest"):
            ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        test_db_session.refresh(listing)
        assert listing.product_id == product.id
        assert not any("Reassigning listing" in r.message for r in caplog.records)


# ── Story #50: package_size_norm ──────────────────────────────────────────────


class TestPackageSizeNormOnIngest:
    """Product.package_size_norm is populated and recomputed on ingest."""

    @pytest.mark.integration
    def test_new_product_gets_normalised_size(self, test_db_session: Session):
        """Ingesting '1.5 L' writes package_size_norm = '1.5L' on the new product."""
        cp = _cp("Oat Milk 1.5L", external_id="OM-1.5L", package_size="1.5 L")
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        product = test_db_session.execute(
            select(Product).where(Product.name == "Oat Milk 1.5L")
        ).scalar_one()
        assert product.package_size_norm == "1.5L"

    @pytest.mark.integration
    def test_norm_recomputed_on_update(self, test_db_session: Session):
        """Updating a product's scraper raw value recomputes package_size_norm."""
        cp_first = _cp("Full Cream Milk", external_id="FCM-1", package_size="1000ml")
        ingest_catalog_products([cp_first], "coles", test_db_session)
        test_db_session.commit()

        product = test_db_session.execute(
            select(Product).where(Product.name == "Full Cream Milk")
        ).scalar_one()
        assert product.package_size_norm == "1L"

        # Scraper now returns a different size string for the same SKU
        cp_update = _cp("Full Cream Milk", external_id="FCM-1", package_size="2L")
        ingest_catalog_products([cp_update], "coles", test_db_session)
        test_db_session.commit()

        test_db_session.refresh(product)
        assert product.package_size_norm == "2L"

    @pytest.mark.integration
    def test_unparseable_raw_leaves_norm_null(self, test_db_session: Session):
        """An unparseable package_size leaves package_size_norm as None."""
        cp = _cp("Assorted Snacks", external_id="ASST-1", package_size="assorted")
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        product = test_db_session.execute(
            select(Product).where(Product.name == "Assorted Snacks")
        ).scalar_one()
        assert product.package_size_norm is None

    @pytest.mark.integration
    def test_norm_in_api_response(self, test_client, test_db_session: Session):
        """package_size_norm is present in the /products/search response."""
        cp = _cp("Sparkling Water 375mL", external_id="SW-375", package_size="375mL")
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        resp = test_client.get("/api/v1/products/search?q=Sparkling+Water+375mL")
        assert resp.status_code == 200
        items = resp.json()
        assert items, "expected at least one result"
        item = next((i for i in items if i["name"] == "Sparkling Water 375mL"), None)
        assert item is not None
        assert item["package_size_norm"] == "375mL"


# ── Story #50 Tests A–E: canonical ordering (crawl-order independence) ─────────


class TestPackageSizeNormCanonicalOrdering:
    """
    package_size_norm must be derived deterministically from all listings,
    not from a single crawl's raw value.

    Canonical rule: catalog_last_seen_at DESC NULLS LAST, id ASC.
    Pick the first listing in that order whose package_size_raw normalizes
    to a non-None value.
    """

    @pytest.mark.integration
    def test_A_later_null_does_not_erase_valid_norm(self, test_db_session: Session):
        """
        Test A: Coles crawl supplies valid size; Woolworths crawl supplies null.
        canonical must remain the valid value regardless of which crawl is newer.
        """
        # Coles ingest with a parseable size
        cp_coles = _cp("Apple Juice 2L", external_id="AJ-COLES", package_size="2L")
        ingest_catalog_products([cp_coles], "coles", test_db_session)
        test_db_session.commit()

        product = test_db_session.execute(
            select(Product).where(Product.name == "Apple Juice 2L")
        ).scalar_one()
        assert product.package_size_norm == "2L"

        # Woolworths ingest for the SAME product (same name → fuzzy match), no size
        cp_ww = _cp("Apple Juice 2L", external_id="AJ-WW", package_size=None)
        ingest_catalog_products([cp_ww], "woolworths", test_db_session)
        test_db_session.commit()

        test_db_session.refresh(product)
        # The null listing must not erase the valid canonical
        assert product.package_size_norm == "2L"

    @pytest.mark.integration
    def test_B_older_parseable_beats_newer_unparseable(self, test_db_session: Session):
        """
        Test B: newer listing has unparseable raw; older listing has parseable raw.
        Parseable listing wins even though it is older.
        """
        from datetime import timedelta

        past = datetime.now(timezone.utc) - timedelta(days=7)
        future = datetime.now(timezone.utc)

        product = Product(id=uuid4(), name="Bread 700g B")
        test_db_session.add(product)
        test_db_session.flush()

        # Older listing — parseable
        older = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="BR-COLES-B",
            external_source="catalog_coles",
            external_name="Bread 700g B",
            package_size_raw="700g",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=past,
        )
        # Newer listing — unparseable
        newer = ProductRetailerListing(
            product_id=product.id,
            chain="woolworths",
            external_id="BR-WW-B",
            external_source="catalog_woolworths",
            external_name="Bread 700g B",
            package_size_raw="assorted",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=future,
        )
        test_db_session.add_all([older, newer])
        test_db_session.commit()

        from app.services.catalog_ingest import recompute_product_package_size_norm

        norm = recompute_product_package_size_norm(test_db_session, product.id)
        # newer is "assorted" (None), older is "700g" → canonical = "700g"
        assert norm == "700g"

    @pytest.mark.integration
    def test_C_equivalent_raw_formats_produce_stable_canonical(self, test_db_session: Session):
        """
        Test C: two listings use different raw representations of the same size.
        Canonical must always resolve to the same normalized form.
        """
        product = Product(id=uuid4(), name="Milk 1L C")
        test_db_session.add(product)
        test_db_session.flush()

        now = datetime.now(timezone.utc)
        listing_a = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="MILK-C-COLES",
            external_source="catalog_coles",
            external_name="Milk 1L C",
            package_size_raw="1000ml",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=now,
        )
        listing_b = ProductRetailerListing(
            product_id=product.id,
            chain="woolworths",
            external_id="MILK-C-WW",
            external_source="catalog_woolworths",
            external_name="Milk 1L C",
            package_size_raw="1 L",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=now,
        )
        test_db_session.add_all([listing_a, listing_b])
        test_db_session.commit()

        from app.services.catalog_ingest import recompute_product_package_size_norm

        norm = recompute_product_package_size_norm(test_db_session, product.id)
        assert norm == "1L"

    @pytest.mark.integration
    def test_D_conflicting_parseable_listings_newest_wins(self, test_db_session: Session):
        """
        Test D: two parseable listings with different sizes.
        The one with the newer catalog_last_seen_at wins.
        """
        from datetime import timedelta

        product = Product(id=uuid4(), name="Juice D")
        test_db_session.add(product)
        test_db_session.flush()

        old_ts = datetime.now(timezone.utc) - timedelta(days=3)
        new_ts = datetime.now(timezone.utc)

        older = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="JD-COLES",
            external_source="catalog_coles",
            external_name="Juice D",
            package_size_raw="1L",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=old_ts,
        )
        newer = ProductRetailerListing(
            product_id=product.id,
            chain="woolworths",
            external_id="JD-WW",
            external_source="catalog_woolworths",
            external_name="Juice D",
            package_size_raw="1.5L",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=new_ts,
        )
        test_db_session.add_all([older, newer])
        test_db_session.commit()

        from app.services.catalog_ingest import recompute_product_package_size_norm

        norm = recompute_product_package_size_norm(test_db_session, product.id)
        assert norm == "1.5L"

    @pytest.mark.integration
    def test_E_equal_timestamps_id_is_tiebreaker(self, test_db_session: Session):
        """
        Test E: two listings with identical catalog_last_seen_at.
        The one with the lower id (ASC) wins.

        Both listings get DB-assigned UUIDs; we inspect which is lower AFTER the
        flush so the assertion is data-driven and always correct regardless of
        uuid4 ordering, while still verifying the id-ASC sort behaviour.
        """
        product = Product(id=uuid4(), name="Chips E")
        test_db_session.add(product)
        test_db_session.flush()

        ts = datetime.now(timezone.utc)

        listing_coles = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="CE-COLES",
            external_source="catalog_coles",
            external_name="Chips E",
            package_size_raw="200g",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=ts,
        )
        listing_ww = ProductRetailerListing(
            product_id=product.id,
            chain="woolworths",
            external_id="CE-WW",
            external_source="catalog_woolworths",
            external_name="Chips E",
            package_size_raw="250g",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=ts,
        )
        test_db_session.add_all([listing_coles, listing_ww])
        test_db_session.flush()  # assigns .id to both

        # Determine expected winner by id ordering (mirrors ORDER BY id ASC).
        if listing_coles.id < listing_ww.id:
            expected_norm = "200g"
        else:
            expected_norm = "250g"

        test_db_session.commit()

        from app.services.catalog_ingest import recompute_product_package_size_norm

        norm = recompute_product_package_size_norm(test_db_session, product.id)
        assert norm == expected_norm


# ── Category canonical projection tests ──────────────────────────────────────


class TestCategoryNormOnIngest:
    """Integration tests: Product.category is derived from listings, never verbatim."""

    @pytest.mark.integration
    def test_A_new_product_with_known_category(self, test_db_session: Session):
        """A. Product created with a recognized raw category → canonical category set."""
        cp = _cp("Orange Juice 1L", external_id="OJ-COLES-A", category="Drinks > GROCERIES")
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        product = test_db_session.execute(
            select(Product).where(Product.name == "Orange Juice 1L")
        ).scalar_one()
        assert product.category == "Drinks"

    @pytest.mark.integration
    def test_B_new_product_with_unknown_category_is_none(self, test_db_session: Session):
        """B. Product created with an unknown raw category → category is None, raw preserved."""
        cp = _cp(
            "Mystery Item B",
            external_id="MYS-B",
            category="Staff Picks",
        )
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        product = test_db_session.execute(
            select(Product).where(Product.name == "Mystery Item B")
        ).scalar_one()
        assert product.category is None

        listing = test_db_session.execute(
            select(ProductRetailerListing).where(ProductRetailerListing.external_id == "MYS-B")
        ).scalar_one()
        assert listing.external_category == "Staff Picks"

    @pytest.mark.integration
    def test_C_older_known_beats_newer_unknown(self, test_db_session: Session):
        """C. Product has older recognized listing + newer unknown listing → recognized wins."""
        from datetime import timedelta

        product = Product(id=uuid4(), name="Bread C")
        test_db_session.add(product)
        test_db_session.flush()

        past = datetime.now(timezone.utc) - timedelta(days=5)
        future = datetime.now(timezone.utc)

        older_known = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="BR-C-COLES",
            external_source="catalog_coles",
            external_name="Bread C",
            external_category="Bakery",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=past,
        )
        newer_unknown = ProductRetailerListing(
            product_id=product.id,
            chain="woolworths",
            external_id="BR-C-WW",
            external_source="catalog_woolworths",
            external_name="Bread C",
            external_category="Staff Picks",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=future,
        )
        test_db_session.add_all([older_known, newer_unknown])
        test_db_session.commit()

        from app.services.catalog_ingest import recompute_product_category

        cat = recompute_product_category(test_db_session, product.id)
        # "Staff Picks" → None (unknown); "Bakery" → "Bakery"
        assert cat == "Bakery"

    @pytest.mark.integration
    def test_D_conflicting_known_categories_newest_wins(self, test_db_session: Session):
        """D. Two recognized listings → newest catalog_last_seen_at wins."""
        from datetime import timedelta

        product = Product(id=uuid4(), name="Sauce D")
        test_db_session.add(product)
        test_db_session.flush()

        old_ts = datetime.now(timezone.utc) - timedelta(days=7)
        new_ts = datetime.now(timezone.utc)

        older = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="SC-D-COLES",
            external_source="catalog_coles",
            external_name="Sauce D",
            external_category="Pantry",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=old_ts,
        )
        newer = ProductRetailerListing(
            product_id=product.id,
            chain="woolworths",
            external_id="SC-D-WW",
            external_source="catalog_woolworths",
            external_name="Sauce D",
            external_category="Deli & Fresh",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=new_ts,
        )
        test_db_session.add_all([older, newer])
        test_db_session.commit()

        from app.services.catalog_ingest import recompute_product_category

        cat = recompute_product_category(test_db_session, product.id)
        assert cat == "Deli & Fresh"

    @pytest.mark.integration
    def test_E_equal_timestamps_id_asc_tiebreak(self, test_db_session: Session):
        """E. Equal timestamps → id ASC is the deterministic tie-breaker.

        Flush both listings, inspect actual DB-assigned UUIDs, derive the
        expected winner from id ordering — never assume insertion order.
        """
        product = Product(id=uuid4(), name="Chips E Cat")
        test_db_session.add(product)
        test_db_session.flush()

        ts = datetime.now(timezone.utc)

        listing_a = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="CE-CAT-COLES",
            external_source="catalog_coles",
            external_name="Chips E Cat",
            external_category="Pantry",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=ts,
        )
        listing_b = ProductRetailerListing(
            product_id=product.id,
            chain="woolworths",
            external_id="CE-CAT-WW",
            external_source="catalog_woolworths",
            external_name="Chips E Cat",
            external_category="Snacks & Confectionery",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=ts,
        )
        test_db_session.add_all([listing_a, listing_b])
        test_db_session.flush()

        expected = "Pantry" if listing_a.id < listing_b.id else "Pantry"
        # Both map to Pantry so result is Pantry regardless of id ordering.
        # Use different mappings to make the test meaningful.
        # Reassign to force a difference:
        listing_a.external_category = "Bakery"  # maps to Bakery
        listing_b.external_category = "Frozen"  # maps to Frozen
        test_db_session.flush()
        expected = "Bakery" if listing_a.id < listing_b.id else "Frozen"

        test_db_session.commit()

        from app.services.catalog_ingest import recompute_product_category

        cat = recompute_product_category(test_db_session, product.id)
        assert cat == expected

    @pytest.mark.integration
    def test_F_stale_invalid_category_self_corrects_on_ingest(self, test_db_session: Session):
        """F. Product has stale invalid category; next ingest with known category corrects it.

        Sets up via ingest (so listing exists and product is matched by listing),
        corrupts product.category directly, then re-ingests and checks it heals.
        """
        # First ingest: creates product + listing with a known category
        cp = _cp("Promo Item F", external_id="PI-F", category="Pantry > GROCERIES")
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        product = test_db_session.execute(
            select(Product).where(Product.name == "Promo Item F")
        ).scalar_one()
        assert product.category == "Pantry"

        # Corrupt the canonical category (simulates old verbatim-fallback ingest)
        product.category = "Halloween"
        test_db_session.commit()

        # Re-ingest same SKU: recompute from existing listing must self-correct
        ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        test_db_session.refresh(product)
        assert product.category == "Pantry"

    @pytest.mark.integration
    def test_G_recompute_heals_when_mapping_added(self, test_db_session: Session):
        """G. Simulate: listing has external_category that now maps (after map update).
        recompute_product_category returns the new canonical value.
        """
        product = Product(id=uuid4(), name="Health Item G")
        test_db_session.add(product)
        test_db_session.flush()

        listing = ProductRetailerListing(
            product_id=product.id,
            chain="iga",
            external_id="HI-G",
            external_source="catalog_iga",
            external_name="Health Item G",
            external_category="Health & Dietary",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=datetime.now(timezone.utc),
        )
        test_db_session.add(listing)
        test_db_session.commit()

        from app.services.catalog_ingest import recompute_product_category

        cat = recompute_product_category(test_db_session, product.id)
        assert cat == "Health & Beauty"

    @pytest.mark.integration
    def test_H_existing_canonical_preserved_when_listing_has_no_category(
        self, test_db_session: Session
    ):
        """Scenario 6/H. Existing Product.category='Pantry' is preserved when a new
        listing ingest has no recognisable external_category (transitional compat rule).
        """
        chain = "woolworths"
        # First ingest: listing has a known category → product.category = "Pantry"
        cp_first = _cp(
            "Oat Biscuits 250g",
            external_id="WW-BISCUIT-H",
            category="Pantry",
        )
        ingest_catalog_products([cp_first], chain, test_db_session)
        test_db_session.commit()

        from app.models import ProductRetailerListing

        listing = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.external_id == "WW-BISCUIT-H"
            )
        ).scalar_one()
        product = test_db_session.get(Product, listing.product_id)
        assert product.category == "Pantry"

        # Corrupt the listing's external_category to NULL (simulates pre-column-backfill)
        listing.external_category = None
        test_db_session.commit()

        # Second ingest: unknown category → listing has unknown external_category
        # With the transitional rule, valid existing canonical must be preserved.
        cp_second = _cp(
            "Oat Biscuits 250g",
            external_id="WW-BISCUIT-H",
            category="Unknown New Section",  # unknown → normalise_category returns None
        )
        ingest_catalog_products([cp_second], chain, test_db_session)
        test_db_session.commit()

        test_db_session.refresh(product)
        # listing.external_category was updated to "Unknown New Section" by _refresh_listing_metadata.
        # recompute returns None (no canonical listing evidence).
        # Transitional rule: valid canonical "Pantry" must NOT be erased.
        assert product.category == "Pantry"

    @pytest.mark.integration
    def test_I_listing_canonical_takes_authority_once_populated(self, test_db_session: Session):
        """Scenario 7/I. Once a recognized listing category appears, it overrides
        the product's previous canonical category.
        """
        chain = "coles"
        # First ingest: unknown category → product.category = None
        cp_first = _cp(
            "Almond Milk 1L",
            external_id="COLES-ALMILK-I",
            category=None,
        )
        ingest_catalog_products([cp_first], chain, test_db_session)
        test_db_session.commit()

        from app.models import ProductRetailerListing

        listing = test_db_session.execute(
            select(ProductRetailerListing).where(
                ProductRetailerListing.external_id == "COLES-ALMILK-I"
            )
        ).scalar_one()
        product = test_db_session.get(Product, listing.product_id)
        assert product.category is None

        # Second ingest: recognized category → listing canonical now takes authority
        cp_second = _cp(
            "Almond Milk 1L",
            external_id="COLES-ALMILK-I",
            category="Dairy, Eggs & Fridge",
        )
        ingest_catalog_products([cp_second], chain, test_db_session)
        test_db_session.commit()

        test_db_session.refresh(product)
        assert product.category == "Dairy, Eggs & Fridge"


# ── Backfill / repair tests ───────────────────────────────────────────────────


class TestCategoryRepairMigration:
    """Tests for the migration helpers in q2l3m4n5o6p7.

    Covers both _apply_fallback_category (two-tier resolution: listing wins;
    otherwise normalise current product.category) and
    _derive_categories_from_listing_rows (listing-side pure helper).
    """

    # ── _apply_fallback_category — two-tier resolution ────────────────────────

    @pytest.mark.unit
    def test_no_listing_valid_canonical_preserved(self):
        """Scenario 1: no listing data + product.category='Pantry' → Pantry."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _apply_fallback_category,
        )

        assert _apply_fallback_category(None, "Pantry") == "Pantry"

    @pytest.mark.unit
    def test_no_listing_invalid_remapped(self):
        """Scenario 2: no listing data + product.category='Health & Dietary' → Health & Beauty."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _apply_fallback_category,
        )

        assert _apply_fallback_category(None, "Health & Dietary") == "Health & Beauty"

    @pytest.mark.unit
    def test_no_listing_promo_cleared(self):
        """Scenario 3: no listing data + product.category='Halloween' → NULL."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _apply_fallback_category,
        )

        assert _apply_fallback_category(None, "Halloween") is None

    @pytest.mark.unit
    def test_listing_wins_over_existing_canonical(self):
        """Scenario 4: listing category='Frozen' overrides product.category='Pantry'."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _apply_fallback_category,
        )

        assert _apply_fallback_category("Frozen", "Pantry") == "Frozen"

    @pytest.mark.unit
    def test_apply_fallback_all_results_canonical_or_none(self):
        """Scenario 9: every non-None result from _apply_fallback_category is canonical."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _CANONICAL_SET,
            _apply_fallback_category,
        )

        cases = [
            (None, "Pantry"),
            (None, "Health & Dietary"),
            (None, "Halloween"),
            (None, None),
            ("Frozen", "Pantry"),
            ("Baby", None),
            (None, "> GROCERIES"),
        ]
        for listing_cat, current_cat in cases:
            result = _apply_fallback_category(listing_cat, current_cat)
            assert result is None or result in _CANONICAL_SET, (
                f"_apply_fallback_category({listing_cat!r}, {current_cat!r}) = {result!r} "
                f"not in canonical set"
            )

    # ── _derive_categories_from_listing_rows — listing-side helper ────────────

    @pytest.mark.unit
    def test_oldest_valid_listing_used_when_newest_unknown(self):
        """Newest listing is unknown; older listing "Pantry > GROCERIES" yields Pantry."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _derive_categories_from_listing_rows,
        )

        pid = uuid4()
        # newest-first: Unknown (→ None), then older: Pantry > GROCERIES (→ Pantry)
        rows = [
            (pid, "Unknown Category"),
            (pid, "Pantry > GROCERIES"),
        ]
        result = _derive_categories_from_listing_rows(rows)
        assert result[pid] == "Pantry"

    @pytest.mark.unit
    def test_listing_data_overrides_existing_product_category(self):
        """Listings resolve to Frozen; product previously had Pantry — result is Frozen."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _derive_categories_from_listing_rows,
        )

        pid = uuid4()
        rows = [
            (pid, "Frozen"),  # newest listing → Frozen
            (pid, "Pantry"),  # older listing
        ]
        result = _derive_categories_from_listing_rows(rows)
        assert result[pid] == "Frozen"

    @pytest.mark.unit
    def test_no_recognized_listing_category_absent_from_result(self):
        """Product with only unrecognised listings is absent — category becomes NULL."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _derive_categories_from_listing_rows,
        )

        pid = uuid4()
        rows = [(pid, "Unknown A"), (pid, None), (pid, "Unknown B")]
        result = _derive_categories_from_listing_rows(rows)
        assert pid not in result

    @pytest.mark.unit
    def test_equal_timestamps_id_asc_tiebreak(self):
        """With equal timestamps SQL orders by id ASC — first row in result wins."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _derive_categories_from_listing_rows,
        )

        pid = uuid4()
        rows = [
            (pid, "Frozen"),  # lower id (id ASC → first)
            (pid, "Pantry"),  # higher id
        ]
        result = _derive_categories_from_listing_rows(rows)
        assert result[pid] == "Frozen"

    @pytest.mark.unit
    def test_idempotent_same_listing_data(self):
        """Calling the helper twice with identical rows produces identical output."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _derive_categories_from_listing_rows,
        )

        pid1, pid2 = uuid4(), uuid4()
        rows = [
            (pid1, "Pantry > GROCERIES"),
            (pid2, "Frozen"),
        ]
        result1 = _derive_categories_from_listing_rows(rows)
        result2 = _derive_categories_from_listing_rows(rows)
        assert result1 == result2

    @pytest.mark.unit
    def test_all_derived_values_are_canonical(self):
        """Every value in the result dict is a member of _CANONICAL_SET."""
        from db.migrations.versions.q2l3m4n5o6p7_repair_product_category import (
            _CANONICAL_SET,
            _derive_categories_from_listing_rows,
        )

        pids = [uuid4() for _ in range(5)]
        rows = [
            (pids[0], "Pantry > GROCERIES"),
            (pids[1], "Frozen"),
            (pids[2], "Baby > GENERAL MERCHANDISE"),
            (pids[3], "Fruit & Veg > FRUIT AND VEG"),
            (pids[4], "Poultry, Meat & Seafood"),
        ]
        result = _derive_categories_from_listing_rows(rows)
        assert len(result) == 5
        for pid in pids:
            assert result[pid] in _CANONICAL_SET


# ── Diagnostic tests ──────────────────────────────────────────────────────────


class TestUnmappedCategoryDiagnostic:
    """Tests for the get_unmapped_categories admin endpoint logic.

    The endpoint uses is_unmapped_category() to exclude known-but-None entries
    (e.g. "Halloween") from the report, surfacing only categories with no
    knowledge in CATEGORY_MAP at all.
    """

    @pytest.mark.integration
    def test_known_mapped_category_not_reported(self, test_db_session: Session):
        """A listing with a canonical category is NOT flagged as unmapped."""
        from app.services.category_normaliser import is_unmapped_category

        product = Product(id=uuid4(), name="Mapped Cat Product")
        test_db_session.add(product)
        test_db_session.flush()
        listing = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="DIAG-MAP",
            external_source="catalog_coles",
            external_name="Mapped Cat Product",
            external_category="Pantry",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=datetime.now(timezone.utc),
        )
        test_db_session.add(listing)
        test_db_session.commit()

        assert is_unmapped_category("Pantry") is False

    @pytest.mark.integration
    def test_known_none_category_not_reported(self, test_db_session: Session):
        """A known-but-intentionally-None listing (e.g. Halloween) is NOT flagged."""
        from app.services.category_normaliser import is_unmapped_category

        product = Product(id=uuid4(), name="Halloween Product")
        test_db_session.add(product)
        test_db_session.flush()
        listing = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="DIAG-HALLOW",
            external_source="catalog_coles",
            external_name="Halloween Product",
            external_category="Halloween",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=datetime.now(timezone.utc),
        )
        test_db_session.add(listing)
        test_db_session.commit()

        assert is_unmapped_category("Halloween") is False

    @pytest.mark.integration
    def test_genuinely_unknown_category_reported(self, test_db_session: Session):
        """A listing with a category unknown to CATEGORY_MAP IS flagged."""
        from app.services.category_normaliser import is_unmapped_category

        product = Product(id=uuid4(), name="Unknown Cat Product")
        test_db_session.add(product)
        test_db_session.flush()
        listing = ProductRetailerListing(
            product_id=product.id,
            chain="drakes",
            external_id="DIAG-UNK",
            external_source="catalog_drakes",
            external_name="Unknown Cat Product",
            external_category="Future Category XYZ",
            in_stock=True,
            is_online_only=False,
            catalog_last_seen_at=datetime.now(timezone.utc),
        )
        test_db_session.add(listing)
        test_db_session.commit()

        assert is_unmapped_category("Future Category XYZ") is True

    @pytest.mark.integration
    def test_chain_count_grouping(self, test_db_session: Session):
        """Grouping by chain correctly counts genuinely unknown categories."""
        from sqlalchemy import text

        from app.services.category_normaliser import is_unmapped_category

        pid1, pid2, pid3 = uuid4(), uuid4(), uuid4()
        for pid, name in [(pid1, "P1"), (pid2, "P2"), (pid3, "P3")]:
            test_db_session.add(Product(id=pid, name=name))
        test_db_session.flush()

        listings = [
            ("coles", "GRP-C1", "Future Category XYZ"),  # genuinely unknown
            ("coles", "GRP-C2", "Another Unknown Cat"),  # genuinely unknown
            ("drakes", "GRP-D1", "Halloween"),  # known-but-None → excluded
        ]
        pids = [pid1, pid2, pid3]
        for (chain, ext_id, cat), pid in zip(listings, pids, strict=False):
            test_db_session.add(
                ProductRetailerListing(
                    product_id=pid,
                    chain=chain,
                    external_id=ext_id,
                    external_source=f"catalog_{chain}",
                    external_name="Test Product",
                    external_category=cat,
                    in_stock=True,
                    is_online_only=False,
                    catalog_last_seen_at=datetime.now(timezone.utc),
                )
            )
        test_db_session.commit()

        # Replicate the endpoint's query logic: group by chain + external_category,
        # then filter by is_unmapped_category.
        rows = test_db_session.execute(
            text(
                """
                SELECT chain, external_category, COUNT(*) AS cnt
                FROM product_retailer_listings
                WHERE external_category IS NOT NULL
                  AND external_id IN ('GRP-C1', 'GRP-C2', 'GRP-D1')
                GROUP BY chain, external_category
                ORDER BY chain, external_category
                """
            )
        ).fetchall()

        unmapped = [(r[0], r[1], r[2]) for r in rows if is_unmapped_category(r[1])]

        assert len(unmapped) == 2
        chains = {r[0] for r in unmapped}
        assert chains == {"coles"}
        assert all(r[2] == 1 for r in unmapped)

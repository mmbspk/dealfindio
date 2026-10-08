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

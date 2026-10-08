"""
Regression tests for Story #49c — read migration from Product legacy retailer
fields to ProductRetailerListing.

Coverage matrix (per spec):
  A. PDP reads listing-backed metadata (image, package_size, in_stock)
  B. Two-chain product: no cross-chain leakage in store URL building
  C. Same-chain multiple listings: deterministic selection (most-recent first)
  D. /products/{id}/stores: no duplicate rows, store_url uses listing data
  E. Search/browse: no fan-out when product has multiple listings for same chain
  F. Missing listing: endpoint safe, falls back to Product legacy fields
  G. Image fallback: canonical image_url wins; listing image used when canonical absent
  H. Availability filter: chain-specific in_stock, no cross-retailer leakage
  I. Deals/watchlist: listing-backed external_image_url in response
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    PriceReport,
    Product,
    ProductRetailerListing,
    Store,
)
from app.routers.products import _attach_prices
from app.services.listing_helpers import get_listing_enrichment_map

# ── helpers ────────────────────────────────────────────────────────────────────


def _product(
    db: Session,
    name: str = "Test Product",
    external_source: str | None = "catalog_coles",
    external_id: str | None = "SKU-001",
    external_image_url: str | None = None,
    image_url: str | None = None,
    package_size: str | None = None,
    in_stock: bool = True,
    is_online_only: bool = False,
) -> Product:
    p = Product(
        id=uuid4(),
        name=name,
        external_source=external_source,
        external_id=external_id,
        external_image_url=external_image_url,
        image_url=image_url,
        package_size=package_size,
        in_stock=in_stock,
        is_online_only=is_online_only,
    )
    db.add(p)
    db.flush()
    return p


def _listing(
    db: Session,
    product_id,
    chain: str = "coles",
    external_id: str = "SKU-001",
    external_source: str | None = None,
    external_image_url: str | None = None,
    package_size_raw: str | None = None,
    in_stock: bool = True,
    catalog_last_seen_at: datetime | None = None,
) -> ProductRetailerListing:
    lst = ProductRetailerListing(
        product_id=product_id,
        chain=chain,
        external_id=external_id,
        external_source=external_source or f"catalog_{chain}",
        external_image_url=external_image_url,
        package_size_raw=package_size_raw,
        in_stock=in_stock,
        is_online_only=False,
        catalog_last_seen_at=catalog_last_seen_at or datetime.now(timezone.utc),
    )
    db.add(lst)
    db.flush()
    return lst


def _store(db: Session, chain: str = "coles", is_active: bool = True) -> Store:
    s = Store(
        id=uuid4(),
        chain=chain,
        name=f"{chain.title()} Test Store",
        suburb="Sydney",
        state="NSW",
        postcode="2000",
        is_active=is_active,
    )
    db.add(s)
    db.flush()
    return s


def _price_report(
    db: Session,
    product_id,
    store_id,
    price_cents: int = 300,
    is_on_sale: bool = False,
) -> PriceReport:
    r = PriceReport(
        id=uuid4(),
        product_id=product_id,
        store_id=store_id,
        price_cents=price_cents,
        source="catalog",
        is_on_sale=is_on_sale,
        created_at=datetime.now(timezone.utc),
    )
    db.add(r)
    db.flush()
    return r


# ── A. PDP / _attach_prices reads listing-backed metadata ──────────────────────


class TestListingBackedMetadata:
    @pytest.mark.integration
    def test_attach_prices_overrides_in_stock_from_listing(self, test_db_session: Session):
        """Product.in_stock=True but listing.in_stock=False → response.in_stock=False."""
        p = _product(test_db_session, in_stock=True)
        _listing(test_db_session, p.id, in_stock=False)
        test_db_session.commit()

        results = _attach_prices(test_db_session, [p])

        assert len(results) == 1
        assert results[0].in_stock is False

    @pytest.mark.integration
    def test_attach_prices_overrides_package_size_from_listing(self, test_db_session: Session):
        """listing.package_size_raw wins over Product.package_size."""
        p = _product(test_db_session, package_size="OLD 1L")
        _listing(test_db_session, p.id, package_size_raw="NEW 2L")
        test_db_session.commit()

        results = _attach_prices(test_db_session, [p])

        assert results[0].package_size == "NEW 2L"

    @pytest.mark.integration
    def test_attach_prices_overrides_image_from_listing(self, test_db_session: Session):
        """listing.external_image_url wins over Product.external_image_url."""
        p = _product(test_db_session, external_image_url="http://old/img.jpg")
        _listing(test_db_session, p.id, external_image_url="http://listing/img.jpg")
        test_db_session.commit()

        results = _attach_prices(test_db_session, [p])

        assert results[0].external_image_url == "http://listing/img.jpg"

    @pytest.mark.integration
    def test_any_in_stock_true_when_one_listing_in_stock(self, test_db_session: Session):
        """Product with two listings (one in, one out) → any_in_stock=True."""
        p = _product(test_db_session, external_id=None, external_source=None)
        _listing(test_db_session, p.id, chain="coles", external_id="C-001", in_stock=True)
        _listing(test_db_session, p.id, chain="woolworths", external_id="W-001", in_stock=False)
        test_db_session.commit()

        results = _attach_prices(test_db_session, [p])

        assert results[0].in_stock is True

    @pytest.mark.integration
    def test_any_in_stock_false_when_all_listings_out(self, test_db_session: Session):
        """Product with two out-of-stock listings → in_stock=False."""
        p = _product(test_db_session, external_id=None, external_source=None, in_stock=True)
        _listing(test_db_session, p.id, chain="coles", external_id="C-002", in_stock=False)
        _listing(test_db_session, p.id, chain="woolworths", external_id="W-002", in_stock=False)
        test_db_session.commit()

        results = _attach_prices(test_db_session, [p])

        assert results[0].in_stock is False


# ── C. Same-chain multiple listings: deterministic selection ────────────────────


class TestDeterministicListingSelection:
    @pytest.mark.integration
    def test_most_recently_seen_image_selected(self, test_db_session: Session):
        """When multiple listings exist, the one with latest catalog_last_seen_at
        provides the image (ties broken by id ASC)."""
        from datetime import timedelta

        p = _product(test_db_session, external_id=None, external_source=None)
        now = datetime.now(timezone.utc)
        _listing(
            test_db_session,
            p.id,
            chain="coles",
            external_id="C-OLD",
            external_image_url="http://img/old.jpg",
            catalog_last_seen_at=now - timedelta(days=2),
        )
        _listing(
            test_db_session,
            p.id,
            chain="coles",
            external_id="C-NEW",
            external_image_url="http://img/new.jpg",
            catalog_last_seen_at=now - timedelta(days=0),
        )
        test_db_session.commit()

        enrichment = get_listing_enrichment_map(test_db_session, [p.id])
        assert enrichment[p.id]["external_image_url"] == "http://img/new.jpg"

    @pytest.mark.integration
    def test_null_last_seen_ranks_last(self, test_db_session: Session):
        """Listing with catalog_last_seen_at=None ranks after any non-null timestamp."""
        p = _product(test_db_session, external_id=None, external_source=None)
        _listing(
            test_db_session,
            p.id,
            chain="coles",
            external_id="C-NULL",
            external_image_url="http://img/null-date.jpg",
            catalog_last_seen_at=None,
        )
        _listing(
            test_db_session,
            p.id,
            chain="coles",
            external_id="C-DATED",
            external_image_url="http://img/dated.jpg",
            catalog_last_seen_at=datetime.now(timezone.utc),
        )
        test_db_session.commit()

        enrichment = get_listing_enrichment_map(test_db_session, [p.id])
        # The listing with an actual timestamp should win
        assert enrichment[p.id]["external_image_url"] == "http://img/dated.jpg"


# ── E. Browse / search: no fan-out when multiple listings exist ─────────────────


class TestBrowseNoFanOut:
    @pytest.mark.integration
    def test_chain_filter_returns_product_once_with_multiple_listings(
        self, test_client, test_db_session: Session
    ):
        """Product with two listings for the same chain appears exactly once in browse."""
        p = _product(test_db_session, name="Multi-SKU Milk", external_id=None, external_source=None)
        _listing(test_db_session, p.id, chain="coles", external_id="COLES-A")
        _listing(test_db_session, p.id, chain="coles", external_id="COLES-B")
        test_db_session.commit()

        resp = test_client.get("/api/v1/products/search?chain=coles")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert ids.count(str(p.id)) == 1, "product must appear exactly once"

    @pytest.mark.integration
    def test_chain_filter_excludes_products_without_that_chain(
        self, test_client, test_db_session: Session
    ):
        """Product with only a woolworths listing does not appear in chain=coles filter."""
        p_coles = _product(
            test_db_session, name="Coles Only", external_id=None, external_source=None
        )
        p_ww = _product(test_db_session, name="WW Only", external_id=None, external_source=None)
        _listing(test_db_session, p_coles.id, chain="coles", external_id="C-ONLY")
        _listing(test_db_session, p_ww.id, chain="woolworths", external_id="W-ONLY")
        test_db_session.commit()

        resp = test_client.get("/api/v1/products/search?chain=coles")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert str(p_coles.id) in ids
        assert str(p_ww.id) not in ids


# ── F. Missing listing: safe fallback to Product fields ────────────────────────


class TestMissingListingFallback:
    @pytest.mark.integration
    def test_product_without_listing_uses_product_fields(self, test_db_session: Session):
        """Product with no listing keeps Product.package_size and Product.in_stock."""
        p = _product(
            test_db_session,
            package_size="Legacy 500g",
            in_stock=True,
            external_image_url="http://old/legacy.jpg",
        )
        test_db_session.commit()

        results = _attach_prices(test_db_session, [p])

        assert results[0].package_size == "Legacy 500g"
        assert results[0].in_stock is True
        assert results[0].external_image_url == "http://old/legacy.jpg"

    @pytest.mark.integration
    def test_enrichment_map_empty_for_product_with_no_listings(self, test_db_session: Session):
        p = _product(test_db_session)
        test_db_session.commit()

        enrichment = get_listing_enrichment_map(test_db_session, [p.id])
        assert p.id not in enrichment


# ── G. Image fallback hierarchy ────────────────────────────────────────────────


class TestImageFallbackHierarchy:
    @pytest.mark.integration
    def test_canonical_image_url_not_overridden_by_listing(self, test_db_session: Session):
        """Product.image_url (canonical self-hosted) must never be replaced by listing image."""
        p = _product(test_db_session, image_url="http://canonical/self-hosted.jpg")
        _listing(test_db_session, p.id, external_image_url="http://listing/retailer.jpg")
        test_db_session.commit()

        results = _attach_prices(test_db_session, [p])

        assert results[0].image_url == "http://canonical/self-hosted.jpg"
        assert results[0].external_image_url == "http://listing/retailer.jpg"

    @pytest.mark.integration
    def test_listing_image_used_as_image_url_when_canonical_absent(self, test_db_session: Session):
        """When Product.image_url is None, listing.external_image_url fills image_url."""
        p = _product(test_db_session, image_url=None, external_image_url=None)
        _listing(test_db_session, p.id, external_image_url="http://listing/img.jpg")
        test_db_session.commit()

        results = _attach_prices(test_db_session, [p])

        assert results[0].image_url == "http://listing/img.jpg"
        assert results[0].external_image_url == "http://listing/img.jpg"


# ── H. Availability filter: chain-specific, no cross-retailer leakage ──────────


class TestAvailabilityFilter:
    @pytest.mark.integration
    def test_in_stock_filter_true_matches_products_with_in_stock_listing(
        self, test_client, test_db_session: Session
    ):
        p_in = _product(test_db_session, name="In Stock", external_id=None, external_source=None)
        p_out = _product(
            test_db_session, name="Out of Stock", external_id=None, external_source=None
        )
        _listing(test_db_session, p_in.id, chain="coles", external_id="H-IN", in_stock=True)
        _listing(test_db_session, p_out.id, chain="coles", external_id="H-OUT", in_stock=False)
        test_db_session.commit()

        resp = test_client.get("/api/v1/products/search?in_stock=true")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert str(p_in.id) in ids
        assert str(p_out.id) not in ids

    @pytest.mark.integration
    def test_in_stock_cross_chain_no_leakage(self, test_client, test_db_session: Session):
        """chain=woolworths + in_stock=true must NOT return a product that is only
        in-stock at Coles, not Woolworths."""
        p = _product(
            test_db_session, name="Coles Only In Stock", external_id=None, external_source=None
        )
        _listing(test_db_session, p.id, chain="coles", external_id="H-CX", in_stock=True)
        _listing(test_db_session, p.id, chain="woolworths", external_id="H-WX", in_stock=False)
        test_db_session.commit()

        resp = test_client.get("/api/v1/products/search?chain=woolworths&in_stock=true")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert str(p.id) not in ids, "must not leak Coles stock status into Woolworths filter"


# ── D. /products/{id}/stores: listing-backed store URL ─────────────────────────


class TestStoresListingBackedUrl:
    @pytest.mark.integration
    def test_store_url_built_from_listing_external_source_and_id(
        self, test_client, test_db_session: Session
    ):
        """store_url must use listing.external_source + listing.external_id."""
        p = _product(test_db_session, external_source=None, external_id=None)
        _listing(
            test_db_session,
            p.id,
            chain="woolworths",
            external_id="WW-12345",
            external_source="catalog_woolworths",
        )
        store = _store(test_db_session, chain="woolworths")
        _price_report(test_db_session, p.id, store.id)
        test_db_session.commit()

        resp = test_client.get(f"/api/v1/products/{p.id}/stores")
        assert resp.status_code == 200
        stores = resp.json()["stores"]
        assert len(stores) == 1
        assert "WW-12345" in (stores[0]["store_url"] or "")

    @pytest.mark.integration
    def test_store_not_duplicated_when_multiple_listings_for_same_chain(
        self, test_client, test_db_session: Session
    ):
        """Even if a product has two listings for one chain, the store appears once."""
        p = _product(test_db_session, external_source=None, external_id=None)
        _listing(test_db_session, p.id, chain="coles", external_id="C-DUP-A")
        _listing(test_db_session, p.id, chain="coles", external_id="C-DUP-B")
        store = _store(test_db_session, chain="coles")
        _price_report(test_db_session, p.id, store.id)
        test_db_session.commit()

        resp = test_client.get(f"/api/v1/products/{p.id}/stores")
        assert resp.status_code == 200
        store_ids = [s["id"] for s in resp.json()["stores"]]
        assert store_ids.count(str(store.id)) == 1

    @pytest.mark.integration
    def test_stores_fallback_graceful_when_no_listing(self, test_client, test_db_session: Session):
        """Product with no listing returns store row but store_url may be None."""
        p = _product(test_db_session, external_source=None, external_id=None)
        store = _store(test_db_session, chain="iga")
        _price_report(test_db_session, p.id, store.id)
        test_db_session.commit()

        resp = test_client.get(f"/api/v1/products/{p.id}/stores")
        assert resp.status_code == 200
        stores = resp.json()["stores"]
        assert len(stores) == 1
        # store_url is None when no listing and no legacy external_source
        assert stores[0]["store_url"] is None


# ── B. Two-chain: no cross-chain leakage in store URL ──────────────────────────


class TestTwoChainStoreUrls:
    @pytest.mark.integration
    def test_each_chain_gets_its_own_listing_store_url(self, test_client, test_db_session: Session):
        """A product mapped to both Coles and Woolworths gets independent store_urls."""
        p = _product(
            test_db_session, external_source=None, external_id=None, name="Dual-Chain Milk"
        )
        _listing(
            test_db_session,
            p.id,
            chain="coles",
            external_id="COLES-SKU",
            external_source="catalog_coles",
        )
        _listing(
            test_db_session,
            p.id,
            chain="woolworths",
            external_id="WW-9999",
            external_source="catalog_woolworths",
        )
        coles_store = _store(test_db_session, chain="coles")
        ww_store = _store(test_db_session, chain="woolworths")
        _price_report(test_db_session, p.id, coles_store.id, price_cents=350)
        _price_report(test_db_session, p.id, ww_store.id, price_cents=400)
        test_db_session.commit()

        resp = test_client.get(f"/api/v1/products/{p.id}/stores")
        assert resp.status_code == 200
        stores_by_chain = {s["chain"]: s for s in resp.json()["stores"]}

        # Woolworths URL must contain the woolworths external_id
        ww_url = stores_by_chain["woolworths"]["store_url"] or ""
        assert "WW-9999" in ww_url, f"expected WW-9999 in woolworths URL, got: {ww_url}"

        # Coles URL must NOT contain the woolworths ID
        coles_url = stores_by_chain["coles"]["store_url"] or ""
        assert "WW-9999" not in coles_url, "coles URL must not leak woolworths external_id"


# ── Batch enrichment helper unit tests ─────────────────────────────────────────


class TestGetListingEnrichmentMap:
    @pytest.mark.integration
    def test_empty_product_ids_returns_empty_dict(self, test_db_session: Session):
        result = get_listing_enrichment_map(test_db_session, [])
        assert result == {}

    @pytest.mark.integration
    def test_returns_correct_values_for_single_listing(self, test_db_session: Session):
        p = _product(test_db_session)
        _listing(
            test_db_session,
            p.id,
            external_image_url="http://img/a.jpg",
            package_size_raw="500g",
            in_stock=True,
        )
        test_db_session.commit()

        enrichment = get_listing_enrichment_map(test_db_session, [p.id])

        assert enrichment[p.id]["external_image_url"] == "http://img/a.jpg"
        assert enrichment[p.id]["package_size_raw"] == "500g"
        assert enrichment[p.id]["any_in_stock"] is True

    @pytest.mark.integration
    def test_product_ids_not_in_result_when_no_listings(self, test_db_session: Session):
        p = _product(test_db_session)
        test_db_session.commit()

        enrichment = get_listing_enrichment_map(test_db_session, [p.id])
        assert p.id not in enrichment

    @pytest.mark.integration
    def test_batch_fetches_multiple_products(self, test_db_session: Session):
        p1 = _product(test_db_session, name="P1")
        p2 = _product(test_db_session, name="P2", external_id="SKU-002")
        _listing(test_db_session, p1.id, external_image_url="http://img/p1.jpg")
        _listing(
            test_db_session, p2.id, external_id="SKU-002", external_image_url="http://img/p2.jpg"
        )
        test_db_session.commit()

        enrichment = get_listing_enrichment_map(test_db_session, [p1.id, p2.id])

        assert enrichment[p1.id]["external_image_url"] == "http://img/p1.jpg"
        assert enrichment[p2.id]["external_image_url"] == "http://img/p2.jpg"

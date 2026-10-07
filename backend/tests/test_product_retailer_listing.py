"""
Tests for the ProductRetailerListing schema (Story #49a).

Covers:
1. listing can be created for a Product
2. two chains can share the same external_id
3. same (chain, external_id) cannot be inserted twice (unique constraint)
4. one Product can have multiple listings from the same chain with different external_ids
5. deleting the canonical Product cascades to its listings
6. nullable optional fields can be left unset
"""

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Product, ProductRetailerListing

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_product(db: Session, name: str = "Test Product") -> Product:
    product = Product(id=uuid4(), name=name)
    db.add(product)
    db.flush()
    return product


def _make_listing(
    db: Session,
    product: Product,
    chain: str,
    external_id: str,
    **kwargs,
) -> ProductRetailerListing:
    listing = ProductRetailerListing(
        product_id=product.id,
        chain=chain,
        external_id=external_id,
        **kwargs,
    )
    db.add(listing)
    db.flush()
    return listing


# ── tests ──────────────────────────────────────────────────────────────────────


class TestProductRetailerListingSchema:
    def test_listing_can_be_created_for_product(self, test_db_session: Session):
        product = _make_product(test_db_session)
        listing = _make_listing(
            test_db_session,
            product,
            chain="coles",
            external_id="SKU-001",
            external_name="Coles Full Cream Milk 2L",
        )
        test_db_session.commit()

        fetched = test_db_session.get(ProductRetailerListing, listing.id)
        assert fetched is not None
        assert fetched.product_id == product.id
        assert fetched.chain == "coles"
        assert fetched.external_id == "SKU-001"
        assert fetched.external_name == "Coles Full Cream Milk 2L"

    def test_two_chains_can_share_same_external_id(self, test_db_session: Session):
        product = _make_product(test_db_session)
        l1 = _make_listing(test_db_session, product, chain="coles", external_id="12345")
        l2 = _make_listing(test_db_session, product, chain="woolworths", external_id="12345")
        test_db_session.commit()

        assert l1.id != l2.id
        assert l1.chain == "coles"
        assert l2.chain == "woolworths"
        assert l1.external_id == l2.external_id

    def test_same_chain_and_external_id_cannot_insert_twice(self, test_db_session: Session):
        product = _make_product(test_db_session)
        _make_listing(test_db_session, product, chain="coles", external_id="DUP-001")

        duplicate = ProductRetailerListing(
            product_id=product.id,
            chain="coles",
            external_id="DUP-001",
        )
        test_db_session.add(duplicate)
        with pytest.raises(IntegrityError):
            test_db_session.flush()

    def test_one_product_multiple_listings_same_chain(self, test_db_session: Session):
        product = _make_product(test_db_session)
        l1 = _make_listing(test_db_session, product, chain="iga", external_id="IGA-A")
        l2 = _make_listing(test_db_session, product, chain="iga", external_id="IGA-B")
        test_db_session.commit()

        assert l1.product_id == product.id
        assert l2.product_id == product.id
        assert l1.chain == l2.chain == "iga"
        assert l1.external_id != l2.external_id

    def test_deleting_canonical_product_cascades_to_listings(self, test_db_session: Session):
        product = _make_product(test_db_session)
        listing = _make_listing(test_db_session, product, chain="aldi", external_id="ALDI-999")
        test_db_session.commit()
        listing_id = listing.id

        test_db_session.delete(product)
        test_db_session.commit()

        gone = test_db_session.get(ProductRetailerListing, listing_id)
        assert gone is None

    def test_nullable_optional_fields_can_be_omitted(self, test_db_session: Session):
        product = _make_product(test_db_session)
        listing = ProductRetailerListing(
            product_id=product.id,
            chain="drakes",
            external_id="DRAKES-001",
        )
        test_db_session.add(listing)
        test_db_session.commit()
        test_db_session.refresh(listing)

        assert listing.external_name is None
        assert listing.external_brand is None
        assert listing.external_category is None
        assert listing.catalog_last_seen_at is None
        assert listing.external_image_url is None
        assert listing.in_stock is True
        assert listing.is_online_only is False

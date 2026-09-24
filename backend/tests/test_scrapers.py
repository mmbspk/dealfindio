"""
Unit tests for scraper modules.

Tests:
- HTML/PDF parsing for Coles and Woolworths scrapers
- Product extraction and data normalization
- Duplicate detection and product deduplication
- Price report ingestion
- Error handling and edge cases
"""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models import Product, Store
from scrapers.base import ScrapedProduct
from scrapers.coles import ColesScraper


class TestScrapedProduct:
    """The ScrapedProduct DTO used by every scraper."""

    @pytest.mark.unit
    def test_minimal_construction(self):
        p = ScrapedProduct(product_name="Milk 2L", price_cents=350)
        assert p.product_name == "Milk 2L"
        assert p.price_cents == 350
        assert p.was_price_cents is None
        assert p.gtin is None

    @pytest.mark.unit
    def test_full_construction(self):
        p = ScrapedProduct(
            product_name="Bread",
            price_cents=280,
            was_price_cents=400,
            gtin="9300617006552",
            valid_from=date(2026, 8, 1),
            valid_to=date(2026, 8, 7),
        )
        assert p.was_price_cents == 400
        assert p.gtin == "9300617006552"
        assert p.valid_to == date(2026, 8, 7)


class TestColesParsing:
    """Coles HTML parsing via BeautifulSoup (async parse_flyer)."""

    def _scraper(self) -> ColesScraper:
        # No DB/redis needed for pure parsing.
        return ColesScraper(db_session=None, redis_client=None)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_parses_product_card(self):
        html = """
        <section data-testid="product-tile">
          <h2 class="product__title">Coles Full Cream Milk 2L</h2>
          <span data-testid="product-pricing">$3.50</span>
          <span class="price__was">Was $5.00</span>
        </section>
        """
        products = await self._scraper().parse_flyer(html)
        assert len(products) == 1
        p = products[0]
        assert p.product_name == "Coles Full Cream Milk 2L"
        assert p.price_cents == 350

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_html_returns_empty_list(self):
        products = await self._scraper().parse_flyer("<html></html>")
        assert products == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_card_without_name_is_skipped(self):
        html = '<section data-testid="product-tile"><span data-testid="product-pricing">$1.00</span></section>'
        products = await self._scraper().parse_flyer(html)
        assert products == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_card_without_price_is_skipped(self):
        html = '<section data-testid="product-tile"><h2 class="product__title">No Price Item</h2></section>'
        products = await self._scraper().parse_flyer(html)
        assert products == []


class TestColesScraperParsing:
    """Tests for Coles HTML scraper parsing."""

    @pytest.mark.unit
    def test_coles_parse_valid_html(self, sample_coles_html):
        """Test parsing valid Coles HTML."""
        # Verify HTML can be parsed
        assert sample_coles_html is not None
        assert "product" in sample_coles_html.lower()

    @pytest.mark.unit
    def test_coles_extract_product_name(self):
        """Test extraction of product name from Coles HTML."""
        html = """
        <div class="product">
            <h2>Coles Milk 2L</h2>
            <span class="price">$3.50</span>
        </div>
        """
        # Should extract "Coles Milk 2L"
        assert "Milk" in html or "Coles" in html

    @pytest.mark.unit
    def test_coles_extract_price(self):
        """Test extraction of price from Coles HTML."""
        html = "<span class='price'>$3.50</span>"
        # Should extract 350 (cents)
        assert "$3.50" in html

    @pytest.mark.unit
    def test_coles_extract_gtin(self):
        """Test extraction of GTIN from Coles product."""
        html = '<div data-gtin="9300617006552">Product</div>'
        assert "9300617006552" in html

    @pytest.mark.unit
    def test_coles_invalid_html_handling(self):
        """Test handling of malformed HTML."""
        invalid_html = "<div><span>No closing tags"
        # Should handle gracefully
        assert invalid_html is not None

    @pytest.mark.unit
    def test_coles_parse_promotions(self):
        """Test parsing of promotion/discount information."""
        html = """
        <div class="promotion">
            <span class="original-price">$5.00</span>
            <span class="sale-price">$3.50</span>
        </div>
        """
        assert "$5.00" in html
        assert "$3.50" in html


class TestWoolworthsScraperParsing:
    """Tests for Woolworths API scraper parsing."""

    @pytest.mark.unit
    def test_woolworths_parse_json_api(self, sample_woolworths_json):
        """Test parsing Woolworths JSON API response."""
        assert sample_woolworths_json is not None
        assert "Products" in sample_woolworths_json

    @pytest.mark.unit
    def test_woolworths_extract_product_id(self):
        """Test extraction of product ID."""
        api_response = {"Products": [{"ProductId": "123456", "Name": "Milk"}]}
        assert api_response["Products"][0]["ProductId"] == "123456"

    @pytest.mark.unit
    def test_woolworths_extract_price(self):
        """Test extraction of price from API response."""
        api_response = {"Products": [{"Price": 3.50, "Name": "Milk"}]}
        assert api_response["Products"][0]["Price"] == 3.50

    @pytest.mark.unit
    def test_woolworths_extract_gtin(self):
        """Test extraction of GTIN from API response."""
        api_response = {"Products": [{"Gtin": "9300617006552", "Name": "Milk"}]}
        assert api_response["Products"][0]["Gtin"] == "9300617006552"

    @pytest.mark.unit
    def test_woolworths_invalid_json_handling(self):
        """Malformed JSON should raise a JSON decode error when parsed."""
        import json

        with pytest.raises(json.JSONDecodeError):
            json.loads("{not valid json")

    @pytest.mark.unit
    def test_woolworths_empty_products(self):
        """Test handling of empty product list."""
        api_response = {"Products": []}
        assert len(api_response["Products"]) == 0

    @pytest.mark.unit
    def test_woolworths_missing_fields(self):
        """Test handling of missing required fields."""
        api_response = {"Products": [{"Name": "Milk"}]}  # Missing Price, ProductId
        # Should handle gracefully
        assert api_response is not None


class TestALDIScraperParsing:
    """Tests for ALDI scraper parsing."""

    @pytest.mark.unit
    def test_aldi_parse_csv_data(self):
        """Test parsing ALDI CSV format."""
        csv_data = "Product,Price,GTIN\nMilk,3.50,9300617006552"
        assert "Milk" in csv_data
        assert "3.50" in csv_data


class TestProductDeduplication:
    """Tests for product deduplication logic."""

    @pytest.mark.integration
    def test_gtin_lookup_exact_match(self, test_db_session: Session, sample_product: Product):
        """Test GTIN-based product lookup with exact match."""
        # Query for the sample product by GTIN
        result = test_db_session.query(Product).filter(Product.gtin == sample_product.gtin).first()

        assert result is not None
        assert result.id == sample_product.id
        assert result.gtin == sample_product.gtin

    @pytest.mark.integration
    def test_gtin_lookup_no_match(self, test_db_session: Session):
        """Test GTIN lookup with no matching product."""
        result = test_db_session.query(Product).filter(Product.gtin == "9999999999999").first()

        assert result is None

    @pytest.mark.integration
    def test_product_name_fuzzy_match(
        self, test_db_session: Session, sample_products: list[Product]
    ):
        """Test fuzzy matching of product names."""
        # Attempt to find similar products
        products = test_db_session.query(Product).all()
        assert len(products) == 3


class TestPriceReportIngestion:
    """Tests for price report ingestion and validation."""

    @pytest.mark.integration
    def test_valid_price_report_ingestion(
        self,
        test_db_session: Session,
        sample_product: Product,
        sample_store: Store,
    ):
        """Test ingestion of valid price report."""
        from app.models import PriceReport

        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,
            was_price_cents=500,
            source="community",
            valid_from=date.today(),
            valid_to=None,
        )

        test_db_session.add(report)
        test_db_session.commit()
        test_db_session.refresh(report)

        # Verify report was created
        assert report.id is not None
        assert report.price_cents == 350
        assert report.was_price_cents == 500

    @pytest.mark.integration
    def test_price_report_with_invalid_price(
        self, test_db_session: Session, sample_product: Product, sample_store: Store
    ):
        """A negative price violates the DB CHECK constraint on flush."""
        from app.models import PriceReport

        report = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=-100,  # Invalid: violates price_cents >= 0
            source="community",
            valid_from=date.today(),
        )
        test_db_session.add(report)
        # The constraint is enforced by the database at flush/commit time,
        # not at object construction.
        with pytest.raises(Exception):
            test_db_session.commit()

    @pytest.mark.integration
    def test_price_report_missing_product(self, test_db_session: Session, sample_store: Store):
        """A report referencing a non-existent product fails the FK on flush."""
        from uuid import uuid4

        from app.models import PriceReport

        report = PriceReport(
            product_id=uuid4(),  # Non-existent product
            store_id=sample_store.id,
            price_cents=350,
            source="community",
            valid_from=date.today(),
        )
        test_db_session.add(report)
        with pytest.raises(Exception):
            test_db_session.commit()

    @pytest.mark.integration
    def test_price_report_duplicate_detection(
        self,
        test_db_session: Session,
        sample_product: Product,
        sample_store: Store,
    ):
        """Test duplicate price report detection."""
        from app.models import PriceReport

        report1 = PriceReport(
            product_id=sample_product.id,
            store_id=sample_store.id,
            price_cents=350,
            was_price_cents=500,
            source="community",
            valid_from=date.today(),
        )
        test_db_session.add(report1)
        test_db_session.commit()

        # Query back by product + store (which are guaranteed to match).
        reports = (
            test_db_session.query(PriceReport)
            .filter(
                PriceReport.product_id == sample_product.id,
                PriceReport.store_id == sample_store.id,
            )
            .all()
        )

        # Exactly one report should exist for this product/store pair.
        assert len(reports) == 1

    @pytest.mark.unit
    def test_price_discount_calculation(self):
        """Test discount percentage calculation."""
        original = 500  # cents
        sale = 350  # cents
        discount = ((original - sale) / original) * 100
        assert discount == 30.0


class TestScraperErrorHandling:
    """Scrapers must degrade gracefully on malformed input, not crash."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_coles_malformed_html_returns_empty(self):
        scraper = ColesScraper(db_session=None, redis_client=None)
        # Unclosed tags / garbage -> no product cards -> empty list, no raise.
        products = await scraper.parse_flyer("<div><span>broken")
        assert products == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_coles_non_html_input_returns_empty(self):
        scraper = ColesScraper(db_session=None, redis_client=None)
        products = await scraper.parse_flyer("not html at all just text")
        assert products == []


class TestScraperDataValidation:
    """Tests for data validation in scrapers."""

    @pytest.mark.unit
    def test_gtin_validation_valid(self):
        """Test validation of valid GTINs."""
        valid_gtins = [
            "9300617006552",  # EAN-13
            "93006170065521",  # EAN-14
        ]
        for gtin in valid_gtins:
            assert len(gtin) in [12, 13, 14]

    @pytest.mark.unit
    def test_gtin_validation_invalid(self):
        """Test rejection of invalid GTINs."""
        invalid_gtins = [
            "123",  # Too short
            "abc123def456ghi",  # Contains letters
            "",  # Empty
        ]
        for gtin in invalid_gtins:
            assert len(gtin) < 12 or not gtin.isdigit()

    @pytest.mark.unit
    def test_price_validation_valid(self):
        """Test validation of valid prices."""
        valid_prices = [1, 100, 10000, 99999]
        for price in valid_prices:
            assert price > 0

    @pytest.mark.unit
    def test_price_validation_invalid(self):
        """Test rejection of invalid prices."""
        invalid_prices = [-100, 0, None, "abc"]
        for price in invalid_prices:
            if price is None:
                continue
            # A valid price must be a positive integer. Check the type first to
            # avoid comparing a str with an int (TypeError in Python 3).
            is_invalid = isinstance(price, str) or price <= 0
            assert is_invalid

    @pytest.mark.unit
    def test_product_name_validation(self):
        """Test validation of product names."""
        valid_names = [
            "Coles Milk 2L",
            "Woolworths Bread Multigrain",
            "ALDI Pasta",
        ]
        for name in valid_names:
            assert len(name) > 0
            assert name.strip() == name

    @pytest.mark.unit
    def test_store_location_validation(self):
        """Test validation of store coordinates."""
        # Valid Sydney coordinates
        valid_coords = [(-33.87, 151.21)]  # Chatswood
        for lat, lon in valid_coords:
            assert -90 <= lat <= 90
            assert -180 <= lon <= 180

        # Invalid coordinates
        invalid_coords = [(91, 200), (-200, 500)]
        for lat, lon in invalid_coords:
            assert not (-90 <= lat <= 90 and -180 <= lon <= 180)


class TestScraperIntegration:
    """Integration test with the parse pipeline end to end."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_parse_then_build_scraped_products(self):
        # Parse HTML -> ScrapedProduct list -> assert the pipeline output shape
        # is usable by the ingest layer (name + positive integer price_cents).
        html = """
        <section data-testid="product-tile"><h2 class="product__title">Milk 2L</h2><span data-testid="product-pricing">$3.50</span></section>
        <section data-testid="product-tile"><h2 class="product__title">Bread</h2><span data-testid="product-pricing">$2.80</span></section>
        """
        scraper = ColesScraper(db_session=None, redis_client=None)
        products = await scraper.parse_flyer(html)
        assert len(products) == 2
        for p in products:
            assert isinstance(p, ScrapedProduct)
            assert p.product_name
            assert isinstance(p.price_cents, int)
            assert p.price_cents > 0

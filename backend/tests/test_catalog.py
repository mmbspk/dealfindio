"""
Tests for Feature B — authenticated full-catalog scraper.

Covers:
- HumanLikeRateLimiter: burst allowance, backoff on 429.
- catalog_registry: factory + config assembly + unknown-chain error.
- CatalogScraper base: delta fingerprint change detection, run_full/run_delta
  orchestration and 401 re-auth, using a fake in-memory scraper.
- catalog_coles: BFF item parsing (price, promo, image, sold-out).
- catalog_ingest: create vs update, price-report creation, image no-clobber.
- run_catalog_crawl task: end to end against a fake scraper + real DB.
"""

import asyncio
from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import fakeredis
import pytest

from app.models import CatalogCrawlRun, PriceReport, Product
from scrapers.catalog_base import (
    AuthExpiredError,
    CatalogProduct,
    CatalogScraper,
    CatalogStoreConfig,
    CategoryNode,
    HumanLikeRateLimiter,
)

# ── HumanLikeRateLimiter ─────────────────────────────────────────────────────


class TestHumanLikeRateLimiter:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_burst_allows_fast_initial_requests(self):
        # With burst=3 and a large delay, the first 3 waits should not block.
        rl = HumanLikeRateLimiter(min_delay_s=100, max_delay_s=100, burst_limit=3)
        loop = asyncio.get_event_loop()
        start = loop.time()
        await rl.wait()
        await rl.wait()
        await rl.wait()
        assert loop.time() - start < 1.0  # all three consumed burst tokens

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_on_rate_limited_sets_penalty_and_drains_burst(self):
        rl = HumanLikeRateLimiter(min_delay_s=0, max_delay_s=0, burst_limit=5)
        rl.on_rate_limited(retry_after_s=42.0)
        assert rl._tokens == 0
        assert rl._penalty_until >= asyncio.get_event_loop().time() + 41.0


# ── Registry ─────────────────────────────────────────────────────────────────


class TestCatalogRegistry:
    @pytest.mark.unit
    def test_available_chains_includes_coles(self):
        from scrapers.catalog_registry import available_chains

        assert "coles" in available_chains()

    @pytest.mark.unit
    def test_get_scraper_builds_coles(self):
        from scrapers.catalog_coles import ColesCatalogScraper
        from scrapers.catalog_registry import get_catalog_scraper

        s = get_catalog_scraper("coles")
        assert isinstance(s, ColesCatalogScraper)
        assert s.chain == "coles"
        assert s.config.min_delay_s > 0

    @pytest.mark.unit
    def test_unknown_chain_raises(self):
        from scrapers.catalog_registry import get_catalog_scraper

        with pytest.raises(ValueError):
            get_catalog_scraper("bunnings")

    @pytest.mark.unit
    def test_build_store_config_reads_settings(self):
        from scrapers.catalog_registry import build_store_config

        settings = MagicMock()
        settings.COLES_EMAIL = "a@b.com"
        settings.COLES_PASSWORD = "secret"
        settings.CATALOG_MIN_DELAY_S = 1.0
        settings.CATALOG_MAX_DELAY_S = 2.0
        settings.CATALOG_BURST_LIMIT = 3
        settings.CATALOG_PAGE_SIZE = 24
        settings.CATALOG_MAX_PAGES_PER_CATEGORY = 10
        settings.CATALOG_COLES_ENABLED = True
        cfg = build_store_config("coles", settings)
        assert cfg.email == "a@b.com"
        assert cfg.password == "secret"
        assert cfg.page_size == 24
        assert cfg.enabled is True


# ── Fake scraper for base-class orchestration tests ──────────────────────────


class _FakeScraper(CatalogScraper):
    """In-memory catalog scraper: two categories, fixed products, no network."""

    def __init__(self, *args, fail_first_crawl_401=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_calls = 0
        self.crawl_calls = 0
        self._fail_first_401 = fail_first_crawl_401

    async def authenticate(self) -> dict:
        self.auth_calls += 1
        return {"headers": {"x": "1"}, "token": f"tok{self.auth_calls}"}

    async def discover_categories(self):
        return [
            CategoryNode(id="dairy", name="Dairy"),
            CategoryNode(id="bakery", name="Bakery"),
        ]

    async def crawl_category(self, category, auth, *, page_sink=None, start_page: int = 1):
        self.crawl_calls += 1
        if self._fail_first_401 and self.crawl_calls == 1:
            raise AuthExpiredError("401")
        return {"category": category.id, "items": [{"n": category.id}]}

    async def parse_products(self, raw, category):
        return [CatalogProduct(product_name=f"{category.name} Item", price_cents=199)]


def _fake_config():
    return CatalogStoreConfig(
        chain="coles",
        min_delay_s=0,
        max_delay_s=0,
        burst_limit=10,
        page_size=48,
        max_pages_per_category=5,
        enabled=True,
    )


class TestCatalogScraperBase:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_full_crawls_all_categories(self):
        r = fakeredis.FakeStrictRedis()
        scraper = _FakeScraper("coles", _fake_config(), redis_client=r)
        summary = await scraper.run_full()
        assert summary["categories_found"] == 2
        assert summary["categories_crawled"] == 2
        assert summary["products_found"] == 2
        assert summary["status"] == "success"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_full_invokes_product_sink(self):
        r = fakeredis.FakeStrictRedis()
        scraper = _FakeScraper("coles", _fake_config(), redis_client=r)
        seen = []

        async def sink(products, category):
            seen.append((category.id, len(products)))

        await scraper.run_full(product_sink=sink)
        assert len(seen) == 2
        assert all(count == 1 for _cat, count in seen)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_delta_skips_unchanged_categories(self):
        r = fakeredis.FakeStrictRedis()
        scraper = _FakeScraper("coles", _fake_config(), redis_client=r)
        # First full crawl records fingerprints.
        await scraper.run_full()
        # A delta right after (same fixed data) should skip both categories.
        summary = await scraper.run_delta()
        assert summary["categories_skipped"] == 2
        assert summary["categories_crawled"] == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_reauth_on_401(self):
        r = fakeredis.FakeStrictRedis()
        scraper = _FakeScraper("coles", _fake_config(), redis_client=r, fail_first_crawl_401=True)
        summary = await scraper.run_full()
        # The first crawl raised 401 -> re-auth -> retried; both cats still done.
        assert scraper.auth_calls >= 2
        assert summary["categories_crawled"] == 2

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_auth_cached_in_redis(self):
        r = fakeredis.FakeStrictRedis()
        scraper = _FakeScraper("coles", _fake_config(), redis_client=r)
        await scraper._get_or_refresh_auth()
        await scraper._get_or_refresh_auth()
        # Second call hits the Redis cache, so authenticate ran only once.
        assert scraper.auth_calls == 1

    @pytest.mark.unit
    def test_fingerprint_stable_and_sensitive(self):
        a = CatalogScraper._fingerprint({"x": 1, "y": 2})
        b = CatalogScraper._fingerprint({"y": 2, "x": 1})  # key order irrelevant
        c = CatalogScraper._fingerprint({"x": 1, "y": 3})
        assert a == b
        assert a != c


# ── Coles parsing ────────────────────────────────────────────────────────────


class TestColesCatalogParsing:
    @pytest.mark.unit
    def test_parse_item_full(self):
        from scrapers.catalog_coles import _parse_item

        cat = CategoryNode(id="dairy", name="Dairy")
        item = {
            "_type": "PRODUCT",
            "name": "Milk 2L",
            "brand": "Coles",
            "id": "77",
            "pricing": {"now": 3.5, "was": 5.0},
            "imageUris": [{"uri": "http://img/x.jpg"}],
        }
        p = _parse_item(item, cat, date.today())
        assert p.product_name == "Milk 2L"
        assert p.price_cents == 350
        assert p.was_price_cents == 500
        assert p.image_url == "http://img/x.jpg"
        assert p.brand == "Coles"
        assert p.external_id == "77"
        assert p.category == "Dairy"

    @pytest.mark.unit
    def test_parse_item_skips_non_product(self):
        from scrapers.catalog_coles import _parse_item

        cat = CategoryNode(id="dairy", name="Dairy")
        assert _parse_item({"_type": "AD", "name": "Ad"}, cat, date.today()) is None

    @pytest.mark.unit
    def test_parse_item_drops_bogus_was(self):
        from scrapers.catalog_coles import _parse_item

        cat = CategoryNode(id="dairy", name="Dairy")
        # was <= now should not be treated as a promo.
        item = {"name": "X", "id": "1", "pricing": {"now": 5.0, "was": 5.0}}
        p = _parse_item(item, cat, date.today())
        assert p.was_price_cents is None

    @pytest.mark.unit
    def test_parse_item_missing_price_returns_none(self):
        from scrapers.catalog_coles import _parse_item

        cat = CategoryNode(id="dairy", name="Dairy")
        assert _parse_item({"name": "X", "pricing": {}}, cat, date.today()) is None

    @pytest.mark.unit
    def test_extract_results_shapes(self):
        from scrapers.catalog_coles import _extract_results

        # Real Coles shape: products under pageProps.searchResults.results.
        assert _extract_results({"pageProps": {"searchResults": {"results": [1, 2]}}}) == [1, 2]
        # Legacy fallback: pageProps.results.
        assert _extract_results({"pageProps": {"results": [3]}}) == [3]
        # Fallback: a bare top-level results list.
        assert _extract_results({"results": [4]}) == [4]
        # No products / bad input -> empty.
        assert _extract_results({"pageProps": {}}) == []
        assert _extract_results({}) == []
        assert _extract_results("nope") == []

    @pytest.mark.unit
    def test_parse_item_l2_category_tagging(self):
        from scrapers.catalog_coles import _parse_item

        cat = CategoryNode(id="dairy-eggs-fridge", name="Dairy, Eggs & Fridge")
        l2_map = {"dairy": "Dairy", "eggs": "Eggs", "cheese": "Cheese"}
        item = {
            "_type": "PRODUCT",
            "name": "Cheddar 500g",
            "id": "123",
            "pricing": {"now": 6.0},
            "categories": [{"seoToken": "cheese"}],
        }
        p = _parse_item(item, cat, date.today(), l2_map)
        assert p is not None
        assert p.category == "Dairy, Eggs & Fridge > Cheese"

    @pytest.mark.unit
    def test_parse_item_l2_fallback_to_l1_when_no_match(self):
        from scrapers.catalog_coles import _parse_item

        cat = CategoryNode(id="dairy-eggs-fridge", name="Dairy, Eggs & Fridge")
        l2_map = {"cheese": "Cheese"}
        item = {
            "_type": "PRODUCT",
            "name": "Mystery Item",
            "id": "999",
            "pricing": {"now": 2.0},
            "categories": [{"seoToken": "unknown-token"}],
        }
        p = _parse_item(item, cat, date.today(), l2_map)
        assert p is not None
        assert p.category == "Dairy, Eggs & Fridge"  # falls back to L1


class TestColesPackageSize:
    """Unit tests for _extract_coles_package_size — covers all three priority paths."""

    def _fn(self, item):
        from scrapers.catalog_coles import _extract_coles_package_size

        return _extract_coles_package_size(item)

    @pytest.mark.unit
    def test_dedicated_size_field_wins(self):
        item = {
            "name": "Farmhouse Gold Full Cream Milk",
            "description": "PAULS FARMHOUSE GOLD FULL CREAM MILK 1.5 LITRE",
            "size": "1.5L",
        }
        assert self._fn(item) == "1.5L"

    @pytest.mark.unit
    def test_dedicated_size_various_units(self):
        for size in ("350g", "180g", "420g", "375mL", "2kg"):
            item = {"name": "Product", "size": size}
            assert self._fn(item) == size

    @pytest.mark.unit
    def test_description_fallback_when_no_size_field(self):
        item = {
            "name": "Oat Milk",
            "description": "MILKLAB OAT MILK 250ML",
        }
        assert self._fn(item) == "250ML"

    @pytest.mark.unit
    def test_description_litre_normalised(self):
        item = {
            "name": "Full Cream Milk",
            "description": "PAULS FULL CREAM MILK 1.5 LITRE",
        }
        # "1.5 LITRE" normalises to "1.5 L" — space is preserved from the original
        assert self._fn(item) == "1.5 L"

    @pytest.mark.unit
    def test_name_fallback_when_neither_size_nor_description(self):
        item = {"name": "Coca-Cola No Sugar 375mL 24 Pack"}
        assert self._fn(item) == "375mL 24 Pack"

    @pytest.mark.unit
    def test_name_inline_size(self):
        item = {"name": "Milk 2L"}
        assert self._fn(item) == "2L"

    @pytest.mark.unit
    def test_no_size_returns_none(self):
        item = {"name": "Milk", "description": "COLES FRESH MILK"}
        assert self._fn(item) is None

    @pytest.mark.unit
    def test_empty_size_field_falls_through(self):
        item = {
            "name": "Oat Milk",
            "description": "MILKLAB OAT MILK 1L",
            "size": "",
        }
        assert self._fn(item) == "1L"

    @pytest.mark.unit
    def test_no_regression_coles_kitchen_product(self):
        # From live API: size field is "350g", description is the full uppercase title
        item = {
            "name": "Special Fried Rice",
            "description": "COLES KITCHEN SPECIAL FRIED RICE 350G",
            "size": "350g",
        }
        assert self._fn(item) == "350g"


# ── catalog_ingest ───────────────────────────────────────────────────────────


class TestCatalogIngest:
    @pytest.mark.integration
    def test_creates_new_product_and_price_report(self, test_db_session):
        from app.services.catalog_ingest import ingest_catalog_products

        cp = CatalogProduct(
            product_name="Fresh Catalog Milk 2L",
            price_cents=350,
            was_price_cents=500,
            image_url="http://img/m.jpg",
            brand="Coles",
            category="Dairy",
            external_id="c1",
            valid_from=date.today(),
        )
        counts = ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        assert counts.created == 1
        assert counts.price_reports == 1
        product = test_db_session.query(Product).filter_by(name="Fresh Catalog Milk 2L").one()
        # Scraped CDN URL lands in external_image_url; image_url stays NULL for enricher.
        assert product.external_image_url == "http://img/m.jpg"
        assert product.image_url is None
        assert product.image_source is None
        assert product.catalog_last_seen_at is not None
        report = test_db_session.query(PriceReport).filter_by(product_id=product.id).one()
        assert report.source == "catalog_coles"
        assert report.is_on_sale is True

    @pytest.mark.integration
    def test_updates_existing_without_clobbering_image(self, test_db_session):
        from app.services.catalog_ingest import ingest_catalog_products

        existing = Product(
            id=uuid4(),
            name="Existing Widget 1kg",
            gtin=None,
            image_url="http://existing/keep.jpg",
            image_source="open_food_facts",
        )
        test_db_session.add(existing)
        test_db_session.commit()

        cp = CatalogProduct(
            product_name="Existing Widget 1kg",
            price_cents=1000,
            image_url="http://catalog/new.jpg",
            valid_from=date.today(),
        )
        counts = ingest_catalog_products([cp], "coles", test_db_session)
        test_db_session.commit()

        assert counts.created == 0
        assert counts.updated == 1
        refreshed = test_db_session.get(Product, existing.id)
        # Existing OFF image_url must NOT be overwritten; catalog URL goes to external_image_url.
        assert refreshed.image_url == "http://existing/keep.jpg"
        assert refreshed.external_image_url == "http://catalog/new.jpg"
        assert refreshed.catalog_last_seen_at is not None

    @pytest.mark.integration
    def test_empty_batch_is_noop(self, test_db_session):
        from app.services.catalog_ingest import ingest_catalog_products

        counts = ingest_catalog_products([], "coles", test_db_session)
        assert counts.created == 0
        assert counts.price_reports == 0


# ── run_catalog_crawl task ───────────────────────────────────────────────────


class TestRunCatalogCrawlTask:
    def _fn(self):
        from app.workers.catalog_tasks import run_catalog_crawl

        return run_catalog_crawl

    @pytest.mark.integration
    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            self._fn().run("coles", "sideways")

    @pytest.mark.integration
    def test_full_crawl_ingests_and_records_run(self, test_db_session):
        from app.workers import catalog_tasks

        fake_redis = fakeredis.FakeStrictRedis()
        fake_scraper = _FakeScraper("coles", _fake_config(), redis_client=fake_redis)

        with patch.object(
            catalog_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(catalog_tasks, "get_redis", return_value=fake_redis), patch(
            "scrapers.catalog_registry.get_catalog_scraper", return_value=fake_scraper
        ):
            result = self._fn().run("coles", "full")

        assert result["status"] == "success"
        assert result["products_found"] == 2
        assert result["products_new"] == 2
        # A completed run row was recorded.
        run = (
            test_db_session.query(CatalogCrawlRun)
            .filter_by(chain="coles")
            .order_by(CatalogCrawlRun.run_started_at.desc())
            .first()
        )
        assert run is not None
        assert run.status == "success"
        assert run.products_found == 2
        # Products actually landed in the DB.
        assert test_db_session.query(Product).count() >= 2

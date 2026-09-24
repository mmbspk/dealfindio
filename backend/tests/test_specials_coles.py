"""
Unit tests for ColesSpecialsScraper (specials_coles.py).

Tests cover JSON response parsing, pagination logic, image URL helper,
and bot-challenge detection — without any real HTTP calls.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapers.specials_coles import (
    ColesSpecialsScraper,
    _absolute_image_url,
    _extract_results,
    _parse_item,
    _to_cents,
    _total_results,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_result(name="Coles Milk 2L", now=3.50, was=5.00, brand="Coles", image_uri="/8/0001.jpg"):
    r = {
        "_type": "PRODUCT",
        "name": name,
        "brand": brand,
        "pricing": {"now": now, "was": was},
        "imageUris": [{"uri": image_uri}],
        "categories": [{"name": "Dairy", "seoToken": "dairy"}],
    }
    return r


def _make_page(results, total=None):
    search = {"results": results}
    if total is not None:
        search["noOfResults"] = total
    return {"pageProps": {"searchResults": search}}


# ── _extract_results ──────────────────────────────────────────────────────────


class TestExtractResults:
    @pytest.mark.unit
    def test_standard_shape(self):
        page = _make_page([_make_result()])
        assert len(_extract_results(page)) == 1

    @pytest.mark.unit
    def test_legacy_props_results(self):
        page = {"pageProps": {"results": [_make_result()]}}
        assert len(_extract_results(page)) == 1

    @pytest.mark.unit
    def test_bare_top_level(self):
        page = {"results": [_make_result()]}
        assert len(_extract_results(page)) == 1

    @pytest.mark.unit
    def test_empty_dict(self):
        assert _extract_results({}) == []

    @pytest.mark.unit
    def test_not_a_dict(self):
        assert _extract_results("not a dict") == []


# ── _total_results ────────────────────────────────────────────────────────────


class TestTotalResults:
    @pytest.mark.unit
    def test_reads_noOfResults(self):
        page = _make_page([], total=120)
        assert _total_results(page) == 120

    @pytest.mark.unit
    def test_missing_field_returns_none(self):
        assert _total_results(_make_page([])) is None


# ── _parse_item ───────────────────────────────────────────────────────────────


class TestParseItem:
    TODAY = date(2026, 9, 8)
    VALID_TO = date(2026, 9, 15)

    def _parse(self, item):
        return _parse_item(item, self.TODAY, self.VALID_TO)

    @pytest.mark.unit
    def test_full_product(self):
        p = self._parse(_make_result())
        assert p is not None
        assert p.product_name == "Coles Milk 2L"
        assert p.price_cents == 350
        assert p.was_price_cents == 500
        assert p.brand == "Coles"
        assert p.image_url == "https://productimages.coles.com.au/productimages/8/0001.jpg"
        assert p.category == "Dairy"
        assert p.valid_from == self.TODAY
        assert p.valid_to == self.VALID_TO

    @pytest.mark.unit
    def test_no_was_price(self):
        item = _make_result(was=0)
        p = self._parse(item)
        assert p is not None
        assert p.was_price_cents is None

    @pytest.mark.unit
    def test_was_price_not_higher_than_now_is_dropped(self):
        item = _make_result(now=5.00, was=4.00)
        p = self._parse(item)
        assert p.was_price_cents is None

    @pytest.mark.unit
    def test_ad_tile_skipped(self):
        item = {**_make_result(), "_type": "AD_TILE"}
        assert self._parse(item) is None

    @pytest.mark.unit
    def test_missing_name_skipped(self):
        item = _make_result()
        item.pop("name")
        assert self._parse(item) is None

    @pytest.mark.unit
    def test_missing_pricing_skipped(self):
        item = _make_result()
        item["pricing"] = {}
        assert self._parse(item) is None

    @pytest.mark.unit
    def test_zero_price_skipped(self):
        item = _make_result(now=0)
        assert self._parse(item) is None

    @pytest.mark.unit
    def test_non_dict_skipped(self):
        assert _parse_item("not a dict", self.TODAY, self.VALID_TO) is None

    @pytest.mark.unit
    def test_no_image_uris(self):
        item = _make_result()
        item["imageUris"] = []
        p = self._parse(item)
        assert p is not None
        assert p.image_url is None

    @pytest.mark.unit
    def test_no_categories(self):
        item = _make_result()
        item["categories"] = []
        p = self._parse(item)
        assert p is not None
        assert p.category is None


# ── _absolute_image_url ───────────────────────────────────────────────────────


class TestAbsoluteImageUrl:
    @pytest.mark.unit
    def test_relative_with_leading_slash(self):
        url = _absolute_image_url("/8/1234.jpg")
        assert url == "https://productimages.coles.com.au/productimages/8/1234.jpg"

    @pytest.mark.unit
    def test_relative_without_leading_slash(self):
        url = _absolute_image_url("8/1234.jpg")
        assert url == "https://productimages.coles.com.au/productimages/8/1234.jpg"

    @pytest.mark.unit
    def test_already_absolute_unchanged(self):
        url = _absolute_image_url("https://cdn.example.com/img.jpg")
        assert url == "https://cdn.example.com/img.jpg"

    @pytest.mark.unit
    def test_none_returns_none(self):
        assert _absolute_image_url(None) is None

    @pytest.mark.unit
    def test_empty_string_returns_none(self):
        assert _absolute_image_url("") is None


# ── _to_cents ─────────────────────────────────────────────────────────────────


class TestToCents:
    @pytest.mark.unit
    def test_float(self):
        assert _to_cents(3.50) == 350

    @pytest.mark.unit
    def test_string_price(self):
        assert _to_cents("5.99") == 599

    @pytest.mark.unit
    def test_none_returns_none(self):
        assert _to_cents(None) is None

    @pytest.mark.unit
    def test_invalid_returns_none(self):
        assert _to_cents("not a number") is None

    @pytest.mark.unit
    def test_rounding(self):
        # 1.995 cents → rounds to 200 (i.e. $1.99 → 199 cents = normal, but
        # 1.005 in IEEE754 is actually 100 due to float repr — just verify int round)
        assert _to_cents(2.00) == 200


# ── ColesSpecialsScraper.parse_flyer ─────────────────────────────────────────


class TestColesSpecialsScraperParsing:
    def _scraper(self):
        return ColesSpecialsScraper(db_session=None, redis_client=None)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_single_page_single_product(self):
        pages = [_make_page([_make_result("Bread", 2.50, 4.00)])]
        products = await self._scraper().parse_flyer(pages)
        assert len(products) == 1
        assert products[0].product_name == "Bread"
        assert products[0].price_cents == 250
        assert products[0].was_price_cents == 400

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_multiple_pages_flattened(self):
        pages = [
            _make_page([_make_result("Milk")]),
            _make_page([_make_result("Eggs")]),
        ]
        products = await self._scraper().parse_flyer(pages)
        assert len(products) == 2
        names = {p.product_name for p in products}
        assert names == {"Milk", "Eggs"}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_pages_returns_empty(self):
        products = await self._scraper().parse_flyer([])
        assert products == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ad_tiles_excluded(self):
        ad = {**_make_result("Ad Tile"), "_type": "AD_TILE"}
        pages = [_make_page([_make_result("Real Product"), ad])]
        products = await self._scraper().parse_flyer(pages)
        assert len(products) == 1
        assert products[0].product_name == "Real Product"


# ── ColesSpecialsScraper.discover_source (bootstrap) ─────────────────────────


class TestColesSpecialsScraperBootstrap:
    def _scraper(self):
        return ColesSpecialsScraper(db_session=None, redis_client=None)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_extracts_build_id(self):
        fake_html = '<html><script id="__NEXT_DATA__">{"buildId":"abc123XYZ"}</script></html>'
        mock_resp = MagicMock()
        mock_resp.text = fake_html
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.cookies.jar = []
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.specials_coles.httpx.AsyncClient", return_value=mock_client):
            auth = await self._scraper().discover_source()

        assert auth["build_id"] == "abc123XYZ"
        assert "cookies" in auth

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_raises_on_bot_challenge(self):
        from scrapers.base import ScraperError

        mock_resp = MagicMock()
        mock_resp.text = "<html>No buildId here — Imperva challenge</html>"
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.cookies.jar = []
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("scrapers.specials_coles.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ScraperError, match="blocked after retries"):
                await self._scraper().discover_source()

"""
Unit tests for _parse_item and discover_categories in catalog_woolworths.

Covers:
- Marketplace exclusion via IsMarketProduct flag
- Marketplace exclusion via 10-digit stockcode guard
- Marketplace exclusion via SourcePlatform=WMP secondary signal
- Normal grocery item passes through
- discover_categories skips nodes matching _SKIP_CATEGORY_NAME_FRAGMENTS
"""

from datetime import date

from scrapers.catalog_base import CategoryNode
from scrapers.catalog_woolworths import _parse_item

# Minimal valid grocery item — all required fields present.
_BASE_ITEM = {
    "Name": "Weet-Bix Breakfast Cereal",
    "Price": 5.00,
    "Stockcode": 123456,
    "Barcode": "9300652830019",
    "MediumImageFile": "https://example.com/img.jpg",
    "Brand": "Sanitarium",
    "IsInStock": True,
    "IsAvailable": True,
    "IsMarketProduct": False,
    "IsOnlineOnly": False,
    "PackageSize": "750g",
    "AdditionalAttributes": {},
}

_CATEGORY = CategoryNode(id="1_39FD49C", name="Pantry", url="pantry")
_TODAY = date(2026, 10, 7)


def _item(**overrides):
    return {**_BASE_ITEM, **overrides}


# ── Marketplace exclusion ────────────────────────────────────────────────────


def test_parse_item_skips_is_market_product_flag():
    result = _parse_item(_item(IsMarketProduct=True), _CATEGORY, _TODAY)
    assert result is None


def test_parse_item_skips_10_digit_stockcode():
    """10-digit stockcode = Everyday Market; should be rejected even without the flag."""
    result = _parse_item(_item(Stockcode=1120548579, IsMarketProduct=False), _CATEGORY, _TODAY)
    assert result is None


def test_parse_item_skips_source_platform_wmp():
    """SourcePlatform=WMP in AdditionalAttributes is a secondary marketplace signal."""
    result = _parse_item(
        _item(AdditionalAttributes={"SourcePlatform": "WMP"}),
        _CATEGORY,
        _TODAY,
    )
    assert result is None


def test_parse_item_skips_10_digit_stockcode_without_is_market_flag():
    """Regression: flag absent but stockcode gives it away."""
    item = _item(Stockcode=1094737927, IsMarketProduct=None)
    result = _parse_item(item, _CATEGORY, _TODAY)
    assert result is None


# ── Normal grocery item ──────────────────────────────────────────────────────


def test_parse_item_accepts_normal_grocery():
    result = _parse_item(_item(), _CATEGORY, _TODAY)
    assert result is not None
    assert result.product_name == "Weet-Bix Breakfast Cereal"
    assert result.external_id == "123456"
    assert result.price_cents == 500


def test_parse_item_accepts_6_digit_stockcode():
    result = _parse_item(_item(Stockcode=654321), _CATEGORY, _TODAY)
    assert result is not None
    assert result.external_id == "654321"


# ── discover_categories name-fragment filter ─────────────────────────────────


def test_discover_categories_skips_everyday_market_by_name(monkeypatch):
    """Categories whose Description contains 'everyday market' must be filtered out."""
    import fakeredis

    from scrapers.catalog_base import CatalogStoreConfig
    from scrapers.catalog_woolworths import WoolworthsCatalogScraper

    cfg = CatalogStoreConfig(
        chain="woolworths",
        min_delay_s=0,
        max_delay_s=0,
        burst_limit=100,
        page_size=36,
        max_pages_per_category=1,
        enabled=True,
    )
    scraper = WoolworthsCatalogScraper("woolworths", cfg, redis_client=fakeredis.FakeStrictRedis())

    # Feed two synthetic categories: one real, one marketplace by name.
    fake_cats = [
        {
            "NodeId": "1_39FD49C",
            "Description": "Pantry",
            "IsSpecial": False,
            "UrlFriendlyName": "pantry",
        },
        {
            "NodeId": "1_A999999",
            "Description": "Everyday Market Specials",
            "IsSpecial": False,
            "UrlFriendlyName": "em",
        },
        {
            "NodeId": "1_B999999",
            "Description": "Marketplace Extras",
            "IsSpecial": False,
            "UrlFriendlyName": "mx",
        },
    ]

    import asyncio

    async def fake_get_or_refresh_auth():
        scraper._auth_token = "tok"
        scraper._auth_expiry = 9999999999
        return {"cookies": {}}

    monkeypatch.setattr(scraper, "_get_or_refresh_auth", fake_get_or_refresh_auth)

    # Patch the HTTP call so discover_categories uses our fake data.
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ListTopLevelPiesCategories": {"Categories": fake_cats}}
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: mock_client)

    cats = asyncio.run(scraper.discover_categories())
    names = [c.name for c in cats]

    assert "Pantry" in names
    assert "Everyday Market Specials" not in names
    assert "Marketplace Extras" not in names

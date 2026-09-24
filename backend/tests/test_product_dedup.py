"""
Unit + integration tests for the product deduplication service.

Covers the pure normalization/keying logic, the Redis dedup cache (via
fakeredis), the singleton factories, and async fuzzy name matching against the
async test DB.
"""

from datetime import date
from uuid import uuid4

import pytest
from fakeredis import FakeStrictRedis

from app.services.product_dedup import (
    FuzzyMatcher,
    GTINResolver,
    RedisDedup,
    brands_conflict,
    get_fuzzy_matcher,
    get_gtin_resolver,
    get_redis_dedup,
)


class TestNormalizeName:
    """FuzzyMatcher.normalize_name — pure string logic."""

    def setup_method(self):
        self.m = FuzzyMatcher()

    @pytest.mark.unit
    def test_empty(self):
        assert self.m.normalize_name("") == ""

    @pytest.mark.unit
    def test_lowercases(self):
        assert self.m.normalize_name("MILK") == "milk"

    @pytest.mark.unit
    def test_strips_special_chars(self):
        assert self.m.normalize_name("Coca-Cola!") == "cocacola"

    @pytest.mark.unit
    def test_strips_pack_size_suffixes(self):
        # "2L", "500g", "1kg" are removed.
        assert "2l" not in self.m.normalize_name("Milk 2L")
        assert self.m.normalize_name("Milk 2L").strip() == "milk"
        assert self.m.normalize_name("Flour 1kg").strip() == "flour"
        assert self.m.normalize_name("Chips 500g").strip() == "chips"

    @pytest.mark.unit
    def test_collapses_whitespace(self):
        assert self.m.normalize_name("Full   Cream    Milk") == "full cream milk"

    @pytest.mark.unit
    def test_docstring_example(self):
        # From the method docstring.
        assert self.m.normalize_name("Coca-Cola 2L Bottle") == "cocacola bottle"


class TestBrandsConflict:
    """brands_conflict — brand-based hard veto for the matcher."""

    @pytest.mark.unit
    def test_different_brands_conflict(self):
        # Kirks vs Fanta: same "Orange" name, clearly different companies.
        assert brands_conflict("Kirks", "Fanta") is True

    @pytest.mark.unit
    def test_same_brand_minor_variation_no_conflict(self):
        assert brands_conflict("Coca-Cola", "Coca Cola") is False

    @pytest.mark.unit
    def test_identical_brand_no_conflict(self):
        assert brands_conflict("Cadbury", "Cadbury") is False

    @pytest.mark.unit
    def test_one_missing_brand_is_not_a_conflict(self):
        # A single unknown brand is not enough evidence to block a match.
        assert brands_conflict(None, "Fanta") is False
        assert brands_conflict("Fanta", None) is False

    @pytest.mark.unit
    def test_both_missing_is_not_a_conflict(self):
        assert brands_conflict(None, None) is False
        assert brands_conflict("", "") is False


class TestDedupKey:
    """RedisDedup.dedup_key — deterministic key generation."""

    @pytest.mark.unit
    def test_key_format(self):
        d = RedisDedup()
        pid = uuid4()
        sid = uuid4()
        key = d.dedup_key(pid, sid, 999, date(2026, 8, 28))
        assert key == f"{d.PREFIX}:{pid}:{sid}:999:2026-08-28"

    @pytest.mark.unit
    def test_key_is_deterministic(self):
        d = RedisDedup()
        pid, sid = uuid4(), uuid4()
        k1 = d.dedup_key(pid, sid, 500, date(2026, 1, 1))
        k2 = d.dedup_key(pid, sid, 500, date(2026, 1, 1))
        assert k1 == k2

    @pytest.mark.unit
    def test_different_price_different_key(self):
        d = RedisDedup()
        pid, sid = uuid4(), uuid4()
        assert d.dedup_key(pid, sid, 500, date(2026, 1, 1)) != d.dedup_key(
            pid, sid, 600, date(2026, 1, 1)
        )


class TestRedisDedupCache:
    """is_duplicate / mark_ingested against fakeredis."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_new_key_is_not_duplicate(self):
        d = RedisDedup()
        redis = FakeStrictRedis(decode_responses=True)
        assert await d.is_duplicate("dedup:new", redis) is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_marked_key_is_duplicate(self):
        d = RedisDedup()
        redis = FakeStrictRedis(decode_responses=True)
        await d.mark_ingested("dedup:seen", redis, ttl_days=7)
        assert await d.is_duplicate("dedup:seen", redis) is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_mark_ingested_sets_ttl(self):
        d = RedisDedup()
        redis = FakeStrictRedis(decode_responses=True)
        await d.mark_ingested("dedup:ttl", redis, ttl_days=7)
        ttl = redis.ttl("dedup:ttl")
        # ~7 days in seconds.
        assert 7 * 86400 - 5 <= ttl <= 7 * 86400


class TestSingletonFactories:
    @pytest.mark.unit
    def test_gtin_resolver_singleton(self):
        assert get_gtin_resolver() is get_gtin_resolver()
        assert isinstance(get_gtin_resolver(), GTINResolver)

    @pytest.mark.unit
    def test_fuzzy_matcher_singleton(self):
        assert get_fuzzy_matcher() is get_fuzzy_matcher()
        assert isinstance(get_fuzzy_matcher(), FuzzyMatcher)

    @pytest.mark.unit
    def test_redis_dedup_singleton(self):
        assert get_redis_dedup() is get_redis_dedup()
        assert isinstance(get_redis_dedup(), RedisDedup)

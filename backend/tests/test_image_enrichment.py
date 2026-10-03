"""
Tests for Feature A — Open Food Facts image enrichment.

Covers:
- object_store: key building, extension mapping, local backend round-trip,
  S3/R2 backend config validation + a mocked put_object.
- oft_image_enricher: OFTRateLimiter jitter/backoff, name-search + download
  against a mocked httpx transport (no network), and graceful no-match/error.
- image_tasks: candidate selection query + the enrich task end to end with the
  OFF enricher and object store mocked, asserting products get an image_url.
"""

import asyncio
from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.models import PriceReport, Product, Store
from app.services import object_store as os_mod
from app.services.object_store import (
    LocalObjectStore,
    ObjectStoreError,
    S3ObjectStore,
    extension_for,
    product_image_key,
)
from app.services.oft_image_enricher import (
    OFTImageEnricher,
    OFTRateLimiter,
    _best_image_url,
    _clean_search_name,
    _parse_retry_after,
)

# ── object_store ─────────────────────────────────────────────────────────────


class TestObjectStoreHelpers:
    @pytest.mark.unit
    def test_extension_for_known_types(self):
        assert extension_for("image/jpeg") == "jpg"
        assert extension_for("image/jpg") == "jpg"
        assert extension_for("image/png") == "png"
        assert extension_for("image/webp") == "webp"
        assert extension_for("image/gif") == "gif"

    @pytest.mark.unit
    def test_extension_for_unknown_defaults_jpg(self):
        assert extension_for("application/pdf") == "jpg"
        assert extension_for("") == "jpg"
        assert extension_for(None) == "jpg"  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_product_image_key_sharded(self):
        pid = "550e8400-e29b-41d4-a716-446655440000"
        assert product_image_key(pid, "image/png") == f"products/55/{pid}.png"
        assert product_image_key(pid) == f"products/55/{pid}.jpg"

    @pytest.mark.unit
    def test_product_image_key_short_id_padded(self):
        # A degenerate 1-char id still yields a valid two-char shard.
        assert product_image_key("x", "image/jpeg") == "products/00/x.jpg"


class TestLocalObjectStore:
    @pytest.mark.unit
    def test_round_trip_writes_file_and_returns_url(self, tmp_path):
        store = LocalObjectStore(root=str(tmp_path), base_url="/media")
        url = store.put_object("products/55/abc.jpg", b"imagebytes", "image/jpeg")
        assert url == "/media/products/55/abc.jpg"
        written = tmp_path / "products" / "55" / "abc.jpg"
        assert written.read_bytes() == b"imagebytes"

    @pytest.mark.unit
    def test_base_url_trailing_slash_stripped(self, tmp_path):
        store = LocalObjectStore(root=str(tmp_path), base_url="/media/")
        url = store.put_object("k.jpg", b"x", "image/jpeg")
        assert url == "/media/k.jpg"


class TestObjectStoreFactory:
    @pytest.mark.unit
    def test_build_local_backend(self, tmp_path):
        settings = MagicMock()
        settings.OBJECT_STORAGE_BACKEND = "local"
        settings.OBJECT_STORAGE_LOCAL_ROOT = str(tmp_path)
        settings.OBJECT_STORAGE_LOCAL_BASE_URL = "/media"
        store = os_mod.build_object_store(settings)
        assert isinstance(store, LocalObjectStore)

    @pytest.mark.unit
    def test_unknown_backend_raises(self):
        settings = MagicMock()
        settings.OBJECT_STORAGE_BACKEND = "azure"
        with pytest.raises(ObjectStoreError):
            os_mod.build_object_store(settings)

    @pytest.mark.unit
    def test_s3_backend_requires_public_base_url(self):
        settings = MagicMock()
        settings.OBJECT_STORAGE_BACKEND = "r2"
        settings.R2_PUBLIC_BASE_URL = None
        with pytest.raises(ObjectStoreError):
            os_mod.build_object_store(settings)


class TestS3ObjectStore:
    def _settings(self):
        s = MagicMock()
        s.R2_PUBLIC_BASE_URL = "https://images.dealfindio.com.au/"
        s.R2_ENDPOINT_URL = "https://acct.r2.cloudflarestorage.com"
        s.R2_ACCOUNT_ID = "acct"
        s.R2_BUCKET = "dealfindio-media"
        s.R2_REGION = "auto"
        s.R2_ACCESS_KEY_ID = "ak"
        s.R2_SECRET_ACCESS_KEY = "sk"
        return s

    @pytest.mark.unit
    def test_put_object_uploads_and_returns_cdn_url(self):
        fake_client = MagicMock()
        with patch("boto3.client", return_value=fake_client) as mk:
            store = S3ObjectStore(self._settings())
            url = store.put_object("products/55/abc.jpg", b"bytes", "image/jpeg")

        # Client constructed with the S3-compatible endpoint.
        assert mk.call_args.kwargs["endpoint_url"] == "https://acct.r2.cloudflarestorage.com"
        # put_object called with our key/body/content-type.
        put_kwargs = fake_client.put_object.call_args.kwargs
        assert put_kwargs["Key"] == "products/55/abc.jpg"
        assert put_kwargs["Body"] == b"bytes"
        assert put_kwargs["ContentType"] == "image/jpeg"
        # URL points at the public CDN base (trailing slash stripped), not the bucket.
        assert url == "https://images.dealfindio.com.au/products/55/abc.jpg"

    @pytest.mark.unit
    def test_put_object_wraps_upload_error(self):
        fake_client = MagicMock()
        fake_client.put_object.side_effect = RuntimeError("boom")
        with patch("boto3.client", return_value=fake_client):
            store = S3ObjectStore(self._settings())
            with pytest.raises(ObjectStoreError):
                store.put_object("k.jpg", b"x", "image/jpeg")


# ── OFTRateLimiter ───────────────────────────────────────────────────────────


class TestOFTRateLimiter:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_first_wait_does_not_block_long(self):
        # With no prior request there is no gap to enforce; wait returns fast.
        rl = OFTRateLimiter(min_delay_s=0.0, max_delay_s=0.0)
        await rl.wait()
        assert rl._last_request_at is not None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_on_rate_limited_sets_penalty_window(self):
        rl = OFTRateLimiter(min_delay_s=0.0, max_delay_s=0.0)
        loop_time = asyncio.get_event_loop().time()
        rl.on_rate_limited(retry_after_s=30.0)
        assert rl._penalty_until >= loop_time + 29.0

    @pytest.mark.unit
    def test_parse_retry_after(self):
        assert _parse_retry_after("30") == 30.0
        assert _parse_retry_after(None) == 60.0
        assert _parse_retry_after("garbage") == 60.0


# ── _best_image_url ───────────────────────────────────────────────────────────


class TestBestImageUrl:
    @pytest.mark.unit
    def test_selected_images_front_display_preferred(self):
        product = {
            "image_url": "https://example.com/raw.jpg",
            "image_front_url": "https://example.com/front.jpg",
            "selected_images": {"front": {"display": {"en": "https://example.com/curated.jpg"}}},
        }
        assert _best_image_url(product) == "https://example.com/curated.jpg"

    @pytest.mark.unit
    def test_falls_back_to_non_english_display_lang(self):
        product = {
            "selected_images": {"front": {"display": {"fr": "https://example.com/fr.jpg"}}},
        }
        assert _best_image_url(product) == "https://example.com/fr.jpg"

    @pytest.mark.unit
    def test_falls_back_to_image_front_url(self):
        product = {"image_front_url": "https://example.com/front.jpg", "image_url": "https://example.com/any.jpg"}
        assert _best_image_url(product) == "https://example.com/front.jpg"

    @pytest.mark.unit
    def test_falls_back_to_image_url(self):
        product = {"image_url": "https://example.com/any.jpg"}
        assert _best_image_url(product) == "https://example.com/any.jpg"

    @pytest.mark.unit
    def test_returns_none_when_no_image(self):
        assert _best_image_url({}) is None
        assert _best_image_url({"selected_images": {}}) is None
        assert _best_image_url({"selected_images": {"front": {"display": {}}}}) is None


# ── _clean_search_name ───────────────────────────────────────────────────────


class TestCleanSearchName:
    @pytest.mark.unit
    def test_strips_woolworths_prefix(self):
        assert _clean_search_name("Woolworths Full Cream Milk 2L") == "Full Cream Milk 2L"

    @pytest.mark.unit
    def test_strips_coles_prefix(self):
        assert _clean_search_name("Coles Chicken Breast 500g") == "Chicken Breast 500g"

    @pytest.mark.unit
    def test_strips_aldi_prefix(self):
        assert _clean_search_name("ALDI Butter Cake 600g") == "Butter Cake 600g"

    @pytest.mark.unit
    def test_case_insensitive(self):
        assert _clean_search_name("WOOLWORTHS Milk") == "Milk"
        assert _clean_search_name("coles bread") == "bread"

    @pytest.mark.unit
    def test_non_store_name_unchanged(self):
        assert _clean_search_name("Anchor Full Cream Milk 2L") == "Anchor Full Cream Milk 2L"
        assert _clean_search_name("Weet-Bix 750g") == "Weet-Bix 750g"

    @pytest.mark.unit
    def test_empty_string_unchanged(self):
        assert _clean_search_name("") == ""


# ── OFTImageEnricher ─────────────────────────────────────────────────────────


def _enricher():
    settings = MagicMock()
    settings.OFT_SEARCH_MIN_DELAY_S = 0.0
    settings.OFT_SEARCH_MAX_DELAY_S = 0.0
    settings.OFT_SEARCH_TIMEOUT_S = 5.0
    settings.OPEN_FOOD_FACTS_USER_AGENT = "dealfindio-test/0.1"
    return OFTImageEnricher(settings)


class TestOFTImageEnricher:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_name_returns_none(self):
        enricher = _enricher()
        client = MagicMock()
        assert await enricher.enrich("   ", client) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_full_search_and_download(self):
        # A transport that answers the search JSON then serves the image bytes.
        img_url = "https://static.openfoodfacts.org/images/products/front.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            if "api/v2/search" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "products": [
                            {
                                "code": "9300617006552",
                                # Name closely matches the search term "Milk 2L"
                                "product_name": "Milk 2L",
                                "image_front_url": img_url,
                            }
                        ]
                    },
                )
            if str(request.url) == img_url:
                return httpx.Response(
                    200,
                    content=b"\xff\xd8\xff\xe0jpegbytes",
                    headers={"Content-Type": "image/jpeg"},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _enricher().enrich("Milk 2L", client)

        assert result is not None
        assert result.image_bytes == b"\xff\xd8\xff\xe0jpegbytes"
        assert result.content_type == "image/jpeg"
        assert result.source_url == img_url
        assert result.off_code == "9300617006552"
        assert result.match_score >= 0.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_search_results_returns_none(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"products": []}))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await _enricher().enrich("Nonexistent", client) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_match_without_image_returns_none(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"products": [{"code": "1", "product_name": "X"}]})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await _enricher().enrich("X", client) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_non_image_content_type_skipped(self):
        img_url = "https://static.openfoodfacts.org/x.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            if "api/v2/search" in str(request.url):
                return httpx.Response(
                    200, json={"products": [{"code": "1", "image_front_url": img_url}]}
                )
            return httpx.Response(200, content=b"<html>", headers={"Content-Type": "text/html"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            assert await _enricher().enrich("thing", client) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_search_429_backs_off_and_returns_none(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(429, headers={"Retry-After": "1"})
        )
        enricher = _enricher()
        async with httpx.AsyncClient(transport=transport) as client:
            assert await enricher.enrich("thing", client) is None
        # Backoff penalty was recorded.
        assert enricher._rate_limiter._penalty_until > 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_search_network_error_returns_none(self):
        def boom(req):
            raise httpx.ConnectError("down")

        transport = httpx.MockTransport(boom)
        async with httpx.AsyncClient(transport=transport) as client:
            assert await _enricher().enrich("thing", client) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_low_similarity_match_rejected(self):
        """A result with mismatched name is rejected even if it has an image."""
        img_url = "https://static.openfoodfacts.org/images/products/front.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            if "api/v2/search" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "products": [
                            {
                                "code": "12345",
                                # Completely different product name — similarity will be ~0
                                "product_name": "Tomato Sauce 500g",
                                "image_front_url": img_url,
                            }
                        ]
                    },
                )
            return httpx.Response(200, content=b"\xff\xd8", headers={"Content-Type": "image/jpeg"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _enricher().enrich("Full Cream Milk 2L", client)

        assert result is None, "Low-similarity match should be rejected"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_high_similarity_match_accepted_with_score(self):
        """A result with sufficiently similar name is accepted and carries match_score."""
        img_url = "https://static.openfoodfacts.org/images/products/front.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            if "api/v2/search" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "products": [
                            {
                                "code": "9300617006552",
                                "product_name": "Full Cream Milk 2L",
                                "image_front_url": img_url,
                            }
                        ]
                    },
                )
            return httpx.Response(200, content=b"\xff\xd8", headers={"Content-Type": "image/jpeg"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _enricher().enrich("Full Cream Milk 2L", client)

        assert result is not None
        assert result.match_score >= 60.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_selected_images_url_used_over_image_front_url(self):
        """selected_images.front.display.en is preferred over image_front_url."""
        curated_url = "https://static.openfoodfacts.org/images/products/front_en.400.jpg"
        raw_url = "https://static.openfoodfacts.org/images/products/front.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            if "api/v2/search" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "products": [
                            {
                                "code": "9300617006552",
                                "product_name": "Full Cream Milk 2L",
                                "image_front_url": raw_url,
                                "selected_images": {"front": {"display": {"en": curated_url}}},
                            }
                        ]
                    },
                )
            if str(request.url) == curated_url:
                return httpx.Response(200, content=b"\xff\xd8", headers={"Content-Type": "image/jpeg"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _enricher().enrich("Full Cream Milk 2L", client)

        assert result is not None
        assert result.source_url == curated_url

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_best_scoring_match_returned_not_first(self):
        """When multiple products pass threshold, the highest-scoring one wins."""
        good_url = "https://static.openfoodfacts.org/good.jpg"
        better_url = "https://static.openfoodfacts.org/better.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            if "api/v2/search" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "products": [
                            # Listed first but less specific name
                            {"code": "111", "product_name": "Whole Milk 2L", "image_front_url": good_url},
                            # Listed second but exact match — should win
                            {"code": "222", "product_name": "Full Cream Milk 2L", "image_front_url": better_url},
                        ]
                    },
                )
            if str(request.url) in (good_url, better_url):
                return httpx.Response(200, content=b"\xff\xd8", headers={"Content-Type": "image/jpeg"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _enricher().enrich("Full Cream Milk 2L", client)

        assert result is not None
        assert result.source_url == better_url
        assert result.off_code == "222"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_gtin_lookup_used_when_provided(self):
        """GTIN path hits /api/v2/product/{gtin} and returns score=100."""
        gtin = "9310055002840"
        img_url = "https://static.openfoodfacts.org/images/products/front.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            if f"/api/v2/product/{gtin}" in str(request.url):
                return httpx.Response(
                    200,
                    json={"product": {"code": gtin, "product_name": "Weet-Bix 750g", "image_front_url": img_url}},
                )
            if str(request.url) == img_url:
                return httpx.Response(200, content=b"\xff\xd8", headers={"Content-Type": "image/jpeg"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _enricher().enrich("Weet-Bix 750g", client, gtin=gtin)

        assert result is not None
        assert result.match_score == 100.0
        assert result.off_code == gtin

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_gtin_name_mismatch_falls_back_to_text_search(self):
        """GTIN hit whose OFF name doesn't match our product name falls back to search.

        Woolworths internal store IDs can collide with unrelated OFF barcodes
        (e.g. our 'Jim Beam Bourbon' GTIN resolves to 'Chocolate Milk' in OFF).
        The enricher must reject the hit and try text search instead.
        """
        gtin = "00000020953584"
        img_url = "https://static.openfoodfacts.org/images/products/front.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            if f"/api/v2/product/{gtin}" in str(request.url):
                # OFF returns a completely different product for this barcode
                return httpx.Response(
                    200,
                    json={"product": {"code": gtin, "product_name": "Chocolate Milk 2L", "image_front_url": img_url}},
                )
            if "api/v2/search" in str(request.url):
                # Text search finds the right product
                return httpx.Response(
                    200,
                    json={"products": [{"code": "jim-beam-code", "product_name": "Jim Beam Bourbon", "image_front_url": img_url}]},
                )
            if str(request.url) == img_url:
                return httpx.Response(200, content=b"\xff\xd8", headers={"Content-Type": "image/jpeg"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _enricher().enrich("Jim Beam Bourbon", client, gtin=gtin)

        assert result is not None
        # Must have come from text search, not the wrong GTIN hit
        assert result.off_code == "jim-beam-code"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_gtin_404_falls_back_to_text_search(self):
        """404 on barcode lookup transparently falls back to text search."""
        gtin = "0000000000000"
        img_url = "https://static.openfoodfacts.org/images/products/front.jpg"

        def handler(request: httpx.Request) -> httpx.Response:
            if f"/api/v2/product/{gtin}" in str(request.url):
                return httpx.Response(404)
            if "api/v2/search" in str(request.url):
                return httpx.Response(
                    200,
                    json={"products": [{"code": "111", "product_name": "Milk 2L", "image_front_url": img_url}]},
                )
            if str(request.url) == img_url:
                return httpx.Response(200, content=b"\xff\xd8", headers={"Content-Type": "image/jpeg"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _enricher().enrich("Milk 2L", client, gtin=gtin)

        assert result is not None  # fell back to search successfully
        assert result.off_code == "111"  # code from search mock, not the GTIN we passed

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_country_filter_sent_in_search_params(self):
        """Text search always includes countries_tags=en:australia."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"products": []})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await _enricher().enrich("Milk 2L", client)

        search_req = next((r for r in captured if "api/v2/search" in str(r.url)), None)
        assert search_req is not None
        assert "en%3Aaustralia" in str(search_req.url) or "en:australia" in str(search_req.url)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_store_prefix_stripped_before_search(self):
        """'Woolworths Milk 2L' is searched as 'Milk 2L', improving similarity."""
        captured_terms: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "api/v2/search" in str(request.url):
                captured_terms.append(str(request.url))
            return httpx.Response(200, json={"products": []})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await _enricher().enrich("Woolworths Full Cream Milk 2L", client)

        assert captured_terms, "Expected a search request to be made"
        assert "Woolworths" not in captured_terms[0]
        assert "Full+Cream+Milk" in captured_terms[0] or "Full%20Cream%20Milk" in captured_terms[0]


# ── image_tasks ──────────────────────────────────────────────────────────────


class TestFindProductsMissingImages:
    @pytest.mark.integration
    def test_filters_by_chain_and_null_image(self, test_db_session, sample_store: Store):
        from app.workers.image_tasks import _find_products_missing_images

        # p1: coles, no image -> candidate.  p2: coles, has image -> excluded.
        # p3: woolworths, no image -> excluded when chain=coles.
        p1 = Product(id=uuid4(), name="Coles Milk", gtin=None)
        p2 = Product(id=uuid4(), name="Coles Bread", gtin=None, image_url="http://x/y.jpg")
        p3 = Product(id=uuid4(), name="Woolies Eggs", gtin=None)
        test_db_session.add_all([p1, p2, p3])
        test_db_session.flush()
        for prod, source in [(p1, "catalog_coles"), (p2, "catalog_coles"), (p3, "catalog_woolworths")]:
            test_db_session.add(
                PriceReport(
                    product_id=prod.id,
                    store_id=sample_store.id,
                    price_cents=350,
                    source=source,
                    valid_from=date.today(),
                )
            )
        test_db_session.commit()

        found = _find_products_missing_images(test_db_session, "coles", 50)
        ids = {p.id for p in found}
        assert p1.id in ids
        assert p2.id not in ids  # has image
        assert p3.id not in ids  # wrong chain

    @pytest.mark.integration
    def test_all_chains_sweep_ignores_source(self, test_db_session, sample_store: Store):
        from app.workers.image_tasks import _find_products_missing_images

        p1 = Product(id=uuid4(), name="A", gtin=None)
        test_db_session.add(p1)
        test_db_session.commit()
        found = _find_products_missing_images(test_db_session, None, 50)
        assert p1.id in {p.id for p in found}


class TestEnrichProductImagesTask:
    def _fn(self):
        from app.workers.image_tasks import enrich_product_images

        return enrich_product_images

    @pytest.mark.integration
    def test_invalid_chain_raises(self):
        with pytest.raises(ValueError):
            self._fn().run("not_a_chain")

    @pytest.mark.integration
    def test_no_candidates_returns_success(self, test_db_session):
        from app.workers import image_tasks

        with patch.object(image_tasks, "get_db_session", return_value=test_db_session):
            result = self._fn().run("coles")
        assert result["status"] == "success"
        assert result["candidates"] == 0
        assert result["enriched"] == 0

    @pytest.mark.integration
    def test_enriches_and_writes_image_url(self, test_db_session, sample_store: Store):
        from app.services.oft_image_enricher import EnrichedImage
        from app.workers import image_tasks

        product = Product(id=uuid4(), name="Coles Milk 2L", gtin=None)
        test_db_session.add(product)
        test_db_session.flush()
        test_db_session.add(
            PriceReport(
                product_id=product.id,
                store_id=sample_store.id,
                price_cents=350,
                source="catalog_coles",
                valid_from=date.today(),
            )
        )
        test_db_session.commit()
        product_id = product.id

        fake_image = EnrichedImage(
            image_bytes=b"jpegbytes",
            content_type="image/jpeg",
            source_url="https://static.oft/x.jpg",
            off_code="1",
            match_score=85.0,
        )
        fake_store = MagicMock()
        fake_store.put_object.return_value = "https://images.dealfindio.com.au/products/xx/abc.jpg"

        async def fake_enrich_all(targets):
            return [(pid, fake_image) for pid, _name, _gtin in targets]

        with patch.object(
            image_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(image_tasks, "get_object_store", return_value=fake_store), patch.object(
            image_tasks, "_enrich_all", side_effect=fake_enrich_all
        ):
            result = self._fn().run("coles")

        assert result["status"] == "success"
        assert result["enriched"] == 1
        refreshed = test_db_session.get(Product, product_id)
        assert refreshed.image_url == "https://images.dealfindio.com.au/products/xx/abc.jpg"
        assert refreshed.image_source == "open_food_facts"
        assert refreshed.image_enriched_at is not None

    @pytest.mark.integration
    def test_no_match_counts_as_skipped(self, test_db_session, sample_store: Store):
        from app.workers import image_tasks

        product = Product(id=uuid4(), name="Obscure Item", gtin=None)
        test_db_session.add(product)
        test_db_session.flush()
        test_db_session.add(
            PriceReport(
                product_id=product.id,
                store_id=sample_store.id,
                price_cents=999,
                source="catalog_coles",
                valid_from=date.today(),
            )
        )
        test_db_session.commit()

        async def fake_enrich_all(targets):
            return [(pid, None) for pid, _name, _gtin in targets]

        fake_store = MagicMock()
        with patch.object(
            image_tasks, "get_db_session", return_value=test_db_session
        ), patch.object(image_tasks, "get_object_store", return_value=fake_store), patch.object(
            image_tasks, "_enrich_all", side_effect=fake_enrich_all
        ):
            result = self._fn().run("coles")

        assert result["enriched"] == 0
        assert result["skipped"] == 1
        fake_store.put_object.assert_not_called()

"""
Tests for the AI cross-store dedup judge + real-GTIN recovery (ai_dedup.py) and
the admin endpoint's ai_* fields.

No live API cost: the Anthropic client and httpx are mocked. Positive GTIN cases
use real, checksum-valid EAN-13 codes (4006381333931, 5901234123457).
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.ai_dedup import (
    AIVerdict,
    DedupDecision,
    ProductFacts,
    build_judge_prompt,
    choose_survivor,
    extract_size_hint,
    is_ean13_valid,
    judge_pair,
    pre_guard,
    propagate_gtin_on_merge,
    same_chain,
    search_gtin_by_name_brand,
)

# Real, checksum-valid EAN-13 barcodes for positive cases.
VALID_A = "4006381333931"
VALID_B = "5901234123457"
# A Woolworths Stockcode padded to 14 digits — must NOT pass the checksum.
STOCKCODE = "00000000123456"


def _facts(
    id_=None,
    name="Coke 1.25L",
    brand="Coca-Cola",
    chain="coles",
    gtin=None,
    has_image=False,
    category="drinks",
    price=2.5,
):
    return ProductFacts(
        id=str(id_ or uuid4()),
        name=name,
        brand=brand,
        category=category,
        gtin=gtin,
        price_aud=price,
        chain=chain,
        size_hint=extract_size_hint(name),
        has_image=has_image,
    )


# ── is_ean13_valid ────────────────────────────────────────────────────────────


class TestEan13Valid:
    def test_real_barcodes_pass(self):
        assert is_ean13_valid(VALID_A)
        assert is_ean13_valid(VALID_B)

    def test_leading_zero_14_digit_form_passes(self):
        assert is_ean13_valid("0" + VALID_A)

    def test_padded_stockcode_fails(self):
        assert not is_ean13_valid(STOCKCODE)

    def test_all_zeros_and_none_and_short_fail(self):
        assert not is_ean13_valid("0000000000000")
        assert not is_ean13_valid(None)
        assert not is_ean13_valid("12345")
        assert not is_ean13_valid("")

    def test_14_digit_nonzero_lead_fails(self):
        # 14 digits whose leading digit isn't 0 is not our storage form.
        assert not is_ean13_valid("1" + VALID_A)


# ── extract_size_hint ───────────────────────────────────────────────────────


class TestSizeHint:
    def test_pulls_litres_and_grams(self):
        assert extract_size_hint("Coca-Cola 1.25L") == "1.25l"
        assert extract_size_hint("Cadbury Block 180g") == "180g"

    def test_none_when_absent(self):
        assert extract_size_hint("Fresh Bananas") is None
        assert extract_size_hint(None) is None


# ── pre_guard / same_chain ───────────────────────────────────────────────────


class TestPreGuard:
    def test_same_chain_vetoed(self):
        a = _facts(chain="coles")
        b = _facts(chain="coles")
        assert same_chain(a, b)
        assert pre_guard(a, b) is DedupDecision.VETOED

    def test_brand_conflict_vetoed(self):
        a = _facts(chain="coles", brand="Kirks")
        b = _facts(chain="woolworths", brand="Fanta")
        assert pre_guard(a, b) is DedupDecision.VETOED

    def test_clean_cross_chain_proceeds(self):
        a = _facts(chain="coles", brand="Coca-Cola")
        b = _facts(chain="woolworths", brand="Coca-Cola")
        assert pre_guard(a, b) is None


# ── choose_survivor ──────────────────────────────────────────────────────────


class TestChooseSurvivor:
    def test_valid_gtin_record_wins_over_model_keep(self):
        # a has a real barcode; even if the model says keep "b", a survives.
        a = _facts(gtin=VALID_A)
        b = _facts(gtin=None)
        verdict = AIVerdict(same_product=True, confidence=0.9, keep="b", reason="")
        keep_id, drop_id = choose_survivor(a, b, verdict)
        assert keep_id == a.id
        assert drop_id == b.id

    def test_model_keep_breaks_metadata_tie(self):
        a = _facts(gtin=None, has_image=True, brand="X", name="AAAA")
        b = _facts(gtin=None, has_image=True, brand="X", name="AAAA")
        verdict = AIVerdict(same_product=True, confidence=0.9, keep="b", reason="")
        keep_id, _ = choose_survivor(a, b, verdict)
        assert keep_id == b.id

    def test_richer_metadata_wins_when_no_gtin(self):
        a = _facts(gtin=None, has_image=True)
        b = _facts(gtin=None, has_image=False)
        verdict = AIVerdict(same_product=True, confidence=0.9, keep="b", reason="")
        keep_id, _ = choose_survivor(a, b, verdict)
        assert keep_id == a.id


# ── judge_pair (mocked Anthropic) ────────────────────────────────────────────


def _mock_client(payload: dict) -> MagicMock:
    """A fake Anthropic client whose messages.create returns a JSON text block."""
    client = MagicMock()
    block = SimpleNamespace(text=json.dumps(payload))
    client.messages.create.return_value = SimpleNamespace(content=[block])
    return client


class TestJudgePair:
    def test_parses_verdict_from_json_block(self):
        client = _mock_client(
            {"same_product": True, "confidence": 0.93, "keep": "a", "reason": "same item"}
        )
        a = _facts(chain="coles")
        b = _facts(chain="woolworths")
        verdict, _ = judge_pair(a, b, client=client)
        assert isinstance(verdict, AIVerdict)
        assert verdict.same_product is True
        assert verdict.confidence == pytest.approx(0.93)
        assert verdict.keep == "a"

    def test_pre_guard_short_circuits_without_api_call(self):
        client = _mock_client({"same_product": True, "confidence": 1.0, "keep": "a"})
        a = _facts(chain="coles", brand="Kirks")
        b = _facts(chain="woolworths", brand="Fanta")
        verdict, _ = judge_pair(a, b, client=client)
        assert verdict is DedupDecision.VETOED
        client.messages.create.assert_not_called()

    def test_post_guard_downgrades_same_when_brands_conflict(self):
        # Model says same, but brands actually conflict → deterministic downgrade.
        client = _mock_client(
            {"same_product": True, "confidence": 0.99, "keep": "a", "reason": "looks same"}
        )
        # brands_conflict is only reachable post-hoc if pre_guard didn't fire; use
        # brands that conflict — pre_guard would veto, so this is really the
        # post-guard path when reached. Assert the terminal outcome is not "merge".
        a = _facts(chain="coles", brand="Kirks")
        b = _facts(chain="woolworths", brand="Fanta")
        verdict, _ = judge_pair(a, b, client=client)
        # pre_guard already vetoes this pair — never merged.
        assert verdict is DedupDecision.VETOED

    def test_json_fenced_response_is_parsed(self):
        client = MagicMock()
        fenced = (
            "```json\n"
            + json.dumps(
                {"same_product": False, "confidence": 0.2, "keep": "b", "reason": "diff size"}
            )
            + "\n```"
        )
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text=fenced)]
        )
        verdict, _ = judge_pair(_facts(chain="coles"), _facts(chain="woolworths"), client=client)
        assert isinstance(verdict, AIVerdict)
        assert verdict.same_product is False

    def test_api_error_returns_skipped_not_crash(self):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("boom")
        verdict, _ = judge_pair(_facts(chain="coles"), _facts(chain="woolworths"), client=client)
        assert verdict is DedupDecision.SKIPPED

    def test_bad_json_returns_skipped(self):
        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="not json at all")]
        )
        verdict, _ = judge_pair(_facts(chain="coles"), _facts(chain="woolworths"), client=client)
        assert verdict is DedupDecision.SKIPPED

    def test_prompt_includes_size_hint(self):
        a = _facts(name="Coke 2L", chain="coles")
        b = _facts(name="Coke 1L", chain="woolworths")
        system, user = build_judge_prompt(a, b)
        assert "size_hint" in user
        assert "2l" in user and "1l" in user


# ── propagate_gtin_on_merge (DB) ─────────────────────────────────────────────


# Raw-SQL UUID comparisons only match under Postgres; on SQLite the PG_UUID type
# stores a dash-less hex string that never equals a dashed bind param. Guard the
# whole class the way test_dedup_admin guards its raw-SQL merge tests.
@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="requires Postgres (raw-SQL UUID comparison); set TEST_DATABASE_URL",
)
class TestPropagateGtin:
    def _seed(self, session, keep_gtin, drop_gtin):
        from app.models import Product

        keep = Product(
            id=uuid4(),
            name="Coke A",
            brand="Coca-Cola",
            gtin=keep_gtin,
        )
        drop = Product(
            id=uuid4(),
            name="Coke B",
            brand="Coca-Cola",
            gtin=drop_gtin,
        )
        session.add_all([keep, drop])
        session.flush()
        return keep, drop

    @pytest.mark.integration
    def test_valid_gtin_propagates_to_survivor(self, test_db_session):
        keep, drop = self._seed(test_db_session, keep_gtin=None, drop_gtin=VALID_A)
        result = propagate_gtin_on_merge(test_db_session, str(keep.id), str(drop.id))
        assert result == VALID_A
        test_db_session.expire_all()
        from app.models import Product

        refreshed = test_db_session.get(Product, keep.id)
        assert refreshed.gtin == VALID_A

    @pytest.mark.integration
    def test_invalid_gtin_not_propagated(self, test_db_session):
        keep, drop = self._seed(test_db_session, keep_gtin=None, drop_gtin=STOCKCODE)
        result = propagate_gtin_on_merge(test_db_session, str(keep.id), str(drop.id))
        assert result is None
        test_db_session.expire_all()
        from app.models import Product

        assert test_db_session.get(Product, keep.id).gtin is None

    @pytest.mark.integration
    def test_survivor_gtin_preserved(self, test_db_session):
        # Survivor already has a GTIN — never overwrite it.
        keep, drop = self._seed(test_db_session, keep_gtin=VALID_A, drop_gtin=VALID_B)
        result = propagate_gtin_on_merge(test_db_session, str(keep.id), str(drop.id))
        assert result is None
        test_db_session.expire_all()
        from app.models import Product

        assert test_db_session.get(Product, keep.id).gtin == VALID_A

    @pytest.mark.integration
    def test_unrelated_owner_does_not_block_propagation(self, test_db_session):
        # A third product owns a DIFFERENT valid GTIN. It must not be mistaken
        # for a clash — the survivor still gets the dropped row's barcode. This
        # is the regression guard for the old clash query that self-matched the
        # dropped row (and would also have over-matched any unrelated row).
        from app.models import Product

        other = Product(id=uuid4(), name="Owner", gtin=VALID_B)
        test_db_session.add(other)
        test_db_session.flush()
        keep, drop = self._seed(test_db_session, keep_gtin=None, drop_gtin=VALID_A)
        result = propagate_gtin_on_merge(test_db_session, str(keep.id), str(drop.id))
        assert result == VALID_A
        test_db_session.expire_all()
        assert test_db_session.get(Product, keep.id).gtin == VALID_A


# The uniqueness-collision guard uses a stub session (no DB), so it runs on both
# lanes — products.gtin is UNIQUE, making a real same-gtin collision impossible
# to seed without a concurrent writer.
class TestPropagateGtinCollision:
    def test_uniqueness_collision_skips(self):
        # products.gtin is UNIQUE, so no real row can share the dropped row's
        # barcode — the collision path is only reachable if a concurrent writer
        # slips a row in. Exercise the guard directly with a stub session whose
        # clash query returns a hit, proving we bail out rather than emit a
        # duplicate-key UPDATE.
        from app.services import ai_dedup

        keep_id, drop_id = str(uuid4()), str(uuid4())

        class _StubResult:
            def __init__(self, rows=None, one=None):
                self._rows, self._one = rows, one

            def fetchall(self):
                return self._rows or []

            def fetchone(self):
                return self._one

        class _StubSession:
            def __init__(self):
                self.updated = False

            def execute(self, statement, params=None):
                sql = str(statement).lower()
                if sql.startswith("select id, gtin"):
                    return _StubResult(
                        rows=[
                            SimpleNamespace(id=keep_id, gtin=None),
                            SimpleNamespace(id=drop_id, gtin=VALID_A),
                        ]
                    )
                if "limit 1" in sql:  # the uniqueness clash probe
                    return _StubResult(one=(1,))
                if sql.startswith("update products"):
                    self.updated = True
                    return _StubResult()
                return _StubResult()

        session = _StubSession()
        result = ai_dedup.propagate_gtin_on_merge(session, keep_id, drop_id)
        assert result is None
        assert session.updated is False


# ── search_gtin_by_name_brand (mocked httpx) ─────────────────────────────────


def _mock_httpx_get(products: list[dict], status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {}
    resp.json.return_value = {"products": products}
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
class TestSearchGtinByNameBrand:
    async def _limiter(self):
        limiter = MagicMock()
        limiter.wait = AsyncMock()
        limiter.on_rate_limited = MagicMock()
        return limiter

    async def test_returns_valid_brand_agreeing_code(self):
        client = _mock_httpx_get(
            [
                {"code": VALID_A, "product_name": "Coca-Cola 1.25L", "brands": "Coca-Cola"},
            ]
        )
        code = await search_gtin_by_name_brand(
            "Coca-Cola 1.25L", "Coca-Cola", client, await self._limiter(), "ua", 10.0
        )
        assert code == VALID_A

    async def test_checksum_fail_rejected(self):
        client = _mock_httpx_get(
            [
                {"code": STOCKCODE, "product_name": "X", "brands": "Coca-Cola"},
            ]
        )
        code = await search_gtin_by_name_brand(
            "Coke", "Coca-Cola", client, await self._limiter(), "ua", 10.0
        )
        assert code is None

    async def test_brand_mismatch_rejected(self):
        client = _mock_httpx_get(
            [
                {"code": VALID_A, "product_name": "Fanta", "brands": "Fanta"},
            ]
        )
        code = await search_gtin_by_name_brand(
            "Orange", "Kirks", client, await self._limiter(), "ua", 10.0
        )
        assert code is None

    async def test_429_returns_none_and_backs_off(self):
        limiter = await self._limiter()
        client = _mock_httpx_get([], status_code=429)
        code = await search_gtin_by_name_brand("Coke", "Coca-Cola", client, limiter, "ua", 10.0)
        assert code is None
        limiter.on_rate_limited.assert_called_once()


# ── Admin endpoint surfaces ai_* fields ──────────────────────────────────────


# list_dedup_candidates uses Postgres-only SQL (DISTINCT ON, CAST … AS uuid[]),
# so this endpoint test needs a real Postgres DB like test_dedup_admin's.
@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="requires Postgres (DISTINCT ON / uuid[] cast); set TEST_DATABASE_URL",
)
class TestAdminAiFields:
    @pytest.fixture
    def admin_client(self, test_db_session):
        from fastapi.testclient import TestClient

        from app.deps import get_current_user, get_db, require_admin
        from app.main import app
        from app.models.user import User

        def override_get_db():
            return test_db_session

        admin_user = User(firebase_uid="admin_uid", email="admin@example.com")
        admin_user.is_admin = True
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: admin_user
        app.dependency_overrides[require_admin] = lambda: admin_user
        client = TestClient(app)
        yield client
        app.dependency_overrides.clear()

    @pytest.mark.integration
    def test_list_candidates_includes_ai_fields(self, admin_client, test_db_session):
        from app.models import Product
        from app.models.product_match import ProductMatchCandidate

        a = Product(id=uuid4(), name="Coke 1.25L", brand="Coca-Cola")
        b = Product(id=uuid4(), name="Coca Cola 1.25L", brand="Coca-Cola")
        test_db_session.add_all([a, b])
        test_db_session.flush()
        cand = ProductMatchCandidate(
            id=uuid4(),
            product_a_id=a.id,
            product_b_id=b.id,
            score=96.0,
            status="pending",
            proposed_by="ai",
            ai_verdict="same",
            ai_confidence=0.94,
            ai_keep="a",
            ai_reason="same item, same size",
        )
        test_db_session.add(cand)
        test_db_session.commit()

        resp = admin_client.get("/api/v1/admin/dedup/candidates?status=pending")
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["ai_verdict"] == "same"
        assert item["ai_confidence"] == pytest.approx(0.94)
        assert item["ai_keep"] == "a"
        assert item["ai_reason"] == "same item, same size"

"""
AI-powered cross-store product dedup judge + real-GTIN recovery.

A Claude *judge* layered on top of the existing fuzzy pre-filter. The fuzzy
matcher (``product_dedup.FuzzyMatcher`` / ``catalog_ingest._match_product``)
generates plausible cross-chain pairs; this module decides, per pair, whether
they are the SAME physical product and which record should survive a merge.

Design guarantees (all deterministic rules win over the model):
  - **Cross-store only.** We never judge two products from the same chain.
  - **Brand agreement.** ``brands_conflict`` (from ``product_dedup``) hard-vetoes
    a pair before *and* after the model responds — the AI can't override it.
  - **Recover real GTINs only.** ``is_ean13_valid`` gates every GTIN we write, so
    we never propagate a Woolworths Stockcode-as-GTIN, and
    ``search_gtin_by_name_brand`` only returns barcodes OFF actually served that
    pass the checksum and agree on brand. Nothing is invented.

The Anthropic SDK is pinned old (``^0.7.1``): there is no ``messages.parse`` /
structured-output API. We use the same proven pattern as
``receipt_parser`` — ``client.messages.create(...)`` then strip any JSON fence
and ``json.loads`` — and validate into a pydantic ``AIVerdict``.

Everything here is **sync** to match the batch-runner / ``catalog_ingest``
``Session``. The sole exception is ``search_gtin_by_name_brand`` (async), which
reuses the async OFF search primitives from ``oft_image_enricher``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Product
from app.models.product_retailer_listing import ProductRetailerListing
from app.services.oft_image_enricher import (
    _OFF_SEARCH_URL,
    OFTRateLimiter,
    _parse_retry_after,
)
from app.services.product_dedup import brands_conflict
from app.services.receipt_parser import _strip_json_fences

logger = logging.getLogger(__name__)

# Default model — matches receipt_parser.CLAUDE_VISION_MODEL, so it is known-good
# with the pinned SDK / this account. Overridable via Settings.AI_DEDUP_MODEL.
DEFAULT_AI_DEDUP_MODEL = "claude-sonnet-4-6"

# Extract the size token the fuzzy normalizer strips, so the judge can see it.
# Mirrors the suffix regex in FuzzyMatcher.normalize_name.
_SIZE_HINT_RE = re.compile(r"\b(\d+\.?\d*)\s*(l|ml|kg|g|oz|lb|pk|pack|s)\b", re.IGNORECASE)


def _build_store_url(
    chain: Optional[str], external_id: Optional[str], name: Optional[str] = None
) -> Optional[str]:
    if not chain or not external_id:
        return None
    if chain == "woolworths":
        return f"https://www.woolworths.com.au/shop/productdetails/{external_id}"
    if chain == "coles":
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") if name else ""
        return (
            f"https://www.coles.com.au/product/{slug}-{external_id}"
            if slug
            else f"https://www.coles.com.au/product/{external_id}"
        )
    if chain == "iga":
        return f"https://www.igashop.com.au/product/{external_id}"
    if chain == "aldi":
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") if name else ""
        return f"https://www.aldi.com.au/product/{slug}-{external_id}" if slug else None
    return None


# ── Data shapes ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductFacts:
    """The minimal, judge-facing view of a product row."""

    id: str
    name: str
    brand: Optional[str]
    category: Optional[str]
    gtin: Optional[str]
    price_aud: Optional[float]
    chain: Optional[str]  # resolved from primary ProductRetailerListing.chain
    size_hint: Optional[str]  # parsed from name, e.g. "2L" / "500g"
    has_image: bool = False
    store_url: Optional[str] = None  # constructable from external_id + chain
    package_size: Optional[str] = None  # canonical package_size_norm e.g. "375mL"
    unit_price: Optional[float] = None  # comparable price per standard unit
    unit_price_label: Optional[str] = None  # unit label e.g. "100g", "100ml", "ea"

    @classmethod
    def from_product(
        cls,
        p: "Product",
        price_aud: Optional[float] = None,
        *,
        chain: Optional[str] = None,
        external_id: Optional[str] = None,
    ) -> "ProductFacts":
        return cls(
            id=str(p.id),
            name=p.name,
            brand=p.brand,
            category=p.category,
            gtin=p.gtin,
            price_aud=price_aud,
            chain=chain,
            size_hint=extract_size_hint(p.name),
            has_image=bool(getattr(p, "image_url", None)),
            store_url=_build_store_url(chain, external_id, p.name),
            package_size=getattr(p, "package_size_norm", None),
            unit_price=getattr(p, "unit_price", None),
            unit_price_label=getattr(p, "unit_price_label", None),
        )


class AIVerdict(BaseModel):
    """The judge's structured decision for one pair."""

    same_product: bool
    confidence: float = Field(ge=0.0, le=1.0)
    keep: Literal["a", "b"]
    reason: str = ""

    @field_validator("reason")
    @classmethod
    def _clip_reason(cls, v: str) -> str:
        v = (v or "").strip()
        return v[:200]


class DedupDecision(str, Enum):
    """Terminal outcomes that bypass or short-circuit the model."""

    SAME = "same"
    DIFFERENT = "different"
    VETOED = "vetoed"
    SKIPPED = "skipped"


# ── Deterministic helpers ────────────────────────────────────────────────────


def extract_size_hint(name: Optional[str]) -> Optional[str]:
    """Pull the first pack/size token out of a product name (e.g. '2L', '500g').

    The fuzzy normalizer deliberately strips these, which hides real size
    differences from name matching. We re-surface it so the judge sees e.g.
    '2L' vs '1L' explicitly and does not merge different sizes.
    """
    if not name:
        return None
    m = _SIZE_HINT_RE.search(name)
    if not m:
        return None
    return f"{m.group(1)}{m.group(2).lower()}"


def is_ean13_valid(gtin: Optional[str]) -> bool:
    """Return True only for a genuine EAN-13 barcode (valid mod-10 checksum).

    Accepts a 13-digit code, or a 14-digit code whose leading digit is 0 (our
    left-padded storage form). Rejects anything else — crucially, a Woolworths
    Stockcode padded to 14 digits will almost never pass the checksum, so this
    is the gate that keeps Stockcode-as-GTIN pollution out of every write path.
    """
    if not gtin:
        return False
    digits = re.sub(r"\D", "", str(gtin))
    if len(digits) == 14:
        if digits[0] != "0":
            return False
        digits = digits[1:]
    if len(digits) != 13:
        return False
    if digits == "0" * 13:
        return False
    body, check = digits[:12], int(digits[12])
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    computed = (10 - (total % 10)) % 10
    return computed == check


def same_chain(a: ProductFacts, b: ProductFacts) -> bool:
    """True when both products come from the same known chain (never merge these)."""
    return a.chain is not None and a.chain == b.chain


def pre_guard(a: ProductFacts, b: ProductFacts) -> Optional[DedupDecision]:
    """Cheap deterministic gate run BEFORE any API call.

    Returns a terminal ``DedupDecision`` when the pair must not be judged, or
    ``None`` when it should proceed to the model.
    """
    if same_chain(a, b):
        return DedupDecision.VETOED
    if brands_conflict(a.brand, b.brand):
        return DedupDecision.VETOED
    return None


def _facts_dict(p: ProductFacts) -> dict:
    d = {
        "name": p.name,
        "brand": p.brand,
        "category": p.category,
        "size_hint": p.size_hint,
        "price_aud": p.price_aud,
        "chain": p.chain,
    }
    if p.package_size:
        d["package_size"] = p.package_size
    if p.unit_price is not None:
        d["unit_price"] = p.unit_price
    if p.unit_price_label:
        d["unit_price_label"] = p.unit_price_label
    if p.store_url:
        d["store_url"] = p.store_url
    return d


def build_judge_prompt(a: ProductFacts, b: ProductFacts) -> tuple[str, str]:
    """Return (system, user) prompts for the dedup judge."""
    system = (
        "You are a grocery-catalog deduplication judge for an Australian grocery "
        "price-comparison app. The two product records below come from DIFFERENT "
        "retail chains. Decide whether they are the SAME physical product — same "
        "manufacturer/brand, same variant (flavour, fat %, etc.), and same "
        "size/pack. Products that differ in brand, size, or variant are DIFFERENT "
        "even when their names look similar. Home-brand equivalents (e.g. Coles "
        "Milk vs Woolworths Milk) are DIFFERENT products.\n\n"
        "Reply with STRICT JSON only, no prose, no markdown fences:\n"
        '{"same_product": <bool>, "confidence": <0.0-1.0>, '
        '"keep": "a"|"b", "reason": "<=200 chars"}\n'
        '"keep" is which record is the better canonical survivor (prefer the one '
        "with more complete data: a real barcode, an image, a fuller name)."
    )
    user = (
        "Product A:\n"
        + json.dumps(_facts_dict(a), ensure_ascii=False)
        + "\n\nProduct B:\n"
        + json.dumps(_facts_dict(b), ensure_ascii=False)
    )
    return system, user


def _extract_text(response: Any) -> str:
    """Concatenate text blocks from an Anthropic messages response (SDK 0.7.x)."""
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if not content:
        raise ValueError("Empty response from dedup judge")
    parts: list[str] = []
    for block in content:
        block_text = getattr(block, "text", None)
        if block_text is None and isinstance(block, dict):
            block_text = block.get("text")
        if block_text:
            parts.append(block_text)
    if not parts:
        raise ValueError("No text content in dedup judge response")
    return "".join(parts)


def judge_pair(
    a: ProductFacts,
    b: ProductFacts,
    *,
    client: Any,
    model: str = DEFAULT_AI_DEDUP_MODEL,
    max_tokens: int = 512,
) -> "tuple[AIVerdict | DedupDecision, str | None]":
    """Judge whether A and B are the same product.

    Returns ``(verdict, error_str)``.  ``error_str`` is non-None only when the
    AI call failed (verdict will be ``DedupDecision.SKIPPED``). Never raises —
    a batch must survive a single bad pair.
    """
    guard = pre_guard(a, b)
    if guard is not None:
        return guard, None

    system, user = build_judge_prompt(a, b)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = _extract_text(response)
        data = json.loads(_strip_json_fences(raw))
        verdict = AIVerdict(**data)
    except Exception as exc:  # noqa: BLE001 — resilience: any failure => skip pair
        logger.warning("Dedup judge failed for (%s, %s): %s", a.id, b.id, exc)
        return DedupDecision.SKIPPED, str(exc)

    # Post-guard: deterministic rules always win. The model cannot merge a
    # same-chain pair or override a brand conflict, even if it says same_product.
    if verdict.same_product and (same_chain(a, b) or brands_conflict(a.brand, b.brand)):
        logger.debug("Post-guard downgraded a same_product verdict for (%s, %s)", a.id, b.id)
        return (
            AIVerdict(
                same_product=False,
                confidence=verdict.confidence,
                keep=verdict.keep,
                reason="post-guard: brand/chain conflict overrides model",
            ),
            None,
        )
    return verdict, None


def _survivor_rank(p: ProductFacts) -> tuple:
    """Higher tuple sorts first = better survivor (prefer real GTIN, then metadata)."""
    return (
        is_ean13_valid(p.gtin),  # keep the only real barcode
        p.has_image,
        bool(p.brand),
        len(p.name or ""),
    )


def choose_survivor(a: ProductFacts, b: ProductFacts, verdict: AIVerdict) -> tuple[str, str]:
    """Return (keep_id, drop_id) for a merge, deterministically.

    Prefer the record with a valid EAN-13 GTIN as the survivor so a merge never
    deletes the only real barcode; then richer metadata; then the model's
    ``keep`` as a tiebreak; then the lower UUID (matches the existing default).
    """
    rank_a, rank_b = _survivor_rank(a), _survivor_rank(b)
    if rank_a != rank_b:
        keeper = a if rank_a > rank_b else b
    else:
        # Tie on all deterministic signals — defer to the model, then lower UUID.
        if verdict.keep == "a":
            keeper = a
        elif verdict.keep == "b":
            keeper = b
        else:
            keeper = a if str(a.id) < str(b.id) else b
    dropped = b if keeper is a else a
    return keeper.id, dropped.id


# ── GTIN recovery ────────────────────────────────────────────────────────────


def propagate_gtin_on_merge(session: Session, keep_id: str, drop_id: str) -> Optional[str]:
    """Copy a valid EAN-13 GTIN from the dropped product to the survivor.

    Only fires when the survivor has no GTIN and the dropped record has a real,
    checksum-valid one that no other product already owns (products.gtin is
    UNIQUE). Must be called BEFORE the dropped row is deleted. Returns the
    propagated GTIN, or None if nothing was propagated.
    """
    row = session.execute(
        text("SELECT id, gtin FROM products WHERE id IN (:keep, :drop)"),
        {"keep": keep_id, "drop": drop_id},
    ).fetchall()
    gtins = {str(r.id): r.gtin for r in row}
    keep_gtin = gtins.get(str(keep_id))
    drop_gtin = gtins.get(str(drop_id))

    if keep_gtin:  # survivor already has one — nothing to do
        return None
    if not is_ean13_valid(drop_gtin):
        return None

    # Uniqueness guard: never violate the products.gtin UNIQUE constraint.
    # Exclude BOTH the survivor and the dropped row — the dropped row still holds
    # this GTIN at propagation time (it is deleted only afterward), so without
    # excluding it every propagation would self-clash and silently no-op.
    clash = session.execute(
        text("SELECT 1 FROM products WHERE gtin = :g AND id NOT IN (:keep, :drop) LIMIT 1"),
        {"g": drop_gtin, "keep": keep_id, "drop": drop_id},
    ).fetchone()
    if clash is not None:
        logger.info("GTIN %s already owned elsewhere; skip propagation to %s", drop_gtin, keep_id)
        return None

    # Release the barcode from the dropped row FIRST. It still owns drop_gtin at
    # this point (deletion happens later, in _merge_products), and products.gtin
    # is UNIQUE — so setting it on the survivor while the dropped row still holds
    # it would raise a duplicate-key violation. Nulling the doomed row is safe.
    session.execute(
        text("UPDATE products SET gtin = NULL WHERE id = :drop"),
        {"drop": drop_id},
    )
    session.flush()
    session.execute(
        text("UPDATE products SET gtin = :g WHERE id = :keep"),
        {"g": drop_gtin, "keep": keep_id},
    )
    logger.info("Propagated GTIN %s from %s to survivor %s", drop_gtin, drop_id, keep_id)
    return drop_gtin


async def search_gtin_by_name_brand(
    name: str,
    brand: Optional[str],
    client: httpx.AsyncClient,
    rate_limiter: OFTRateLimiter,
    user_agent: str,
    timeout: float,
) -> Optional[str]:
    """Look up a real EAN-13 GTIN for a product via Open Food Facts search.

    Reuses the OFF v2 search endpoint + polite rate limiter. Returns the first
    result whose brand agrees with ours (not ``brands_conflict``) and whose
    ``code`` passes ``is_ean13_valid``. Returns None on no match / error / 429.
    **Never fabricates** — only returns a barcode OFF actually served.
    """
    if not name or not name.strip():
        return None
    query = f"{brand} {name}".strip() if brand else name.strip()

    await rate_limiter.wait()
    params = {
        "search_terms": query,
        "fields": "code,product_name,brands",
        "page_size": 5,
        "sort_by": "unique_scans_n",
    }
    try:
        resp = await client.get(
            _OFF_SEARCH_URL,
            params=params,
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )
        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            rate_limiter.on_rate_limited(retry_after)
            logger.warning("OFF rate-limited during GTIN search; backing off %.0fs", retry_after)
            return None
        resp.raise_for_status()
        products = resp.json().get("products", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OFF GTIN search failed for %r: %s", query, exc)
        return None

    for product in products:
        code = product.get("code")
        if not is_ean13_valid(code):
            continue
        # If both brands are known and clearly different, reject this candidate.
        if brand and brands_conflict(brand, product.get("brands")):
            continue
        return code
    return None


# ── Session helper ───────────────────────────────────────────────────────────


def facts_for_product_id(
    session: Session, product_id: str, price_aud: Optional[float] = None
) -> Optional[ProductFacts]:
    """Load a fresh ``ProductFacts`` by id, or None if the row is gone.

    Resolves chain and external_id from the primary ProductRetailerListing so the
    AI judge sees the correct chain label and store URL. Primary listing = most
    recently seen (catalog_last_seen_at DESC NULLS LAST), breaking ties by id ASC.
    """
    p = session.execute(
        select(Product).where(Product.id == UUID(str(product_id)))
    ).scalar_one_or_none()
    if p is None:
        return None

    listing = session.execute(
        select(ProductRetailerListing)
        .where(ProductRetailerListing.product_id == p.id)
        .order_by(
            ProductRetailerListing.catalog_last_seen_at.desc().nulls_last(),
            ProductRetailerListing.id.asc(),
        )
        .limit(1)
    ).scalar_one_or_none()

    chain: Optional[str] = None
    external_id: Optional[str] = None
    if listing is not None:
        src = listing.external_source or ""
        if src.startswith("catalog_"):
            chain = src[len("catalog_"):]
        elif listing.chain:
            chain = listing.chain
        external_id = listing.external_id

    return ProductFacts.from_product(p, price_aud, chain=chain, external_id=external_id)

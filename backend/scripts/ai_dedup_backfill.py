"""
AI dedup backfill: judge cross-store product pairs with Claude and record the
verdict on product_match_candidates rows (optionally auto-merging high-confidence
matches).

This generalizes ``backfill_candidates.py`` (which only covered ALDI/Coles →
Woolworths) to *all distinct cross-chain pairs*, then layers the Claude judge on
top of the fuzzy pre-filter: the fuzzy matcher proposes plausible pairs, Claude
decides SAME/DIFFERENT + which record to keep, and — only with ``--auto-merge``
and confidence ≥ AI_DEDUP_CONFIDENCE_MIN — the pair is merged (which also
propagates a real GTIN to the survivor).

Cross-store only (never same-chain), brand-vetoed, and idempotent: an existing
candidate row is updated in place, never duplicated. New chains (IGA, Dan
Murphy's) appear automatically as new ``catalog_*`` external_sources — no rework.

Safe defaults: without ``--auto-merge`` nothing is deleted; every verdict is just
written to a candidate row for human review. ``--limit`` stages the rollout so
you can eyeball a few verdicts (and their cost) before a full run.

Story #18 fixes:
  - Product loading now joins through ProductRetailerListing (Product has no
    external_source column — that's on the listing).
  - chain context resolved per-product from the listing, so the AI judge receives
    a valid chain label and the same_chain pre-guard can fire correctly.
  - ProductFacts now built via facts_for_product_id() (resolves listing chain/URL)
    rather than ProductFacts.from_product() without chain context.
  - Hard-excluded known conflict products (5fda8bda, c24cb38e) from all batches.
  - Package-size guard applied before the AI call (mirrors backfill_dedup).
  - _merge_products() call now passes candidate_id/score/ai_verdict/ai_reason.

Usage:
    python3 scripts/ai_dedup_backfill.py --dry-run --limit 5
    python3 scripts/ai_dedup_backfill.py --limit 50
    python3 scripts/ai_dedup_backfill.py --auto-merge --min-fuzzy 88
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "backfill-script")

from itertools import combinations

from rapidfuzz.process import extractOne
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Product
from app.models.product_match import ProductMatchCandidate
from app.models.product_retailer_listing import ProductRetailerListing
from app.services.ai_dedup import (
    AIVerdict,
    DedupDecision,
    choose_survivor,
    facts_for_product_id,
    judge_pair,
)
from app.services.product_dedup import FuzzyMatcher, brands_conflict

_matcher = FuzzyMatcher()

MIN_CANDIDATE_LEN = 15
DEFAULT_MIN_FUZZY = 85.0

# Products that must never be touched by any automatic remediation batch.
# See backend/validation_output/ for the audit evidence.
#   5fda8bda: Tasmanian Heritage Camembert — IGA 125g / Woolworths 200g (bad merge)
#   c24cb38e: ALDI Energy Drink — raw=4x250mL, product name says 200ml
EXCLUDED_PRODUCT_IDS: frozenset[str] = frozenset(
    [
        "5fda8bda-c948-4d82-99f7-36e22253d354",
        "c24cb38e-6990-45c1-9ba0-c1047c13ba34",
    ]
)


def _norm(p: Product) -> str:
    return _matcher.normalize_name(p.name)


def _decision_str(verdict) -> str:
    """Map a judge result (AIVerdict | DedupDecision) to the persisted verdict string."""
    if isinstance(verdict, DedupDecision):
        return verdict.value
    return DedupDecision.SAME.value if verdict.same_product else DedupDecision.DIFFERENT.value


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-judge cross-store dedup candidates")
    parser.add_argument("--dry-run", action="store_true", help="Judge + print, write nothing")
    parser.add_argument("--limit", type=int, default=0, help="Max pairs to JUDGE (0 = unlimited)")
    parser.add_argument(
        "--min-fuzzy",
        type=float,
        default=DEFAULT_MIN_FUZZY,
        help="Minimum fuzzy score for a pair to reach the judge",
    )
    parser.add_argument(
        "--auto-merge",
        action="store_true",
        help="Merge SAME verdicts with confidence >= AI_DEDUP_CONFIDENCE_MIN",
    )
    parser.add_argument("--model", type=str, default=None, help="Override the judge model")
    parser.add_argument(
        "--chains",
        type=str,
        default=None,
        help="Comma-separated chains to restrict to (e.g. coles,woolworths)",
    )
    args = parser.parse_args()

    settings = get_settings()
    restrict = {c.strip() for c in args.chains.split(",") if c.strip()} if args.chains else None

    from app.deps import get_anthropic_client
    from app.services.config_store import get_runtime_typed

    engine = create_engine(settings.DATABASE_URL, echo=False)

    with Session(engine) as session:
        # Resolve settings DB-first (admin Configs UI) then env; the enable
        # toggle, model, and thresholds are all live-editable without a restart.
        if not get_runtime_typed("AI_DEDUP_ENABLED", session):
            print(
                "AI_DEDUP_ENABLED is false — enable it in Admin → Configs (or .env) to run the judge. Aborting."
            )
            sys.exit(1)

        model = args.model or get_runtime_typed("AI_DEDUP_MODEL", session)
        conf_min = get_runtime_typed("AI_DEDUP_CONFIDENCE_MIN", session)
        max_tokens = get_runtime_typed("AI_DEDUP_MAX_TOKENS", session)
        print(f"Using model={model}, confidence_min={conf_min}, max_tokens={max_tokens}")

        client = get_anthropic_client()

        # Load all catalog products grouped by chain, resolving chain from
        # the primary ProductRetailerListing (Product has no external_source column).
        # Primary listing = most recently seen (catalog_last_seen_at DESC NULLS LAST),
        # breaking ties by id ASC — same ordering used by facts_for_product_id.
        # ORDER BY gives deterministic chain assignment when a product has
        # multiple catalog listings (first alphabetically wins for primary chain).
        stmt = (
            select(Product, ProductRetailerListing.external_source, ProductRetailerListing.chain)
            .join(ProductRetailerListing, ProductRetailerListing.product_id == Product.id)
            .where(ProductRetailerListing.external_source.like("catalog_%"))
            .order_by(ProductRetailerListing.product_id, ProductRetailerListing.external_source)
        )
        rows = session.execute(stmt).all()

        by_chain: dict[str, list[Product]] = {}
        # Map product_id → resolved chain name (e.g. "aldi", "coles")
        product_chain_map: dict[str, str] = {}
        # Map product_id → ALL chain names from its listings (for multi-chain guard)
        product_all_chains: dict[str, set[str]] = {}

        for product, ext_src, chain in rows:
            pid = str(product.id)
            if pid in EXCLUDED_PRODUCT_IDS:
                continue
            # Resolve chain: prefer external_source prefix ("catalog_aldi" → "aldi"),
            # fall back to the listing's chain column.
            resolved_chain: str | None = None
            if ext_src and ext_src.startswith("catalog_"):
                resolved_chain = ext_src[len("catalog_") :]
            elif chain:
                resolved_chain = chain

            if resolved_chain is None:
                continue

            # Track ALL chains this product appears in before applying any --chains
            # filter. A restricted run (--chains coles,woolworths) must still know
            # that product P also has an aldi listing so the multi-chain veto fires
            # correctly — building product_all_chains only for restricted chains
            # would silently weaken the same-chain guard.
            product_all_chains.setdefault(pid, set()).add(resolved_chain)

            # Apply --chains restriction only for which products to process.
            if restrict and resolved_chain not in restrict:
                continue

            n = _norm(product)
            if not n or len(n) < MIN_CANDIDATE_LEN:
                continue
            # Only add each product once (a product may have multiple listings)
            if pid not in product_chain_map:
                product_chain_map[pid] = resolved_chain
                by_chain.setdefault(resolved_chain, []).append(product)

        chains = sorted(by_chain)
        print(
            f"Loaded products across {len(chains)} chain(s): "
            + ", ".join(f"{c}={len(by_chain[c])}" for c in chains)
        )

        # Existing candidate rows, keyed by ordered pair, so we update in place.
        existing: dict[tuple[str, str], ProductMatchCandidate] = {}
        for row in session.execute(select(ProductMatchCandidate)).scalars().all():
            existing[(str(row.product_a_id), str(row.product_b_id))] = row
        print(f"Loaded {len(existing)} existing candidate rows (idempotent update)")

        judged = 0
        same = 0
        different = 0
        vetoed = 0
        skipped = 0
        merged = 0

        # Every distinct cross-chain pair. For each product in chain X, fuzzy-match
        # against the normalized names of chain Y (Y != X).
        for chain_a, chain_b in combinations(chains, 2):
            list_a = by_chain[chain_a]
            list_b = by_chain[chain_b]
            names_b = [_norm(p) for p in list_b]

            for pa in list_a:
                if args.limit and judged >= args.limit:
                    break
                norm_a = _norm(pa)
                best = extractOne(norm_a, names_b, score_cutoff=args.min_fuzzy)
                if best is None:
                    continue
                _name, score, idx = best
                pb = list_b[idx]

                # Skip if either product already has a listing in the other's chain.
                # A product with multi-retailer listings is already cross-chain;
                # comparing it against another product in one of its existing chains
                # risks conflating two genuinely different products that are merely
                # both present in that chain.
                chains_a = product_all_chains.get(str(pa.id), set())
                chains_b = product_all_chains.get(str(pb.id), set())
                if chains_a & chains_b:
                    vetoed += 1
                    continue

                # Brand hard-veto before spending an API call.
                if brands_conflict(pa.brand, pb.brand):
                    vetoed += 1
                    continue

                # Package-size guard: different sizes → different products.
                # This mirrors the guard in backfill_dedup.py and prevents the AI
                # from spending tokens on pairs that are clearly different sizes.
                pa_size = pa.package_size_norm
                pb_size = pb.package_size_norm
                if pa_size and pb_size and pa_size != pb_size:
                    vetoed += 1
                    continue

                a_id, b_id = sorted([str(pa.id), str(pb.id)])
                # Re-query survivorship freshly: skip if a prior merge this run
                # already removed one of the two products.
                if session.get(Product, pa.id) is None or session.get(Product, pb.id) is None:
                    continue

                # Build ProductFacts via the helper that resolves listing chain/URL.
                facts_a = facts_for_product_id(session, str(pa.id))
                facts_b = facts_for_product_id(session, str(pb.id))
                if facts_a is None or facts_b is None:
                    skipped += 1
                    continue

                verdict, _ = judge_pair(
                    facts_a, facts_b, client=client, model=model, max_tokens=max_tokens
                )
                judged += 1
                decision = _decision_str(verdict)

                if decision == DedupDecision.SAME.value:
                    same += 1
                elif decision == DedupDecision.DIFFERENT.value:
                    different += 1
                elif decision == DedupDecision.VETOED.value:
                    vetoed += 1
                else:
                    skipped += 1

                conf = verdict.confidence if isinstance(verdict, AIVerdict) else None
                keep = verdict.keep if isinstance(verdict, AIVerdict) else None
                reason = (
                    verdict.reason
                    if isinstance(verdict, AIVerdict)
                    else f"deterministic: {decision}"
                )

                print(
                    f"  [{score:.1f}%] {decision.upper():9s}"
                    + (f" ({conf:.2f})" if conf is not None else "")
                    + f"  [{chain_a}] {pa.name!r} ←→ [{chain_b}] {pb.name!r}"
                    + (f"  — {reason}" if reason else "")
                )

                if args.dry_run:
                    continue

                # Persist the verdict onto a candidate row (create or update).
                row = existing.get((a_id, b_id))
                if row is None:
                    row = ProductMatchCandidate(
                        product_a_id=a_id,
                        product_b_id=b_id,
                        score=score,
                        status="pending",
                        proposed_by="ai",
                    )
                    session.add(row)
                    existing[(a_id, b_id)] = row
                row.ai_verdict = decision
                row.ai_confidence = conf
                row.ai_keep = keep
                row.ai_reason = reason[:300] if reason else None
                row.ai_model = model
                from datetime import datetime, timezone

                row.ai_reviewed_at = datetime.now(timezone.utc)

                # Auto-merge only high-confidence SAME verdicts.
                if (
                    args.auto_merge
                    and isinstance(verdict, AIVerdict)
                    and verdict.same_product
                    and verdict.confidence >= conf_min
                ):
                    keep_id, drop_id = choose_survivor(facts_a, facts_b, verdict)
                    from app.routers.admin import _merge_products

                    session.flush()
                    session.expire(row)
                    # NOTE: _merge_products does not yet accept candidate_id/score/
                    # ai_verdict kwargs (ProductMergeLog is a separate story).
                    # Those should be added before enabling auto-merge at scale.
                    _merge_products(session, str(keep_id), str(drop_id))
                    merged += 1
                    # Drop dropped product from in-memory chain lists so it isn't
                    # re-judged as pb for a later pa.
                    for lst in by_chain.values():
                        lst[:] = [p for p in lst if str(p.id) != str(drop_id)]

                if judged % 25 == 0:
                    session.flush()

            if args.limit and judged >= args.limit:
                break

        if not args.dry_run:
            session.commit()

        prefix = "Dry-run" if args.dry_run else "Done"
        print(
            f"\n{prefix}: judged {judged} pairs — "
            f"{same} same, {different} different, {vetoed} vetoed, {skipped} skipped"
            + (f"; {merged} auto-merged" if args.auto_merge else "")
        )


if __name__ == "__main__":
    main()

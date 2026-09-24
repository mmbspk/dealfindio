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
from app.services.ai_dedup import (
    DedupDecision,
    ProductFacts,
    AIVerdict,
    choose_survivor,
    judge_pair,
)
from app.services.product_dedup import FuzzyMatcher, brands_conflict

_matcher = FuzzyMatcher()

MIN_CANDIDATE_LEN = 15
DEFAULT_MIN_FUZZY = 85.0


def _chain_of(p: Product) -> str | None:
    src = p.external_source or ""
    return src[len("catalog_"):] if src.startswith("catalog_") else None


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
    parser.add_argument("--min-fuzzy", type=float, default=DEFAULT_MIN_FUZZY,
                        help="Minimum fuzzy score for a pair to reach the judge")
    parser.add_argument("--auto-merge", action="store_true",
                        help="Merge SAME verdicts with confidence >= AI_DEDUP_CONFIDENCE_MIN")
    parser.add_argument("--model", type=str, default=None, help="Override the judge model")
    parser.add_argument("--chains", type=str, default=None,
                        help="Comma-separated chains to restrict to (e.g. coles,woolworths)")
    args = parser.parse_args()

    settings = get_settings()
    restrict = (
        {c.strip() for c in args.chains.split(",") if c.strip()} if args.chains else None
    )

    from app.deps import get_anthropic_client
    from app.services.config_store import get_runtime_typed

    engine = create_engine(settings.DATABASE_URL, echo=False)

    with Session(engine) as session:
        # Resolve settings DB-first (admin Configs UI) then env; the enable
        # toggle, model, and thresholds are all live-editable without a restart.
        if not get_runtime_typed("AI_DEDUP_ENABLED", session):
            print("AI_DEDUP_ENABLED is false — enable it in Admin → Configs (or .env) to run the judge. Aborting.")
            sys.exit(1)

        model = args.model or get_runtime_typed("AI_DEDUP_MODEL", session)
        conf_min = get_runtime_typed("AI_DEDUP_CONFIDENCE_MIN", session)
        max_tokens = get_runtime_typed("AI_DEDUP_MAX_TOKENS", session)
        print(f"Using model={model}, confidence_min={conf_min}, max_tokens={max_tokens}")

        client = get_anthropic_client()

        # Load all catalog products grouped by chain (skip chain-less rows).
        products = list(session.execute(
            select(Product).where(Product.external_source.like("catalog_%"))
        ).scalars().all())
        by_chain: dict[str, list[Product]] = {}
        for p in products:
            chain = _chain_of(p)
            if chain is None:
                continue
            if restrict and chain not in restrict:
                continue
            n = _norm(p)
            if not n or len(n) < MIN_CANDIDATE_LEN:
                continue
            by_chain.setdefault(chain, []).append(p)

        chains = sorted(by_chain)
        print(f"Loaded products across {len(chains)} chain(s): "
              + ", ".join(f"{c}={len(by_chain[c])}" for c in chains))

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

                # Brand hard-veto before spending an API call.
                if brands_conflict(pa.brand, pb.brand):
                    vetoed += 1
                    continue

                a_id, b_id = sorted([str(pa.id), str(pb.id)])
                # Re-query survivorship freshly: skip if a prior merge this run
                # already removed one of the two products.
                if session.get(Product, pa.id) is None or session.get(Product, pb.id) is None:
                    continue

                facts_a = ProductFacts.from_product(pa)
                facts_b = ProductFacts.from_product(pb)

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
                    verdict.reason if isinstance(verdict, AIVerdict)
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

"""
Read-only remediation planner — Story #103.

Extends the catalog integrity audit (Story #101) with precise, evidence-based
classification of package-size anomalies and produces a machine-readable
manifest for approval-gated correction.

Seven finding subtypes replace the earlier binary class-A / class-B:

  confirmed_norm_mismatch  raw per-unit size and count explicitly contradict
                           the stored canonical norm (e.g. raw "375mL x 6 pack"
                           → "6x375mL", stored "375mL").
  recoverable_from_raw     raw normalises to a different single-unit norm
                           (e.g. "1500mL" stored vs raw "1.5L"); no multipack
                           ambiguity.
  recoverable_from_name    name has unambiguous volume-based per-unit size and
                           count; raw alone is insufficient.
  count_only               pack count in name but no per-unit size anywhere;
                           cannot produce a norm.
  total_package_weight     name has a pack count and a weight unit; the weight
                           may be the entire pack, not per-item.
  conflicting_evidence     multiple listings on the same canonical product
                           disagree about the package size.
  insufficient_evidence    some signal exists but is too weak to propose a
                           change safely.

Approval statuses in the manifest:

  safe_candidate           strong, consistent evidence — still requires explicit
                           human approval before any write.
  manual_review            conflicting or ambiguous — requires human judgment.
  insufficient_evidence    cannot justify a correction.

Design constraints:

  * Read-only.  No database writes, no automatic approvals.
  * Pass DATABASE_URL as an environment variable — it is never logged.
  * Deterministic: same inputs always produce the same manifest.
  * All listings for a product are examined before a classification is issued,
    so a cross-chain conflict can never be silently approved.
  * "1 Pack" labels (single-unit retail convention) are excluded from multipack
    detection to reduce false positives.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/remediation_planner.py
    DATABASE_URL=postgresql://... python3 scripts/remediation_planner.py \\
        --out-dir /tmp/plan --chains woolworths --limit 500
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "remediation-planner")

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_retailer_listing import ProductRetailerListing
from app.services.package_size import normalize_package_size

# Re-use shared helpers from the audit script.
from scripts.audit_package_sizes import (
    BATCH_SIZE,
    _get_auditable_product_ids,
    _gtin_check_digit_valid,
    _is_multipack_name,
    _is_multipack_norm,
    _sanitize_csv_cell,
)

# ── approval status constants ─────────────────────────────────────────────────

APPROVAL_SAFE = "safe_candidate"
APPROVAL_MANUAL = "manual_review"
APPROVAL_INSUFFICIENT = "insufficient_evidence"

# ── detection helpers ─────────────────────────────────────────────────────────

# Captures individual pack-count tokens, e.g. the "6" in "6 pack" or "6pk".
_PACK_COUNT_TOKEN_RE = re.compile(
    r"\b(\d+)\s*(?:pack|pk|ct|count|pcs?|pieces?)\b",
    re.I,
)

# Weight-based multipack inner-unit pattern, e.g. "8x560g", "4x1.5kg".
_WEIGHT_MULTIPACK_RE = re.compile(r"^\d+x\d+(?:\.\d+)?(?:g|kg)$", re.I)


def _is_genuine_multipack_name(text: str | None) -> bool:
    """True when *text* has a multipack signal with count > 1.

    Excludes '1 Pack' / '1 pk' retail labeling conventions that mark single
    units rather than genuine multipacks.
    """
    if not text:
        return False
    if not _is_multipack_name(text):
        return False
    # NxM cross-pattern (e.g. "6 x 375mL") — count is embedded in the pattern.
    if re.search(r"\b\d+\s*[xX×]\s*\d+", text, re.I):
        return True
    if re.search(r"multipack", text, re.I):
        return True
    # "N pack" tokens — genuine only when at least one N > 1.
    counts = [int(m) for m in _PACK_COUNT_TOKEN_RE.findall(text)]
    return any(c > 1 for c in counts)


def _norm_is_weight_based(norm: str) -> bool:
    """True if the per-unit component of a multipack norm uses weight (g/kg).

    Weight-based multipacks are classified as 'total_package_weight' because the
    weight may represent the entire pack rather than a per-item quantity.
    Example: '8x560g' could mean 8 sausages, each 560g OR total 560g.
    """
    return bool(_WEIGHT_MULTIPACK_RE.match(norm))


def _fmt_conflict_norms(evidence_pairs: list[tuple]) -> str:
    """Pipe-separated 'chain:norm' string for conflict reporting."""
    seen: set[str] = set()
    parts = []
    for lst, norm in evidence_pairs:
        key = f"{lst.chain}:{norm}"
        if key not in seen:
            parts.append(key)
            seen.add(key)
    return "|".join(parts)


# ── data loading ──────────────────────────────────────────────────────────────


def _load_batch_unfiltered(
    session: Session,
    product_ids: list,
) -> tuple[dict, dict]:
    """Load products and ALL their listings for one batch.

    Unlike the audit's ``_load_batch``, this never applies a chain filter so
    we always see every listing attached to a product — required to detect
    cross-chain conflicts.
    """
    products = session.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()
    products_by_id = {str(p.id): p for p in products}

    listings = (
        session.execute(
            select(ProductRetailerListing).where(ProductRetailerListing.product_id.in_(product_ids))
        )
        .scalars()
        .all()
    )

    listings_by_product_id: dict[str, list] = defaultdict(list)
    for lst in listings:
        listings_by_product_id[str(lst.product_id)].append(lst)

    return products_by_id, dict(listings_by_product_id)


# ── core classification ───────────────────────────────────────────────────────


def classify_product(
    product: Product,
    all_listings: list[ProductRetailerListing],
) -> dict | None:
    """Classify a potential package-size anomaly for one canonical product.

    Examines every listing attached to the product (not just those from one
    chain).  Returns a classification dict describing the finding, or None if
    no anomaly is detected.

    Classification priority (highest confidence first):
      1. Raw explicitly encodes a multipack → confirmed_norm_mismatch
      2. Raw differs from stored norm (single-unit) → recoverable_from_raw
      3. Name has volume-based count+size → recoverable_from_name
      4. Name has weight-based count+size → total_package_weight
      5. Name has count, no size anywhere → count_only
      6. Multiple listings disagree → conflicting_evidence (may promote any above)
      7. No actionable evidence → None (no finding)

    Dict keys: classification, proposed_norm, confidence, approval_status,
    justification, evidence_chain, evidence_listing_id, evidence_raw,
    evidence_name, conflict_listing_norms.
    """
    stored_norm = product.package_size_norm

    # ── collect evidence ──────────────────────────────────────────────────────

    raw_multipack: list[tuple] = []  # (listing, norm) from raw → multipack
    raw_single: list[tuple] = []  # (listing, norm) from raw → any single-unit norm
    name_vol_multi: list[tuple] = []  # (listing, norm) from name → volume multipack
    name_wt_multi: list[tuple] = []  # (listing, norm) from name → weight multipack
    name_count_only: list = []  # listing: name has count, no extractable size

    for lst in all_listings:
        raw_norm = normalize_package_size(lst.package_size_raw) if lst.package_size_raw else None
        if raw_norm:
            if _is_multipack_norm(raw_norm):
                raw_multipack.append((lst, raw_norm))
            else:
                raw_single.append((lst, raw_norm))

        name_text = lst.external_name or ""
        if not _is_genuine_multipack_name(name_text):
            continue

        name_norm = normalize_package_size(name_text)
        if name_norm and _is_multipack_norm(name_norm):
            if _norm_is_weight_based(name_norm):
                name_wt_multi.append((lst, name_norm))
            else:
                name_vol_multi.append((lst, name_norm))
        else:
            name_count_only.append(lst)

    # No signals at all → no finding.
    if not (raw_multipack or raw_single or name_vol_multi or name_wt_multi or name_count_only):
        return None

    # ── conflict detection ────────────────────────────────────────────────────
    # Any disagreement among parseable raw norms triggers a conflict — this
    # catches multipack-vs-single disagreements AND same-type norms that differ
    # (e.g. Woolworths 200g vs IGA 125g on the same canonical product row).
    all_parseable_raws = raw_multipack + raw_single
    distinct_raw_norms = {n for _, n in all_parseable_raws}

    if len(distinct_raw_norms) > 1:
        best_evidence = all_parseable_raws[0][0]
        return {
            "classification": "conflicting_evidence",
            "proposed_norm": None,
            "confidence": "low",
            "approval_status": APPROVAL_MANUAL,
            "justification": (f"Listings have conflicting raw norms: {sorted(distinct_raw_norms)}"),
            "evidence_chain": best_evidence.chain,
            "evidence_listing_id": best_evidence.external_id,
            "evidence_raw": best_evidence.package_size_raw or "",
            "evidence_name": best_evidence.external_name or "",
            "conflict_listing_norms": _fmt_conflict_norms(all_parseable_raws),
        }

    # Products already showing a multipack norm: check consistency with listing
    # raws.  If every parseable raw matches stored → clean.  If any raw diverges
    # (e.g. stored "6x375mL" but a listing reports "12x375mL") → flag conflict.
    if _is_multipack_norm(stored_norm):
        differing = [(lst, n) for lst, n in all_parseable_raws if n != stored_norm]
        if differing:
            return {
                "classification": "conflicting_evidence",
                "proposed_norm": None,
                "confidence": "low",
                "approval_status": APPROVAL_MANUAL,
                "justification": (
                    f"Stored multipack '{stored_norm}' but listing raw(s) give:"
                    f" {sorted({n for _, n in differing})}"
                ),
                "evidence_chain": differing[0][0].chain,
                "evidence_listing_id": differing[0][0].external_id,
                "evidence_raw": differing[0][0].package_size_raw or "",
                "evidence_name": differing[0][0].external_name or "",
                "conflict_listing_norms": _fmt_conflict_norms(differing),
            }
        return None

    # ── primary: raw → multipack ──────────────────────────────────────────────
    # No inter-listing conflict; stored_norm is single-unit (or None).
    if raw_multipack:
        norm_votes = Counter(n for _, n in raw_multipack)
        proposed_norm = norm_votes.most_common(1)[0][0]
        best_evidence = next(lst for lst, n in raw_multipack if n == proposed_norm)

        # When name evidence suggests a different multipack norm than raw evidence,
        # treat the disagreement as a conflict rather than auto-approving raw.
        name_proposed_vol = {n for _, n in name_vol_multi}
        if name_proposed_vol and proposed_norm not in name_proposed_vol:
            all_conflict = raw_multipack + list(name_vol_multi)
            return {
                "classification": "conflicting_evidence",
                "proposed_norm": None,
                "confidence": "low",
                "approval_status": APPROVAL_MANUAL,
                "justification": (
                    f"Raw norm '{proposed_norm}' contradicted by name evidence"
                    f" {sorted(name_proposed_vol)}"
                ),
                "evidence_chain": best_evidence.chain,
                "evidence_listing_id": best_evidence.external_id,
                "evidence_raw": best_evidence.package_size_raw or "",
                "evidence_name": best_evidence.external_name or "",
                "conflict_listing_norms": _fmt_conflict_norms(all_conflict),
            }

        if proposed_norm == stored_norm:
            # Raw already matches stored — check if name evidence conflicts.
            name_proposed = {n for _, n in name_vol_multi + name_wt_multi}
            if not name_proposed or proposed_norm in name_proposed:
                return None  # everything consistent, no finding

        return {
            "classification": "confirmed_norm_mismatch",
            "proposed_norm": proposed_norm,
            "confidence": "high",
            "approval_status": APPROVAL_SAFE,
            "justification": (
                f"raw '{best_evidence.package_size_raw}' → '{proposed_norm}'"
                + (f" (stored '{stored_norm}')" if stored_norm else " (stored None)")
            ),
            "evidence_chain": best_evidence.chain,
            "evidence_listing_id": best_evidence.external_id,
            "evidence_raw": best_evidence.package_size_raw or "",
            "evidence_name": best_evidence.external_name or "",
            "conflict_listing_norms": "",
        }

    # ── secondary: raw → different single norm ────────────────────────────────
    # Only consider single-unit raw norms that differ from stored norm.
    raw_single_mismatch = [(lst, n) for lst, n in raw_single if stored_norm and n != stored_norm]
    if raw_single_mismatch:
        # Most common proposed norm from raw (prefer most recent listing data).
        norm_votes = Counter(n for _, n in raw_single_mismatch)
        proposed_norm = norm_votes.most_common(1)[0][0]
        best_evidence = next(lst for lst, n in raw_single_mismatch if n == proposed_norm)

        return {
            "classification": "recoverable_from_raw",
            "proposed_norm": proposed_norm,
            "confidence": "high",
            "approval_status": APPROVAL_SAFE,
            "justification": (
                f"raw '{best_evidence.package_size_raw}' → '{proposed_norm}'"
                + (f" ≠ stored '{stored_norm}'" if stored_norm else " (stored None)")
            ),
            "evidence_chain": best_evidence.chain,
            "evidence_listing_id": best_evidence.external_id,
            "evidence_raw": best_evidence.package_size_raw or "",
            "evidence_name": best_evidence.external_name or "",
            "conflict_listing_norms": _fmt_conflict_norms(raw_single_mismatch),
        }

    # ── tertiary: name → volume-based multipack ───────────────────────────────
    if name_vol_multi:
        norm_votes = Counter(n for _, n in name_vol_multi)
        proposed_norm = norm_votes.most_common(1)[0][0]
        best_evidence = next(lst for lst, n in name_vol_multi if n == proposed_norm)

        if proposed_norm == stored_norm:
            return None

        return {
            "classification": "recoverable_from_name",
            "proposed_norm": proposed_norm,
            "confidence": "medium",
            "approval_status": APPROVAL_SAFE,
            "justification": (
                f"name '{(best_evidence.external_name or '')[:80]}' → '{proposed_norm}'"
                + (f" (stored '{stored_norm}')" if stored_norm else "")
            ),
            "evidence_chain": best_evidence.chain,
            "evidence_listing_id": best_evidence.external_id,
            "evidence_raw": best_evidence.package_size_raw or "",
            "evidence_name": best_evidence.external_name or "",
            "conflict_listing_norms": "",
        }

    # ── quaternary: name → weight-based multipack ─────────────────────────────
    if name_wt_multi:
        lst, candidate_norm = name_wt_multi[0]
        return {
            "classification": "total_package_weight",
            "proposed_norm": None,
            "confidence": "low",
            "approval_status": APPROVAL_MANUAL,
            "justification": (
                f"name has weight-based count '{(lst.external_name or '')[:80]}'"
                f" → candidate '{candidate_norm}' (may be total pack weight)"
            ),
            "evidence_chain": lst.chain,
            "evidence_listing_id": lst.external_id,
            "evidence_raw": lst.package_size_raw or "",
            "evidence_name": lst.external_name or "",
            "conflict_listing_norms": "",
        }

    # ── quinary: name → count only ────────────────────────────────────────────
    if not name_count_only:
        return None

    lst = name_count_only[0]
    return {
        "classification": "count_only",
        "proposed_norm": None,
        "confidence": "low",
        "approval_status": APPROVAL_INSUFFICIENT,
        "justification": (
            f"name signals pack count but no per-unit size: " f"'{(lst.external_name or '')[:80]}'"
        ),
        "evidence_chain": lst.chain,
        "evidence_listing_id": lst.external_id,
        "evidence_raw": lst.package_size_raw or "",
        "evidence_name": lst.external_name or "",
        "conflict_listing_norms": "",
    }


# ── manifest row assembly ─────────────────────────────────────────────────────

_MANIFEST_FIELDS = [
    "product_id",
    "product_name",
    "product_gtin",
    "gtin_valid",
    "current_norm",
    "proposed_norm",
    "classification",
    "confidence",
    "approval_status",
    "justification",
    "evidence_chain",
    "evidence_listing_id",
    "evidence_raw",
    "evidence_name",
    "all_raw_norms",
    "conflict_listing_norms",
    "remediation_action",
]


def _to_manifest_row(
    product: Product,
    cls: dict,
    all_listings: list[ProductRetailerListing],
) -> dict:
    """Build one manifest row from a product and its classification dict."""
    # Collect unique raw norms across all listings for evidence context.
    raw_norms: list[str] = []
    seen_norms: set[str] = set()
    for lst in all_listings:
        n = normalize_package_size(lst.package_size_raw) if lst.package_size_raw else None
        if n and n not in seen_norms:
            raw_norms.append(f"{lst.chain}:{n}")
            seen_norms.add(n)

    gtin = product.gtin or ""
    gtin_valid = ""
    if gtin:
        gtin_valid = "yes" if _gtin_check_digit_valid(gtin) else "no"

    # Remediation action guidance based on classification + approval status.
    action_map = {
        ("confirmed_norm_mismatch", APPROVAL_SAFE): "recompute",
        ("recoverable_from_raw", APPROVAL_SAFE): "recompute",
        ("recoverable_from_name", APPROVAL_SAFE): "source_recovery_or_rescrape",
        ("conflicting_evidence", APPROVAL_MANUAL): "manual_unmerge_or_split",
        ("total_package_weight", APPROVAL_MANUAL): "manual_verify_then_update",
        ("count_only", APPROVAL_INSUFFICIENT): "none",
        ("insufficient_evidence", APPROVAL_INSUFFICIENT): "none",
    }
    action = action_map.get(
        (cls["classification"], cls["approval_status"]),
        "manual_review",
    )

    return {
        "product_id": str(product.id),
        "product_name": product.name,
        "product_gtin": gtin,
        "gtin_valid": gtin_valid,
        "current_norm": product.package_size_norm or "",
        "proposed_norm": cls["proposed_norm"] or "",
        "classification": cls["classification"],
        "confidence": cls["confidence"],
        "approval_status": cls["approval_status"],
        "justification": cls["justification"],
        "evidence_chain": cls["evidence_chain"],
        "evidence_listing_id": cls["evidence_listing_id"],
        "evidence_raw": cls["evidence_raw"],
        "evidence_name": cls["evidence_name"],
        "all_raw_norms": "|".join(raw_norms),
        "conflict_listing_norms": cls["conflict_listing_norms"],
        "remediation_action": action,
    }


# ── main runner ───────────────────────────────────────────────────────────────


def run_plan(
    session: Session,
    chains: set[str] | None = None,
    limit: int = 0,
    batch_size: int = BATCH_SIZE,
) -> list[dict]:
    """Run the remediation planner and return manifest rows.

    Each row represents one canonical product with a detected anomaly.
    The session is used read-only.

    When *chains* is given the product scope is restricted to products that
    have at least one listing in those chains — but ALL listings for each
    in-scope product are loaded and examined for cross-chain conflicts.

    *limit* caps the number of products examined (0 = unlimited; development
    use only).  *batch_size* controls round-trip size (default: 10 000).
    """
    product_ids = _get_auditable_product_ids(session, chains)
    if limit:
        product_ids = product_ids[:limit]

    rows: list[dict] = []

    for start in range(0, max(len(product_ids), 1), batch_size):
        batch = product_ids[start : start + batch_size]
        if not batch:
            break
        # Always load ALL listings regardless of chain filter so cross-chain
        # conflicts are visible.
        products_by_id, listings_by_pid = _load_batch_unfiltered(session, batch)

        for pid_str, product in products_by_id.items():
            all_listings = listings_by_pid.get(pid_str, [])
            cls = classify_product(product, all_listings)
            if cls is None:
                continue
            rows.append(_to_manifest_row(product, cls, all_listings))

    return rows


# ── output helpers ────────────────────────────────────────────────────────────


def _write_manifest_csv(out_dir: Path, rows: list[dict]) -> Path:
    path = out_dir / "remediation_manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {k: _sanitize_csv_cell(v) if isinstance(v, str) else v for k, v in row.items()}
            )
    return path


def _write_plan_summary(
    out_dir: Path,
    rows: list[dict],
    run_at: str,
    chains_filter: str | None,
) -> None:
    """Write a human-readable Markdown summary of the manifest."""
    by_class: Counter = Counter(r["classification"] for r in rows)
    by_approval: Counter = Counter(r["approval_status"] for r in rows)
    by_chain: Counter = Counter(r["evidence_chain"] for r in rows)
    by_action: Counter = Counter(r["remediation_action"] for r in rows)

    # Safe candidates: products where a proposed_norm is available.
    safe_with_proposed = [
        r for r in rows if r["approval_status"] == APPROVAL_SAFE and r["proposed_norm"]
    ]

    lines = [
        "# Catalog Package-Size Remediation Plan",
        "",
        f"Generated: {run_at}",
        f"Chains filter: {chains_filter or '(all chains)'}",
        f"Total findings: {len(rows)}",
        "",
        "## Findings by classification",
        "",
        "| Classification | Count |",
        "| --- | ---: |",
    ]
    cls_order = [
        "confirmed_norm_mismatch",
        "recoverable_from_raw",
        "recoverable_from_name",
        "total_package_weight",
        "count_only",
        "conflicting_evidence",
        "insufficient_evidence",
    ]
    for c in cls_order:
        if by_class[c]:
            lines.append(f"| {c} | {by_class[c]} |")

    lines += [
        "",
        "## Findings by approval status",
        "",
        "| Status | Count |",
        "| --- | ---: |",
        f"| safe_candidate | {by_approval[APPROVAL_SAFE]} |",
        f"| manual_review | {by_approval[APPROVAL_MANUAL]} |",
        f"| insufficient_evidence | {by_approval[APPROVAL_INSUFFICIENT]} |",
        "",
        "## Findings by chain (evidence chain)",
        "",
        "| Chain | Count |",
        "| --- | ---: |",
    ]
    for chain, cnt in by_chain.most_common():
        lines.append(f"| {chain} | {cnt} |")

    lines += [
        "",
        "## Remediation actions",
        "",
        "| Action | Count |",
        "| --- | ---: |",
    ]
    for action, cnt in by_action.most_common():
        lines.append(f"| {action} | {cnt} |")

    lines += [
        "",
        "## Safe candidates preview",
        "",
        f"{len(safe_with_proposed)} products classified as `safe_candidate` with a proposed norm.",
        "All require **explicit approval** before any database write.",
        "",
        "| Product | Current | Proposed | Chain | Confidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in safe_with_proposed[:20]:
        name = r["product_name"][:50]
        lines.append(
            f"| {name} | {r['current_norm'] or '—'} | {r['proposed_norm']} "
            f"| {r['evidence_chain']} | {r['confidence']} |"
        )
    if len(safe_with_proposed) > 20:
        lines.append(f"| … ({len(safe_with_proposed) - 20} more) | | | | |")

    lines += [
        "",
        "## Remediation safety design",
        "",
        "The following constraints govern any future correction executor:",
        "",
        "1. **Dry-run by default** — no changes without an explicit `--approve` flag.",
        "2. **Selected-product approval** — each product_id must be explicitly listed",
        "   in the approval input; batch-all is not allowed.",
        "3. **Idempotent operations** — applying the same correction twice is safe.",
        "4. **Transaction + rollback** — every write occurs inside a BEGIN/COMMIT; a",
        "   failure at any point rolls back the entire batch.",
        "5. **Pre-write revalidation** — immediately before any approved update,",
        "   re-read the product and its listings to confirm the evidence still holds.",
        "   If the raw has changed since the manifest was generated, abort that row.",
        "6. **No listing reassignment** — the executor MUST NOT move a listing from",
        "   one canonical product to another.",
        "7. **No automatic merges or splits** — `conflicting_evidence` rows require",
        "   human action via the admin UI.",
        "8. **Preserve price history** — `price_reports`, `watchlist_items`, and",
        "   `predictions` are never touched; only `package_size_norm` changes.",
        "9. **Concurrent-ingest protection** — wrap approved writes in a",
        "   `SELECT … FOR UPDATE` on the product row to block concurrent catalog",
        "   ingestion from overwriting the correction immediately.",
        "10. **Audit trail** — log the before/after norm and the manifest row ID for",
        "    every write, keyed by product_id and run timestamp.",
        "",
        "### Handling canonical norm changes with disagreeing listings",
        "",
        "When `package_size_norm` is corrected from X to Y and a listing's raw still",
        "normalises to X, the listing is NOT automatically updated.  The listing's",
        "`package_size_raw` holds the authoritative per-chain evidence; a subsequent",
        "catalog ingest for that chain will re-derive the canonical norm from all",
        "listings and may overwrite the correction.  To make a correction durable:",
        "  a) correct the source listing's `package_size_raw` to match the new norm",
        "     (requires a fresh catalog scrape or a manual raw-field update), OR",
        "  b) accept that the norm will be re-derived correctly on the next ingest",
        "     because the updated normaliser handles the raw value (Category 2 case).",
        "",
        "## Output files",
        "",
        "- `remediation_manifest.csv` — full manifest, one row per finding",
        "- `plan_summary.md` — this file",
    ]

    (out_dir / "plan_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only remediation planner — Story #103")
    parser.add_argument(
        "--out-dir",
        default="remediation_output",
        help="Output directory for manifest and summary (created if absent)",
    )
    parser.add_argument(
        "--chains",
        default=None,
        help="Comma-separated chain names to restrict scope (default: all chains)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max products to examine (0 = unlimited; for development only)",
    )
    args = parser.parse_args()

    chains: set[str] | None = None
    if args.chains:
        chains = {c.strip() for c in args.chains.split(",") if c.strip()}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(db_url, echo=False)

    # Enforce read-only at the PostgreSQL driver level — accidental writes
    # are rejected by the database itself, not merely by convention.
    @event.listens_for(engine, "connect")
    def _set_readonly(dbapi_conn, _record):
        if hasattr(dbapi_conn, "set_session"):  # psycopg2
            dbapi_conn.set_session(readonly=True)

    with Session(engine) as session:
        if engine.dialect.name == "postgresql":
            session.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        print("Running remediation planner…")
        rows = run_plan(session, chains=chains, limit=args.limit)

    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    chains_filter = ", ".join(sorted(chains)) if chains else None

    csv_path = _write_manifest_csv(out_dir, rows)
    _write_plan_summary(out_dir, rows, run_at, chains_filter)

    by_approval: Counter = Counter(r["approval_status"] for r in rows)
    print(f"  Total findings:      {len(rows)}")
    print(f"  safe_candidate:      {by_approval[APPROVAL_SAFE]}")
    print(f"  manual_review:       {by_approval[APPROVAL_MANUAL]}")
    print(f"  insufficient_evid:   {by_approval[APPROVAL_INSUFFICIENT]}")
    print(f"\nManifest: {csv_path}")
    print(f"Summary:  {out_dir / 'plan_summary.md'}")


if __name__ == "__main__":
    main()

"""
Approval-gated catalog remediation executor — Story #105.

Applies package_size_norm corrections to canonical products that the
remediation planner (Story #103) classified as
  classification   = confirmed_norm_mismatch
  approval_status  = safe_candidate
  remediation_action = recompute

Safety contract (invariants enforced at runtime, not just by convention):

  1. DRY-RUN BY DEFAULT — no database writes without --approve.
  2. EXPLICIT APPROVAL — --approve requires --approved-ids <file>.
     There is no "approve all" mode.
  3. APPROVAL INTEGRITY — the approved-ids file must not contain duplicate
     product IDs, and every ID must appear in the eligible manifest rows.
  4. STALE-PLAN GUARD — after locking the product row, the executor verifies
     that its current package_size_norm still matches the current_norm recorded
     in the manifest.  A changed norm indicates something acted on this product
     since planning; such rows are skipped and require a fresh plan cycle.
  5. LIVE RECLASSIFICATION — under the write transaction the executor re-fetches
     all current listings and re-runs classify_product().  It proceeds only when
     the live classification is still confirmed_norm_mismatch / safe_candidate
     and the proposed norm still matches the approved manifest value.  This
     catches a new conflicting listing added by a concurrent catalog ingest.
  6. SELECT FOR UPDATE — the product row is locked for the duration of the
     write transaction to prevent concurrent catalog ingestion from overwriting
     the correction immediately.
  7. TRANSACTION + ROLLBACK — every batch runs inside BEGIN/COMMIT; an
     unexpected error triggers a full batch rollback.
  8. IDEMPOTENT — if a product's norm already equals proposed_norm it is
     counted as "already correct" and skipped without writing a log record.
  9. NO LISTING MUTATIONS — only product.package_size_norm is written.
     Listings, price_reports, watchlist_items, and predictions are untouched.
  10. AUDIT TRAIL — every corrected product gets an immutable
      package_size_remediation_log row (before_norm, after_norm, batch_id, …).
  11. PHASE 1 SCOPE — only confirmed_norm_mismatch / safe_candidate / recompute
      rows from the manifest are considered.

Usage:
    # Dry-run (safe, no writes):
    DATABASE_URL=postgresql://... python3 scripts/remediation_executor.py \\
        --manifest remediation_output/20261021/remediation_manifest.csv

    # Approve a reviewed subset:
    DATABASE_URL=postgresql://... python3 scripts/remediation_executor.py \\
        --manifest remediation_output/20261021/remediation_manifest.csv \\
        --approved-ids /tmp/reviewed_batch.txt \\
        --approve \\
        --approved-by "bilal@example.com"

    # Format of --approved-ids file: one product UUID per line, blank lines ignored,
    # no duplicates allowed.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "remediation-executor")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.package_size_remediation_log import PackageSizeRemediationLog
from app.models.product import Product
from app.models.product_retailer_listing import ProductRetailerListing
from scripts.remediation_planner import APPROVAL_SAFE, classify_product

# ── phase 1 filter constants ──────────────────────────────────────────────────

_ELIGIBLE_CLASSIFICATION = "confirmed_norm_mismatch"
_ELIGIBLE_APPROVAL = "safe_candidate"
_ELIGIBLE_ACTION = "recompute"


# ── manifest loading ──────────────────────────────────────────────────────────


def _load_manifest(path: Path) -> list[dict]:
    """Load CSV manifest rows and filter to Phase 1 eligibles."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (
                row["classification"] == _ELIGIBLE_CLASSIFICATION
                and row["approval_status"] == _ELIGIBLE_APPROVAL
                and row["remediation_action"] == _ELIGIBLE_ACTION
                and row["proposed_norm"].strip()
            ):
                rows.append(row)
    return rows


def _load_approved_ids(path: Path) -> frozenset[str]:
    """Load product UUIDs from a file, one per line.

    Raises ValueError if the file contains duplicate IDs — duplicates indicate
    a data-preparation error in the approval file and must be resolved before
    the executor runs.
    """
    ids: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            pid = line.strip()
            if pid:
                ids.append(pid)

    seen: set[str] = set()
    dupes = [pid for pid in ids if pid in seen or seen.add(pid)]  # type: ignore[func-returns-value]
    if dupes:
        raise ValueError(
            f"Duplicate product IDs in approved-ids file ({len(dupes)} dupes): "
            + ", ".join(sorted(set(dupes))[:5])
        )

    return frozenset(ids)


def _manifest_generated_at(manifest_path: Path) -> str:
    """Derive a generation-date string from the manifest's parent directory name."""
    parent = manifest_path.parent.name
    if len(parent) == 8 and parent.isdigit():
        return parent  # e.g. "20261021"
    return datetime.fromtimestamp(manifest_path.stat().st_mtime, tz=timezone.utc).strftime(
        "%Y%m%d"
    )


# ── batch execution ───────────────────────────────────────────────────────────


@contextmanager
def _session_scope(engine):
    """Yield a session; commit on success, rollback on any exception."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _chunks(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _execute_batch(
    rows: list[dict],
    engine,
    *,
    batch_id: str,
    manifest_generated_at: str,
    approved_by: str,
    counters: dict,
) -> None:
    use_for_update = engine.dialect.name == "postgresql"

    with _session_scope(engine) as session:
        for row in rows:
            product_id_str = row["product_id"]
            manifest_proposed = row["proposed_norm"].strip()
            # Normalize empty CSV string → None for comparison with the ORM field.
            manifest_current = row.get("current_norm") or None

            try:
                product_uuid = UUID(product_id_str)
            except ValueError:
                counters["skipped_not_found"] += 1
                print(f"  SKIP  {product_id_str}  — invalid UUID in manifest")
                continue

            # Lock the product row for the duration of this transaction.
            # FOR UPDATE is only supported on PostgreSQL; SQLite tests skip it.
            stmt = select(Product).where(Product.id == product_uuid)
            if use_for_update:
                stmt = stmt.with_for_update()
            product = session.execute(stmt).scalar_one_or_none()

            if product is None:
                counters["skipped_not_found"] += 1
                print(f"  SKIP  {product_id_str}  — product no longer exists")
                continue

            before_norm = product.package_size_norm

            # Idempotency: if already corrected by a prior run, nothing to do.
            if before_norm == manifest_proposed:
                counters["skipped_already_correct"] += 1
                print(f"  OK    {product_id_str}  {before_norm!r} already correct")
                continue

            # Stale-plan guard: the locked norm must still match what was recorded
            # in the manifest.  A divergence means some other process changed the
            # product between planning and execution; skip and require a fresh plan.
            if before_norm != manifest_current:
                counters["skipped_stale_plan"] += 1
                print(
                    f"  SKIP  {product_id_str}  — stale plan: "
                    f"locked={before_norm!r}, manifest_current={manifest_current!r}"
                )
                continue

            # Live reclassification: re-fetch all current listings under this
            # transaction and re-run the full planner classification.  This
            # catches any new conflicting listing added by a concurrent ingest
            # that occurred after the manifest was generated.
            all_listings = session.execute(
                select(ProductRetailerListing).where(
                    ProductRetailerListing.product_id == product_uuid
                )
            ).scalars().all()

            live = classify_product(product, list(all_listings))

            if (
                live is None
                or live["classification"] != _ELIGIBLE_CLASSIFICATION
                or live["approval_status"] != APPROVAL_SAFE
                or live["proposed_norm"] != manifest_proposed
            ):
                live_cls = live["classification"] if live else "no_finding"
                live_prop = live["proposed_norm"] if live else "—"
                counters["skipped_reclassification"] += 1
                print(
                    f"  SKIP  {product_id_str}  — reclassification changed: "
                    f"{live_cls}/{live_prop!r} (was {_ELIGIBLE_CLASSIFICATION}/{manifest_proposed!r})"
                )
                continue

            # Apply the correction.
            product.package_size_norm = manifest_proposed

            # Write immutable audit record.
            session.add(
                PackageSizeRemediationLog(
                    product_id=product_id_str,
                    batch_id=batch_id,
                    before_norm=before_norm,
                    after_norm=manifest_proposed,
                    manifest_proposed_norm=manifest_proposed,
                    manifest_generated_at=manifest_generated_at,
                    classification=row["classification"],
                    approved_by=approved_by,
                )
            )

            counters["corrected"] += 1
            name = row.get("product_name", "")[:50]
            print(f"  FIX   {product_id_str}  {before_norm!r} → {manifest_proposed!r}  ({name})")


# ── dry-run report ────────────────────────────────────────────────────────────


def _print_dry_run(rows: list[dict], approved_ids: frozenset[str] | None) -> None:
    to_process = rows if approved_ids is None else [r for r in rows if r["product_id"] in approved_ids]
    print("\nDRY-RUN — no database writes")
    print(f"  Phase 1 eligible rows in manifest : {len(rows)}")
    print(f"  Would process (approved subset)   : {len(to_process)}")
    print()
    preview = to_process[:25]
    for r in preview:
        print(
            f"  {r['product_id']}  {r['current_norm'] or '(none)'!r} → "
            f"{r['proposed_norm']!r}  [{r['evidence_chain']}]  {r.get('product_name','')[:40]}"
        )
    if len(to_process) > 25:
        print(f"  … ({len(to_process) - 25} more)")
    print()
    print("Re-run with --approve --approved-ids <file> to execute.")


# ── entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Approval-gated executor for package_size_norm corrections."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to remediation_manifest.csv produced by remediation_planner.py",
    )
    parser.add_argument(
        "--approved-ids",
        dest="approved_ids",
        help="File containing approved product UUIDs, one per line (no duplicates)",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Execute writes (default: dry-run only)",
    )
    parser.add_argument(
        "--approved-by",
        dest="approved_by",
        default="batch",
        help="Identity recorded in the audit log (e.g. admin email)",
    )
    parser.add_argument(
        "--batch-size",
        dest="batch_size",
        type=int,
        default=100,
        help="Products per database transaction (default: 100)",
    )
    args = parser.parse_args()

    if args.approve and not args.approved_ids:
        print("ERROR: --approve requires --approved-ids <file>", file=sys.stderr)
        sys.exit(1)

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    rows = _load_manifest(manifest_path)
    print(f"Loaded manifest: {len(rows)} Phase 1 eligible rows")

    approved_ids: frozenset[str] | None = None
    if args.approved_ids:
        try:
            approved_ids = _load_approved_ids(Path(args.approved_ids))
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Approved IDs:    {len(approved_ids)}")

        # Verify every approved ID exists in the eligible manifest.
        manifest_ids = frozenset(r["product_id"] for r in rows)
        unknown = approved_ids - manifest_ids
        if unknown:
            print(
                f"ERROR: {len(unknown)} approved ID(s) not found in eligible manifest rows:",
                file=sys.stderr,
            )
            for uid in sorted(unknown)[:10]:
                print(f"  {uid}", file=sys.stderr)
            if len(unknown) > 10:
                print(f"  … ({len(unknown) - 10} more)", file=sys.stderr)
            sys.exit(1)

    if not args.approve:
        _print_dry_run(rows, approved_ids)
        return

    # ── live execution path ───────────────────────────────────────────────────
    to_process = rows if approved_ids is None else [r for r in rows if r["product_id"] in approved_ids]

    if not to_process:
        print("No eligible rows match the approved IDs. Nothing to do.")
        return

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(db_url, echo=False)
    batch_id = str(uuid4())
    gen_at = _manifest_generated_at(manifest_path)

    print(f"\nExecuting — batch_id={batch_id}")
    print(f"  Products to process: {len(to_process)}")
    print(f"  Batch size:          {args.batch_size}")
    print()

    counters = {
        "corrected": 0,
        "skipped_not_found": 0,
        "skipped_already_correct": 0,
        "skipped_stale_plan": 0,
        "skipped_reclassification": 0,
    }

    for chunk in _chunks(to_process, args.batch_size):
        _execute_batch(
            chunk,
            engine,
            batch_id=batch_id,
            manifest_generated_at=gen_at,
            approved_by=args.approved_by,
            counters=counters,
        )

    total = len(to_process)
    print()
    print("── Results ──────────────────────────────────────────────────────────")
    print(f"  Corrected                   : {counters['corrected']} / {total}")
    print(f"  Skipped (not found)         : {counters['skipped_not_found']}")
    print(f"  Skipped (already correct)   : {counters['skipped_already_correct']}")
    print(f"  Skipped (stale plan)        : {counters['skipped_stale_plan']}")
    print(f"  Skipped (reclassification)  : {counters['skipped_reclassification']}")
    print(f"  batch_id                    : {batch_id}")


if __name__ == "__main__":
    main()

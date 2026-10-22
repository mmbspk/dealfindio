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
  3. PRE-WRITE REVALIDATION — immediately before each write the executor
     re-reads the product and its listings, re-runs the normaliser, and
     verifies the result exactly matches the manifest's proposed_norm.
     Any product whose evidence has changed is skipped (not failed).
  4. NORMALISER AGREEMENT — the executor calls recompute_product_package_size_norm
     and rejects any product where the result ≠ proposed_norm from the manifest.
  5. SELECT FOR UPDATE — the product row is locked during the write to prevent
     concurrent catalog ingestion from overwriting the correction immediately.
  6. TRANSACTION + ROLLBACK — every batch runs inside BEGIN/COMMIT; an
     unexpected error triggers a full batch rollback.
  7. IDEMPOTENT — if a product's norm already equals proposed_norm it is
     counted as "already correct" and skipped without writing a log record.
  8. NO LISTING MUTATIONS — only product.package_size_norm is written.
     Listings, price_reports, watchlist_items, and predictions are untouched.
  9. AUDIT TRAIL — every corrected product gets an immutable
     package_size_remediation_log row (before_norm, after_norm, batch_id, …).
  10. PHASE 1 SCOPE — only confirmed_norm_mismatch / safe_candidate / recompute
      rows from the manifest are considered.

Usage:
    # Dry-run (safe, no writes):
    DATABASE_URL=postgresql://... python3 scripts/remediation_executor.py \\
        --manifest remediation_output/20261021/remediation_manifest.csv

    # Approve a small batch:
    DATABASE_URL=postgresql://... python3 scripts/remediation_executor.py \\
        --manifest remediation_output/20261021/remediation_manifest.csv \\
        --approved-ids /tmp/batch1_ids.txt \\
        --approve \\
        --approved-by "bilal@example.com"

    # Format of --approved-ids file: one product UUID per line, blank lines ignored.
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

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models.package_size_remediation_log import PackageSizeRemediationLog
from app.models.product import Product
from app.services.catalog_ingest import recompute_product_package_size_norm

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
    """Load product UUIDs from a file, one per line."""
    ids = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            pid = line.strip()
            if pid:
                ids.add(pid)
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

            # Idempotency: if already corrected, nothing to do.
            if before_norm == manifest_proposed:
                counters["skipped_already_correct"] += 1
                print(f"  OK    {product_id_str}  {before_norm!r} already correct")
                continue

            # Pre-write revalidation: recompute from current listing evidence.
            computed_norm = recompute_product_package_size_norm(session, product_uuid)

            if computed_norm != manifest_proposed:
                counters["skipped_revalidation"] += 1
                print(
                    f"  SKIP  {product_id_str}  — revalidation mismatch: "
                    f"computed={computed_norm!r}, manifest_proposed={manifest_proposed!r}"
                )
                continue

            # Apply the correction.
            product.package_size_norm = computed_norm

            # Write immutable audit record.
            session.add(
                PackageSizeRemediationLog(
                    product_id=product_id_str,
                    batch_id=batch_id,
                    before_norm=before_norm,
                    after_norm=computed_norm,
                    manifest_proposed_norm=manifest_proposed,
                    manifest_generated_at=manifest_generated_at,
                    classification=row["classification"],
                    approved_by=approved_by,
                )
            )

            counters["corrected"] += 1
            name = row.get("product_name", "")[:50]
            print(f"  FIX   {product_id_str}  {before_norm!r} → {computed_norm!r}  ({name})")


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
        help="File containing approved product UUIDs, one per line",
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
        approved_ids = _load_approved_ids(Path(args.approved_ids))
        print(f"Approved IDs:    {len(approved_ids)}")

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
        "skipped_revalidation": 0,
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
    print(f"  Corrected             : {counters['corrected']} / {total}")
    print(f"  Skipped (not found)   : {counters['skipped_not_found']}")
    print(f"  Skipped (already OK)  : {counters['skipped_already_correct']}")
    print(f"  Skipped (revalidation): {counters['skipped_revalidation']}")
    print(f"  batch_id              : {batch_id}")


if __name__ == "__main__":
    main()

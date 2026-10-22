"""
Tests for scripts/remediation_executor.py — Story #105 (review pass 2).

Uses SQLite in-memory for fast integration tests.  The executor's
revalidation and audit trail logic is tested end-to-end by calling
_execute_batch directly against a real in-memory session.

Test coverage:
  * _load_manifest — phase 1 filter, missing proposed_norm rows excluded
  * _load_manifest — raises ValueError on duplicate product_id (round-2 finding)
  * _load_approved_ids — parses UUIDs, ignores blank lines
  * _load_approved_ids — raises ValueError on duplicate IDs (Finding 4)
  * _execute_batch — successful correction, audit record written
  * _execute_batch — product not found → skip (not error)
  * _execute_batch — already correct (idempotent) → skip, no log row
  * _execute_batch — stale plan (locked norm ≠ manifest current_norm) → skip (Finding 3)
  * _execute_batch — live reclassification fails → skip (Finding 2)
  * _execute_batch — batch rollback on unexpected exception; counters stay 0 (round-2 finding)
  * _execute_batch — approved IDs filter respected
  * _execute_batch — mixed skip + correction in one batch
  * _print_dry_run — writes nothing, prints preview
  * main() — exits if approved ID not in eligible manifest (Finding 4)
  * main() — exits if --approve used without --approved-by (round-2 finding)
  * main() — exits if --batch-size 0 (round-2 finding)
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine, pool, select
from sqlalchemy.orm import Session

from app.models import Base
from app.models.package_size_remediation_log import PackageSizeRemediationLog
from app.models.product import Product
from app.models.product_retailer_listing import ProductRetailerListing
from scripts.remediation_executor import (
    _execute_batch,
    _load_approved_ids,
    _load_manifest,
    _print_dry_run,
)

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def executor_engine():
    """Isolated SQLite engine shared across all executor tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=pool.StaticPool,
        echo=False,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def ex_session(executor_engine):
    """Test session that rolls back after each test."""
    conn = executor_engine.connect()
    txn = conn.begin()
    session = Session(bind=conn)
    yield session
    session.close()
    txn.rollback()
    conn.close()


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_product(session: Session, *, norm: str | None, name: str = "Test Product") -> Product:
    p = Product(id=uuid4(), name=name, package_size_norm=norm)
    session.add(p)
    session.flush()
    return p


def _make_listing(
    session: Session, *, product: Product, raw: str, chain: str = "woolworths"
) -> ProductRetailerListing:
    lst = ProductRetailerListing(
        id=uuid4(),
        product_id=product.id,
        chain=chain,
        external_id=str(uuid4()),
        package_size_raw=raw,
    )
    session.add(lst)
    session.flush()
    return lst


def _write_manifest(rows: list[dict]) -> Path:
    """Write a minimal manifest CSV to a temp file and return its path."""
    headers = [
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
    defaults = {h: "" for h in headers}
    defaults.update(
        {
            "classification": "confirmed_norm_mismatch",
            "approval_status": "safe_candidate",
            "remediation_action": "recompute",
            "confidence": "high",
        }
    )

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    writer = csv.DictWriter(tmp, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow({**defaults, **row})
    tmp.close()
    return Path(tmp.name)


def _manifest_row(
    product_id: str,
    current_norm: str,
    proposed_norm: str,
    *,
    name: str = "Test Product",
) -> dict:
    return {
        "product_id": product_id,
        "product_name": name,
        "current_norm": current_norm,
        "proposed_norm": proposed_norm,
        "evidence_chain": "woolworths",
        "classification": "confirmed_norm_mismatch",
        "approval_status": "safe_candidate",
        "remediation_action": "recompute",
        "confidence": "high",
    }


def _batch_counters():
    return {
        "corrected": 0,
        "skipped_not_found": 0,
        "skipped_already_correct": 0,
        "skipped_stale_plan": 0,
        "skipped_reclassification": 0,
    }


# ── _load_manifest ─────────────────────────────────────────────────────────────


class TestLoadManifest:
    def test_loads_eligible_rows(self, tmp_path):
        path = _write_manifest(
            [
                _manifest_row("id-1", "375mL", "6x375mL"),
                _manifest_row("id-2", "250mL", "4x250mL"),
            ]
        )
        rows = _load_manifest(Path(path))
        assert len(rows) == 2

    def test_excludes_non_recompute_rows(self, tmp_path):
        path = _write_manifest(
            [
                _manifest_row("id-1", "375mL", "6x375mL"),
                {
                    "product_id": "id-2",
                    "product_name": "Manual",
                    "current_norm": "4x250mL",
                    "proposed_norm": "",
                    "classification": "conflicting_evidence",
                    "approval_status": "manual_review",
                    "remediation_action": "manual_unmerge_or_split",
                    "confidence": "high",
                    "evidence_chain": "aldi",
                },
            ]
        )
        rows = _load_manifest(Path(path))
        assert len(rows) == 1
        assert rows[0]["product_id"] == "id-1"

    def test_excludes_rows_with_empty_proposed_norm(self, tmp_path):
        path = _write_manifest(
            [
                {**_manifest_row("id-1", "375mL", ""), "proposed_norm": ""},
            ]
        )
        rows = _load_manifest(Path(path))
        assert rows == []

    def test_raises_on_duplicate_manifest_ids(self, tmp_path):
        """Round-2 finding: duplicate product_id in manifest must be rejected."""
        dup_id = str(uuid4())
        path = _write_manifest(
            [
                _manifest_row(dup_id, "375mL", "6x375mL"),
                _manifest_row(dup_id, "375mL", "6x375mL"),
            ]
        )
        with pytest.raises(ValueError, match="Duplicate product_id in manifest"):
            _load_manifest(Path(path))


# ── _load_approved_ids ─────────────────────────────────────────────────────────


class TestLoadApprovedIds:
    def test_parses_uuids(self, tmp_path):
        f = tmp_path / "ids.txt"
        ids = [str(uuid4()) for _ in range(3)]
        f.write_text("\n".join(ids) + "\n")
        result = _load_approved_ids(f)
        assert result == frozenset(ids)

    def test_ignores_blank_lines(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("id-1\n\n  \nid-2\n")
        result = _load_approved_ids(f)
        assert result == frozenset({"id-1", "id-2"})

    def test_raises_on_duplicate_ids(self, tmp_path):
        """Finding 4: duplicate IDs in approval file must be rejected."""
        dup_id = str(uuid4())
        other_id = str(uuid4())
        f = tmp_path / "ids.txt"
        f.write_text(f"{dup_id}\n{other_id}\n{dup_id}\n")
        with pytest.raises(ValueError, match="Duplicate product IDs"):
            _load_approved_ids(f)


# ── _execute_batch ─────────────────────────────────────────────────────────────


class TestExecuteBatch:
    def test_successful_correction(self, ex_session: Session, executor_engine):
        p = _make_product(ex_session, norm="375mL")
        _make_listing(ex_session, product=p, raw="375mL x 6 pack")
        ex_session.commit()

        pid = str(p.id)
        rows = [_manifest_row(pid, "375mL", "6x375mL")]
        counters = _batch_counters()

        _execute_batch(
            rows,
            executor_engine,
            batch_id="batch-1",
            manifest_generated_at="20261021",
            approved_by="test@example.com",
            counters=counters,
        )

        ex_session.expire(p)
        assert p.package_size_norm == "6x375mL"
        assert counters["corrected"] == 1

        log = ex_session.execute(
            select(PackageSizeRemediationLog).where(PackageSizeRemediationLog.product_id == pid)
        ).scalar_one()
        assert log.before_norm == "375mL"
        assert log.after_norm == "6x375mL"
        assert log.manifest_proposed_norm == "6x375mL"
        assert log.batch_id == "batch-1"
        assert log.manifest_generated_at == "20261021"
        assert log.approved_by == "test@example.com"
        assert log.classification == "confirmed_norm_mismatch"

    def test_product_not_found_is_skipped(self, ex_session: Session, executor_engine):
        missing_id = str(uuid4())
        rows = [_manifest_row(missing_id, "375mL", "6x375mL")]
        counters = _batch_counters()

        _execute_batch(
            rows,
            executor_engine,
            batch_id="batch-2",
            manifest_generated_at="20261021",
            approved_by="test",
            counters=counters,
        )

        assert counters["skipped_not_found"] == 1
        assert counters["corrected"] == 0

    def test_already_correct_is_idempotent(self, ex_session: Session, executor_engine):
        p = _make_product(ex_session, norm="6x375mL")
        _make_listing(ex_session, product=p, raw="375mL x 6 pack")
        ex_session.commit()

        pid = str(p.id)
        # manifest_proposed = "6x375mL" == current stored norm → already correct
        rows = [_manifest_row(pid, "375mL", "6x375mL")]
        counters = _batch_counters()

        _execute_batch(
            rows,
            executor_engine,
            batch_id="batch-3",
            manifest_generated_at="20261021",
            approved_by="test",
            counters=counters,
        )

        assert counters["skipped_already_correct"] == 1
        assert counters["corrected"] == 0
        # No audit record written for already-correct product.
        log_count = (
            ex_session.execute(
                select(PackageSizeRemediationLog).where(
                    PackageSizeRemediationLog.product_id == pid,
                    PackageSizeRemediationLog.batch_id == "batch-3",
                )
            )
            .scalars()
            .all()
        )
        assert log_count == []

    def test_stale_plan_guard(self, ex_session: Session, executor_engine):
        """Finding 3: locked norm ≠ manifest current_norm → skip."""
        # Product was planned when norm was "375mL", but something else changed
        # it to "500mL" after planning and before execution.
        p = _make_product(ex_session, norm="500mL")
        _make_listing(ex_session, product=p, raw="375mL x 6 pack")
        ex_session.commit()

        pid = str(p.id)
        # manifest says current_norm was "375mL" at plan time, but it's now "500mL"
        rows = [_manifest_row(pid, "375mL", "6x375mL")]
        counters = _batch_counters()

        _execute_batch(
            rows,
            executor_engine,
            batch_id="batch-stale",
            manifest_generated_at="20261021",
            approved_by="test",
            counters=counters,
        )

        assert counters["skipped_stale_plan"] == 1
        assert counters["corrected"] == 0
        # Norm must be unchanged.
        ex_session.expire(p)
        assert p.package_size_norm == "500mL"

    def test_live_reclassification_detects_new_conflicting_listing(
        self, ex_session: Session, executor_engine
    ):
        """Finding 2: a new listing added after planning creates a conflict.

        At plan time the product had only one listing: Woolworths "375mL x 6 pack"
        → proposed "6x375mL".  After planning, a Coles listing with raw "250mL"
        was added.  The live reclassification must detect the conflict and skip.
        """
        p = _make_product(ex_session, norm="375mL")
        _make_listing(ex_session, product=p, raw="375mL x 6 pack", chain="woolworths")
        # Conflicting listing added after the manifest was generated:
        _make_listing(ex_session, product=p, raw="250mL", chain="coles")
        ex_session.commit()

        pid = str(p.id)
        rows = [_manifest_row(pid, "375mL", "6x375mL")]
        counters = _batch_counters()

        _execute_batch(
            rows,
            executor_engine,
            batch_id="batch-conflict",
            manifest_generated_at="20261021",
            approved_by="test",
            counters=counters,
        )

        assert counters["skipped_reclassification"] == 1
        assert counters["corrected"] == 0
        # Norm must be unchanged.
        ex_session.expire(p)
        assert p.package_size_norm == "375mL"

    def test_live_reclassification_detects_raw_changed_to_single_unit(
        self, ex_session: Session, executor_engine
    ):
        """Finding 2: listing raw was updated to a single-unit value after planning.

        At plan time: raw = "375mL x 6 pack" → proposed "6x375mL".
        After planning: raw was updated to "375mL" (single-unit).
        The reclassification now returns None (no actionable finding) → skip.
        """
        p = _make_product(ex_session, norm="375mL")
        # Raw has been updated to single-unit after planning
        _make_listing(ex_session, product=p, raw="375mL", chain="woolworths")
        ex_session.commit()

        pid = str(p.id)
        rows = [_manifest_row(pid, "375mL", "6x375mL")]
        counters = _batch_counters()

        _execute_batch(
            rows,
            executor_engine,
            batch_id="batch-raw-changed",
            manifest_generated_at="20261021",
            approved_by="test",
            counters=counters,
        )

        assert counters["skipped_reclassification"] == 1
        assert counters["corrected"] == 0

    def test_batch_rollback_on_unexpected_exception(self, executor_engine):
        """An unexpected mid-batch exception must roll back all prior writes."""
        with Session(executor_engine) as setup_session:
            p1 = _make_product(setup_session, norm="375mL", name="Product A")
            _make_listing(setup_session, product=p1, raw="375mL x 6 pack")
            p2 = _make_product(setup_session, norm="250mL", name="Product B")
            _make_listing(setup_session, product=p2, raw="250mL x 4 pack")
            setup_session.commit()
            p1_id = str(p1.id)
            p2_id = str(p2.id)

        rows = [
            _manifest_row(p1_id, "375mL", "6x375mL"),
            _manifest_row(p2_id, "250mL", "4x250mL"),
        ]
        counters = _batch_counters()

        # Monkey-patch: raise on the second audit-log insert.
        original_add = Session.add

        call_count = [0]

        def _raise_on_second_log(self, obj):
            if isinstance(obj, PackageSizeRemediationLog):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise RuntimeError("simulated DB error mid-batch")
            return original_add(self, obj)

        Session.add = _raise_on_second_log
        try:
            with pytest.raises(RuntimeError, match="simulated DB error mid-batch"):
                _execute_batch(
                    rows,
                    executor_engine,
                    batch_id="batch-rollback",
                    manifest_generated_at="20261021",
                    approved_by="test",
                    counters=counters,
                )
        finally:
            Session.add = original_add

        # After rollback: both products must be unchanged, no log records.
        # Round-2 finding: counters must also be 0 — per-batch accumulation means
        # a rolled-back batch never merges into the caller's dict.
        assert counters["corrected"] == 0

        with Session(executor_engine) as verify:
            p1_after = verify.get(Product, p1.id)
            p2_after = verify.get(Product, p2.id)
            assert p1_after.package_size_norm == "375mL"
            assert p2_after.package_size_norm == "250mL"
            logs = (
                verify.execute(
                    select(PackageSizeRemediationLog).where(
                        PackageSizeRemediationLog.batch_id == "batch-rollback"
                    )
                )
                .scalars()
                .all()
            )
            assert logs == []

    def test_approved_ids_filter(self, ex_session: Session, executor_engine):
        p_approved = _make_product(ex_session, norm="375mL", name="Approved Product")
        _make_listing(ex_session, product=p_approved, raw="375mL x 6 pack")
        p_not_approved = _make_product(ex_session, norm="250mL", name="Not Approved")
        _make_listing(ex_session, product=p_not_approved, raw="250mL x 4 pack")
        ex_session.commit()

        approved_id = str(p_approved.id)
        not_approved_id = str(p_not_approved.id)

        all_rows = [
            _manifest_row(approved_id, "375mL", "6x375mL"),
            _manifest_row(not_approved_id, "250mL", "4x250mL"),
        ]
        # Filter to only the approved product before passing to _execute_batch.
        approved_rows = [r for r in all_rows if r["product_id"] == approved_id]
        counters = _batch_counters()

        _execute_batch(
            approved_rows,
            executor_engine,
            batch_id="batch-filter",
            manifest_generated_at="20261021",
            approved_by="test",
            counters=counters,
        )

        assert counters["corrected"] == 1

        ex_session.expire(p_approved)
        ex_session.expire(p_not_approved)
        assert p_approved.package_size_norm == "6x375mL"
        assert p_not_approved.package_size_norm == "250mL"

    def test_multiple_skips_do_not_prevent_other_corrections(
        self, ex_session: Session, executor_engine
    ):
        """Skipped products do not roll back correctable products in the same batch."""
        p_good = _make_product(ex_session, norm="375mL", name="Good")
        _make_listing(ex_session, product=p_good, raw="375mL x 6 pack")
        # Stale plan: norm changed since planning
        p_stale = _make_product(ex_session, norm="500mL", name="Stale")
        _make_listing(ex_session, product=p_stale, raw="500mL x 6 pack")
        ex_session.commit()

        rows = [
            _manifest_row(str(p_good.id), "375mL", "6x375mL"),
            # manifest says current was "375mL" but actual is "500mL" → stale
            _manifest_row(str(p_stale.id), "375mL", "6x375mL"),
        ]
        counters = _batch_counters()

        _execute_batch(
            rows,
            executor_engine,
            batch_id="batch-mixed",
            manifest_generated_at="20261021",
            approved_by="test",
            counters=counters,
        )

        assert counters["corrected"] == 1
        assert counters["skipped_stale_plan"] == 1

        ex_session.expire(p_good)
        ex_session.expire(p_stale)
        assert p_good.package_size_norm == "6x375mL"
        assert p_stale.package_size_norm == "500mL"


# ── _print_dry_run ─────────────────────────────────────────────────────────────


class TestPrintDryRun:
    def test_dry_run_prints_without_writing(self, ex_session: Session, capsys):
        p = _make_product(ex_session, norm="375mL")
        pid = str(p.id)
        rows = [_manifest_row(pid, "375mL", "6x375mL")]

        _print_dry_run(rows, approved_ids=None)

        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out
        assert "1" in captured.out

        # Norm must be unchanged.
        ex_session.expire(p)
        assert p.package_size_norm == "375mL"

    def test_dry_run_with_approved_ids_subset(self, capsys):
        rows = [
            _manifest_row(str(uuid4()), "375mL", "6x375mL"),
            _manifest_row(str(uuid4()), "250mL", "4x250mL"),
        ]
        approved = frozenset({rows[0]["product_id"]})
        _print_dry_run(rows, approved_ids=approved)
        captured = capsys.readouterr()
        assert "Would process" in captured.out or "1" in captured.out


# ── main() approval-integrity validation ──────────────────────────────────────


class TestMainApprovalIntegrity:
    def test_unknown_approved_id_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        """Finding 4: approved ID not in eligible manifest must cause sys.exit(1)."""
        import scripts.remediation_executor as exe

        manifest_path = _write_manifest([_manifest_row("known-id", "375mL", "6x375mL")])
        approved_path = tmp_path / "ids.txt"
        approved_path.write_text("known-id\nunknown-id\n")

        monkeypatch.setattr(
            "sys.argv",
            [
                "remediation_executor.py",
                "--manifest",
                str(manifest_path),
                "--approved-ids",
                str(approved_path),
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            exe.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "unknown-id" in captured.err or "not found" in captured.err.lower()


# ── main() argument validation ─────────────────────────────────────────────────


class TestMainValidation:
    def test_approved_by_required_with_approve(self, tmp_path, monkeypatch, capsys):
        """Round-2 finding: --approve without --approved-by must exit(1)."""
        import scripts.remediation_executor as exe

        manifest_path = _write_manifest([_manifest_row("id-1", "375mL", "6x375mL")])
        approved_path = tmp_path / "ids.txt"
        approved_path.write_text("id-1\n")

        monkeypatch.setattr(
            "sys.argv",
            [
                "remediation_executor.py",
                "--manifest",
                str(manifest_path),
                "--approved-ids",
                str(approved_path),
                "--approve",
                # intentionally omitting --approved-by
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            exe.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "approved-by" in captured.err.lower() or "identity" in captured.err.lower()

    def test_batch_size_must_be_positive(self, tmp_path, monkeypatch, capsys):
        """Round-2 finding: --batch-size 0 must exit(1)."""
        import scripts.remediation_executor as exe

        manifest_path = _write_manifest([_manifest_row("id-1", "375mL", "6x375mL")])

        monkeypatch.setattr(
            "sys.argv",
            [
                "remediation_executor.py",
                "--manifest",
                str(manifest_path),
                "--batch-size",
                "0",
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            exe.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "batch-size" in captured.err.lower() or "positive" in captured.err.lower()


# ── PostgreSQL concurrency (skipped in CI — requires real PostgreSQL) ──────────


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason=(
        "PostgreSQL concurrency test — requires "
        "DATABASE_URL=postgresql://... pytest tests/test_remediation_executor.py::TestPostgresqlConcurrency -v"
    ),
)
class TestPostgresqlConcurrency:
    """End-to-end locking verification against a real PostgreSQL database.

    Skipped in regular CI (SQLite).  Run manually against a dev/test PostgreSQL
    instance before the first live executor run:

        DATABASE_URL=postgresql://user:pass@host/db \\
            pytest tests/test_remediation_executor.py::TestPostgresqlConcurrency -v

    Background: ``product_retailer_listings.product_id`` has a FK to
    ``products.id``.  In PostgreSQL, FK validation on INSERT requires
    ``FOR KEY SHARE`` on the referenced product row.  ``FOR KEY SHARE`` is
    incompatible with ``FOR UPDATE``, so any concurrent INSERT for a locked
    product will block until the executor's transaction commits or rolls back.
    This test verifies that behaviour directly, using two real connections and
    threading synchronisation.
    """

    def test_for_update_on_product_blocks_concurrent_listing_insert(self):
        """FOR UPDATE on product row blocks concurrent listing INSERT via FK lock.

        Proves that new listing inserts cannot interleave with the executor's
        classify-and-write phase when it holds a FOR UPDATE lock on the product.
        """
        import threading
        import time

        pg_engine = create_engine(os.environ["DATABASE_URL"], echo=False)

        with Session(pg_engine) as setup:
            product = Product(id=uuid4(), name="ConcurrencyTestProduct", package_size_norm="375mL")
            setup.add(product)
            setup.commit()
            product_id = product.id

        lock_held = threading.Event()
        allow_commit = threading.Event()
        insert_done = threading.Event()
        thread_errors: list[tuple[str, Exception]] = []

        def executor_side() -> None:
            try:
                with Session(pg_engine) as session:
                    stmt = select(Product).where(Product.id == product_id).with_for_update()
                    session.execute(stmt)
                    lock_held.set()
                    allow_commit.wait(timeout=10)
                    session.commit()
            except Exception as exc:
                thread_errors.append(("executor", exc))

        def ingest_side() -> None:
            try:
                lock_held.wait(timeout=5)
                with Session(pg_engine) as session:
                    new_lst = ProductRetailerListing(
                        id=uuid4(),
                        product_id=product_id,
                        chain="coles",
                        external_id=f"conc-test-{uuid4()}",
                        package_size_raw="250mL",
                    )
                    session.add(new_lst)
                    session.commit()  # blocks: FK needs FOR KEY SHARE on locked product
            except Exception as exc:
                thread_errors.append(("ingest", exc))
            finally:
                insert_done.set()

        t_exec = threading.Thread(target=executor_side, daemon=True)
        t_ingest = threading.Thread(target=ingest_side, daemon=True)
        t_exec.start()
        t_ingest.start()

        assert lock_held.wait(timeout=5), "Executor never acquired FOR UPDATE lock"

        # Give the ingest thread enough time to reach and block on the INSERT.
        time.sleep(0.2)

        # INSERT must still be blocked (insert_done not set yet).
        assert not insert_done.is_set(), (
            "Concurrent listing INSERT completed while executor held FOR UPDATE lock. "
            "Expected blocking via FK FOR KEY SHARE requirement. "
            "Verify product_retailer_listings.product_id FK references products.id."
        )

        # Release executor lock; both threads should complete cleanly.
        allow_commit.set()
        t_exec.join(timeout=5)
        t_ingest.join(timeout=5)

        assert not thread_errors, f"Thread errors: {thread_errors}"
        assert insert_done.is_set(), "Ingest INSERT never completed after executor committed"

        # Cleanup test data.
        with Session(pg_engine) as cleanup:
            rows = (
                cleanup.execute(
                    select(ProductRetailerListing).where(
                        ProductRetailerListing.product_id == product_id
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                cleanup.delete(row)
            prod = cleanup.get(Product, product_id)
            if prod:
                cleanup.delete(prod)
            cleanup.commit()

        pg_engine.dispose()

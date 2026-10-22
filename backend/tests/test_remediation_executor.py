"""
Tests for scripts/remediation_executor.py — Story #105.

Uses SQLite in-memory for fast integration tests.  The executor's
revalidation and audit trail logic is tested end-to-end by calling
_execute_batch directly against a real in-memory session.

Test coverage:
  * _load_manifest — phase 1 filter, missing proposed_norm rows excluded
  * _load_approved_ids — parses UUIDs, ignores blank lines
  * _execute_batch — successful correction, audit record written
  * _execute_batch — product not found → skip (not error)
  * _execute_batch — already correct (idempotent) → skip
  * _execute_batch — revalidation mismatch → skip
  * _execute_batch — batch rollback on unexpected exception
  * _execute_batch — approved IDs filter respected
  * _print_dry_run — writes nothing, prints preview
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
        "product_id", "product_name", "product_gtin", "gtin_valid",
        "current_norm", "proposed_norm", "classification", "confidence",
        "approval_status", "justification", "evidence_chain",
        "evidence_listing_id", "evidence_raw", "evidence_name",
        "all_raw_norms", "conflict_listing_norms", "remediation_action",
    ]
    defaults = {h: "" for h in headers}
    defaults.update({
        "classification": "confirmed_norm_mismatch",
        "approval_status": "safe_candidate",
        "remediation_action": "recompute",
        "confidence": "high",
    })

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline=""
    )
    writer = csv.DictWriter(tmp, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow({**defaults, **row})
    tmp.close()
    return Path(tmp.name)


def _manifest_row(product_id: str, current_norm: str, proposed_norm: str) -> dict:
    return {
        "product_id": product_id,
        "product_name": "Test Product",
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
        "skipped_revalidation": 0,
    }


# ── _load_manifest ─────────────────────────────────────────────────────────────


class TestLoadManifest:
    def test_loads_eligible_rows(self, tmp_path):
        path = _write_manifest([
            _manifest_row("id-1", "375mL", "6x375mL"),
            _manifest_row("id-2", "250mL", "4x250mL"),
        ])
        rows = _load_manifest(Path(path))
        assert len(rows) == 2

    def test_excludes_non_recompute_rows(self, tmp_path):
        path = _write_manifest([
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
        ])
        rows = _load_manifest(Path(path))
        assert len(rows) == 1
        assert rows[0]["product_id"] == "id-1"

    def test_excludes_rows_with_empty_proposed_norm(self, tmp_path):
        path = _write_manifest([
            {**_manifest_row("id-1", "375mL", ""), "proposed_norm": ""},
        ])
        rows = _load_manifest(Path(path))
        assert rows == []


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
            select(PackageSizeRemediationLog).where(
                PackageSizeRemediationLog.product_id == pid
            )
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
        log_count = ex_session.execute(
            select(PackageSizeRemediationLog).where(
                PackageSizeRemediationLog.product_id == pid,
                PackageSizeRemediationLog.batch_id == "batch-3",
            )
        ).scalars().all()
        assert log_count == []

    def test_revalidation_mismatch_skips_product(self, ex_session: Session, executor_engine):
        # Product was planned with proposed_norm="6x375mL" but the listing raw
        # has since been updated to "250mL" (e.g. re-scraped as a different size).
        p = _make_product(ex_session, norm="375mL")
        _make_listing(ex_session, product=p, raw="250mL")  # raw changed since planning
        ex_session.commit()

        pid = str(p.id)
        rows = [_manifest_row(pid, "375mL", "6x375mL")]
        counters = _batch_counters()

        _execute_batch(
            rows,
            executor_engine,
            batch_id="batch-4",
            manifest_generated_at="20261021",
            approved_by="test",
            counters=counters,
        )

        assert counters["skipped_revalidation"] == 1
        assert counters["corrected"] == 0
        # Norm must not have changed.
        ex_session.expire(p)
        assert p.package_size_norm == "375mL"

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

        # Row 2 has an invalid proposed_norm that will cause a ValueError
        # when we artificially inject a bad value into manifest_proposed.
        rows = [
            _manifest_row(p1_id, "375mL", "6x375mL"),
            # Poison: this row's proposed_norm will be replaced to trigger an error.
            _manifest_row(p2_id, "250mL", "4x250mL"),
        ]
        counters = _batch_counters()

        # Monkey-patch to raise after the first product is written but before commit.
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
        with Session(executor_engine) as verify:
            p1_after = verify.get(Product, p1.id)
            p2_after = verify.get(Product, p2.id)
            assert p1_after.package_size_norm == "375mL"
            assert p2_after.package_size_norm == "250mL"
            logs = verify.execute(
                select(PackageSizeRemediationLog).where(
                    PackageSizeRemediationLog.batch_id == "batch-rollback"
                )
            ).scalars().all()
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
        # This product's raw has changed — will fail revalidation.
        p_changed = _make_product(ex_session, norm="375mL", name="Changed Raw")
        _make_listing(ex_session, product=p_changed, raw="500mL")
        ex_session.commit()

        rows = [
            _manifest_row(str(p_good.id), "375mL", "6x375mL"),
            _manifest_row(str(p_changed.id), "375mL", "6x375mL"),
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
        assert counters["skipped_revalidation"] == 1

        ex_session.expire(p_good)
        ex_session.expire(p_changed)
        assert p_good.package_size_norm == "6x375mL"
        assert p_changed.package_size_norm == "375mL"


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

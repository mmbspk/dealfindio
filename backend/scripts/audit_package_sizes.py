"""
Read-only catalog integrity audit — Story #101.

Identifies five categories of potential data quality issues introduced before
PR #98 / #100 (package-size normalizer improvements):

  1  Name-indicates-multipack but stored norm is single-unit.
       subtype class_a — raw already contains pack count; recompute fixes it.
       subtype class_b — pack count was discarded before persistence; needs
                         fresh ingestion.
  2  Raw-vs-canonical conflict: normalize_package_size(listing.package_size_raw)
     differs from product.package_size_norm under the updated normalizer.
  3  Cross-retailer size disagreement: multiple listings on one canonical product
     produce incompatible norms.
  4  Single/multipack collision: a canonical product has both single-unit and
     multipack listings — suspected bad merge.
  5  GTIN integrity: products with GTINs that fail GS1 check-digit validation.

This script is read-only. It never writes to the database.
Pass DATABASE_URL as an environment variable — it is never logged.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/audit_package_sizes.py
    DATABASE_URL=postgresql://... python3 scripts/audit_package_sizes.py --out-dir /tmp/audit
    DATABASE_URL=postgresql://... python3 scripts/audit_package_sizes.py --chains coles,woolworths
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "audit-script")

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_retailer_listing import ProductRetailerListing
from app.services.package_size import normalize_package_size

# ── detection helpers ─────────────────────────────────────────────────────────

# Signals that an external_name describes a multipack product.
_MULTIPACK_SIGNAL_RE = re.compile(
    r"\b\d+\s*[xX×]\s*\d+" r"|\b\d+\s*(?:pack|pk|ct|count|pcs?|pieces?)\b" r"|multipack",
    re.I,
)

# A canonical norm that is itself a multipack starts with "NNx".
_MULTIPACK_NORM_RE = re.compile(r"^\d+x")


def _is_multipack_name(text: str | None) -> bool:
    return bool(text and _MULTIPACK_SIGNAL_RE.search(text))


def _is_multipack_norm(norm: str | None) -> bool:
    return bool(norm and _MULTIPACK_NORM_RE.match(norm))


def _gtin_check_digit_valid(gtin: str) -> bool:
    """Return True if gtin passes GS1 check-digit validation (GTIN-8/12/13/14)."""
    d = gtin.strip()
    if not d.isdigit() or len(d) not in (8, 12, 13, 14):
        return False
    # GS1 standard: from right, alternating weights 3 and 1 (right-to-left, pos 0 = rightmost).
    total = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(d[:-1])))
    return (10 - total % 10) % 10 == int(d[-1])


# ── CSV fieldname schemas ─────────────────────────────────────────────────────

_FIELDS_1 = [
    "product_id",
    "product_name",
    "product_gtin",
    "product_package_size_norm",
    "listing_chain",
    "listing_external_id",
    "listing_external_name",
    "listing_package_size_raw",
    "computed_norm",
    "subtype",
    "remediation",
]
_FIELDS_2 = [
    "product_id",
    "product_name",
    "product_gtin",
    "product_package_size_norm",
    "listing_chain",
    "listing_external_id",
    "listing_external_name",
    "listing_package_size_raw",
    "computed_norm",
]
_FIELDS_3 = [
    "product_id",
    "product_name",
    "product_gtin",
    "product_package_size_norm",
    "listing_chain",
    "listing_external_id",
    "listing_package_size_raw",
    "computed_norm",
    "all_norms_on_product",
]
_FIELDS_4 = [
    "product_id",
    "product_name",
    "product_gtin",
    "product_package_size_norm",
    "listing_chain",
    "listing_external_id",
    "listing_external_name",
    "listing_package_size_raw",
    "computed_norm",
    "listing_type",
    "single_norms",
    "multi_norms",
]
_FIELDS_5 = [
    "product_id",
    "product_name",
    "gtin",
    "failure_reason",
    "product_package_size_norm",
]

# ── data loading (batched) ────────────────────────────────────────────────────

BATCH_SIZE = 10_000


def _get_auditable_product_ids(
    session: Session,
    chains: set[str] | None,
) -> list:
    """Return product IDs (UUID objects) to include in the audit.

    Results are ordered by ID so ``--limit`` selects the same products on
    every run, making partial audits reproducible and comparable across runs.

    When *chains* is given, only products that have at least one listing in
    those chains are returned so the GTIN audit scope matches the listing
    scope.  When *chains* is None every product ID is returned.

    Memory note: the full list of IDs is held in memory.  At 100 000 products
    this is roughly 16 MB of UUID objects — comfortably within typical limits.
    If the catalog grows significantly, replace this call with a keyset-
    paginated cursor and stream findings directly to CSV rather than
    accumulating them in ``run_audit``.
    """
    if chains:
        stmt = (
            select(ProductRetailerListing.product_id)
            .where(ProductRetailerListing.chain.in_(chains))
            .distinct()
            .order_by(ProductRetailerListing.product_id)
        )
    else:
        stmt = select(Product.id).order_by(Product.id)
    return [row[0] for row in session.execute(stmt).all()]


def _load_batch(
    session: Session,
    product_ids: list,
    chains: set[str] | None,
) -> tuple[dict, dict]:
    """Load products and listings for one batch of product IDs.

    Returns *(products_by_id, listings_by_product_id)* dicts.
    Listings are filtered by *chains* when provided.
    """
    products = session.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()
    products_by_id = {str(p.id): p for p in products}

    lst_stmt = select(ProductRetailerListing).where(
        ProductRetailerListing.product_id.in_(product_ids)
    )
    if chains:
        lst_stmt = lst_stmt.where(ProductRetailerListing.chain.in_(chains))
    listings = session.execute(lst_stmt).scalars().all()

    listings_by_product_id: dict[str, list] = defaultdict(list)
    for lst in listings:
        listings_by_product_id[str(lst.product_id)].append(lst)

    return products_by_id, dict(listings_by_product_id)


# ── audit categories ──────────────────────────────────────────────────────────


def audit_name_vs_norm(
    products_by_id: dict,
    listings_by_product_id: dict,
) -> list[dict]:
    """Category 1: name indicates multipack but stored norm is single-unit."""
    findings = []
    for product_id, listings in listings_by_product_id.items():
        product = products_by_id.get(product_id)
        if product is None:
            continue
        stored_norm = product.package_size_norm
        if _is_multipack_norm(stored_norm):
            continue  # norm already reflects multipack — nothing to report
        for lst in listings:
            name_text = lst.external_name or ""
            if not _is_multipack_name(name_text):
                continue
            new_norm = normalize_package_size(lst.package_size_raw)
            if _is_multipack_norm(new_norm):
                subtype = "class_a"
                remediation = (
                    "Run recompute_product_package_size_norm — raw already contains pack count"
                )
            else:
                subtype = "class_b"
                remediation = "Needs fresh ingestion — pack count was discarded before persistence"
            findings.append(
                {
                    "product_id": product_id,
                    "product_name": product.name,
                    "product_gtin": product.gtin or "",
                    "product_package_size_norm": stored_norm or "",
                    "listing_chain": lst.chain,
                    "listing_external_id": lst.external_id,
                    "listing_external_name": name_text,
                    "listing_package_size_raw": lst.package_size_raw or "",
                    "computed_norm": new_norm or "",
                    "subtype": subtype,
                    "remediation": remediation,
                }
            )
    return findings


def audit_raw_vs_canonical(
    products_by_id: dict,
    listings_by_product_id: dict,
) -> list[dict]:
    """Category 2: normalize_package_size(raw) ≠ stored product.package_size_norm."""
    findings = []
    for product_id, listings in listings_by_product_id.items():
        product = products_by_id.get(product_id)
        if product is None:
            continue
        stored_norm = product.package_size_norm
        if stored_norm is None:
            continue
        for lst in listings:
            if not lst.package_size_raw:
                continue
            new_norm = normalize_package_size(lst.package_size_raw)
            if new_norm is None or new_norm == stored_norm:
                continue
            findings.append(
                {
                    "product_id": product_id,
                    "product_name": product.name,
                    "product_gtin": product.gtin or "",
                    "product_package_size_norm": stored_norm,
                    "listing_chain": lst.chain,
                    "listing_external_id": lst.external_id,
                    "listing_external_name": lst.external_name or "",
                    "listing_package_size_raw": lst.package_size_raw,
                    "computed_norm": new_norm,
                }
            )
    return findings


def audit_cross_retailer_conflicts(
    products_by_id: dict,
    listings_by_product_id: dict,
) -> list[dict]:
    """Category 3: multiple listings on one product with incompatible norms."""
    findings = []
    for product_id, listings in listings_by_product_id.items():
        product = products_by_id.get(product_id)
        if product is None:
            continue
        parsed: list[tuple[str, str, str, str]] = []
        for lst in listings:
            n = normalize_package_size(lst.package_size_raw)
            if n is not None:
                parsed.append((lst.chain, lst.external_id, lst.package_size_raw or "", n))
        if len(parsed) < 2:
            continue
        norms = {item[3] for item in parsed}
        if len(norms) <= 1:
            continue
        norms_str = "|".join(sorted(norms))
        for chain, ext_id, raw, norm in parsed:
            findings.append(
                {
                    "product_id": product_id,
                    "product_name": product.name,
                    "product_gtin": product.gtin or "",
                    "product_package_size_norm": product.package_size_norm or "",
                    "listing_chain": chain,
                    "listing_external_id": ext_id,
                    "listing_package_size_raw": raw,
                    "computed_norm": norm,
                    "all_norms_on_product": norms_str,
                }
            )
    return findings


def audit_single_multipack_collision(
    products_by_id: dict,
    listings_by_product_id: dict,
) -> list[dict]:
    """Category 4: product has both single-unit and multipack listings (bad-merge suspect)."""
    findings = []
    for product_id, listings in listings_by_product_id.items():
        product = products_by_id.get(product_id)
        if product is None:
            continue
        single_items: list[tuple] = []
        multi_items: list[tuple] = []
        for lst in listings:
            n = normalize_package_size(lst.package_size_raw)
            if n is None:
                continue
            if _is_multipack_norm(n):
                multi_items.append((lst, n))
            else:
                single_items.append((lst, n))
        if not single_items or not multi_items:
            continue
        single_norms = "|".join(sorted({x[1] for x in single_items}))
        multi_norms = "|".join(sorted({x[1] for x in multi_items}))
        for lst, n in single_items + multi_items:
            findings.append(
                {
                    "product_id": product_id,
                    "product_name": product.name,
                    "product_gtin": product.gtin or "",
                    "product_package_size_norm": product.package_size_norm or "",
                    "listing_chain": lst.chain,
                    "listing_external_id": lst.external_id,
                    "listing_external_name": lst.external_name or "",
                    "listing_package_size_raw": lst.package_size_raw or "",
                    "computed_norm": n,
                    "listing_type": "multipack" if _is_multipack_norm(n) else "single",
                    "single_norms": single_norms,
                    "multi_norms": multi_norms,
                }
            )
    return findings


def audit_gtin_integrity(products_by_id: dict) -> list[dict]:
    """Category 5: products with GTINs that fail GS1 check-digit validation."""
    findings = []
    for product_id, product in products_by_id.items():
        gtin = product.gtin
        if not gtin:
            continue
        if _gtin_check_digit_valid(gtin):
            continue
        d = gtin.strip()
        if not d.isdigit():
            reason = "non-numeric"
        elif len(d) not in (8, 12, 13, 14):
            reason = f"invalid-length-{len(d)}"
        else:
            reason = "bad-check-digit"
        findings.append(
            {
                "product_id": product_id,
                "product_name": product.name,
                "gtin": gtin,
                "failure_reason": reason,
                "product_package_size_norm": product.package_size_norm or "",
            }
        )
    return findings


# ── output helpers ────────────────────────────────────────────────────────────

_CSV_FORMULA_PREFIXES = frozenset("=+-@\t\r\n")


def _sanitize_csv_cell(value: str) -> str:
    """Prevent spreadsheet formula injection when a cell starts with a formula prefix."""
    if value and value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {k: _sanitize_csv_cell(v) if isinstance(v, str) else v for k, v in row.items()}
            )


def _write_summary(
    out_dir: Path,
    counts: dict[str, int],
    run_at: str,
    chains_filter: str | None,
) -> None:
    lines = [
        "# Catalog Package-Size Integrity Audit",
        "",
        f"Generated: {run_at}",
        f"Chains filter: {chains_filter or '(all chains)'}",
        "",
        "## Finding counts",
        "",
        "| Category | Description | Count |",
        "| --- | --- | --- |",
    ]
    descriptions = {
        "1_name_vs_norm": "Name indicates multipack but stored norm is single-unit",
        "2_raw_vs_canonical": "normalize_package_size(raw) ≠ stored product.package_size_norm",
        "3_cross_retailer": "Multiple listings on one product with incompatible norms",
        "4_single_multipack": "Product has both single-unit and multipack listings (bad-merge suspect)",
        "5_gtin": "GTIN fails GS1 check-digit validation",
    }
    for key, desc in descriptions.items():
        count = counts.get(key, 0)
        lines.append(f"| {key} | {desc} | {count} |")

    lines += [
        "",
        "## Remediation strategy",
        "",
        "### Category 1a (Class A) — recomputable",
        "",
        "The raw string already contains the pack count (e.g. '375mL 12 Pack').",
        "The stored norm is stale because it was computed before PR #98/#100.",
        "Run `recompute_product_package_size_norm(session, product_id)` for each",
        "affected product. Safe to batch-run; idempotent.",
        "",
        "```sql",
        "-- Identify Class A candidates (read-only):",
        "SELECT p.id, p.name, p.package_size_norm, prl.package_size_raw, prl.chain",
        "FROM products p",
        "JOIN product_retailer_listings prl ON prl.product_id = p.id",
        "WHERE (",
        "       prl.package_size_raw ~* '[0-9]+\\s*(pack|pk)'",
        "    OR prl.package_size_raw ~* '[0-9]+\\s*x\\s*[0-9]+'",
        "  )",
        "  AND (p.package_size_norm IS NULL OR p.package_size_norm NOT LIKE '%x%');",
        "```",
        "",
        "### Category 1b (Class B) — needs fresh ingestion",
        "",
        "The pack count was discarded before persistence: `package_size_raw = '375mL'`",
        "even though the product name says '12 Pack ...'.",
        "`recompute_product_package_size_norm` is NOT sufficient here — the raw value",
        "lacks the count. Options:",
        "1. Re-trigger a full scrape of the affected chain so the extractor runs the",
        "   updated `_augment_with_pack_count` logic with the original API response.",
        "2. Manually update `package_size_raw` using `external_name` as the source.",
        "",
        "```sql",
        "-- Identify Class B candidates (read-only):",
        "SELECT p.id, p.name, p.package_size_norm,",
        "       prl.package_size_raw, prl.external_name, prl.chain",
        "FROM products p",
        "JOIN product_retailer_listings prl ON prl.product_id = p.id",
        "WHERE prl.external_name ~* '[0-9]+\\s*(pack|pk)'",
        "  AND (",
        "        prl.package_size_raw IS NULL",
        "     OR (prl.package_size_raw !~* '[0-9]+\\s*(pack|pk)'",
        "         AND prl.package_size_raw NOT LIKE '%x%')",
        "  )",
        "  AND (p.package_size_norm IS NULL OR p.package_size_norm NOT LIKE '%x%');",
        "```",
        "",
        "### Category 2 — stale norms",
        "",
        "Run `recompute_product_package_size_norm` for each affected product_id.",
        "These products have a raw value that the updated normalizer now parses",
        "differently from the stored norm.",
        "",
        "### Categories 3 & 4 — merge conflicts",
        "",
        "Manual review required. Each row identifies a canonical product where",
        "different retailer listings suggest different package sizes. This may",
        "indicate a bad merge or a genuine retailer data inconsistency.",
        "Use `product_id` to locate the row in the admin UI.",
        "",
        "### Category 5 — GTIN integrity",
        "",
        "Invalid GTINs should be corrected manually. Use the Open Food Facts API",
        "(https://world.openfoodfacts.org/api/v2/product/{gtin}) to look up the",
        "correct GTIN for a product by name.",
        "",
        "## Output files",
        "",
    ]
    for key in descriptions:
        lines.append(f"- `findings_{key}.csv` — {counts.get(key, 0)} findings")

    (out_dir / "audit_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── public API ────────────────────────────────────────────────────────────────


def run_audit(
    session: Session,
    chains: set[str] | None = None,
    limit: int = 0,
    batch_size: int = BATCH_SIZE,
) -> dict[str, list[dict]]:
    """Run all audit categories and return findings keyed by category name.

    *session* is used read-only.  *chains* restricts analysis to listings from
    specific retailer chains and also restricts the GTIN audit to the same
    product scope.  *limit* caps the total number of products analysed (0 =
    unlimited; for development use only).  *batch_size* controls how many
    products are loaded per round-trip (default: 10 000).
    """
    product_ids = _get_auditable_product_ids(session, chains)

    if limit:
        product_ids = product_ids[:limit]

    findings: dict[str, list[dict]] = {
        "1_name_vs_norm": [],
        "2_raw_vs_canonical": [],
        "3_cross_retailer": [],
        "4_single_multipack": [],
        "5_gtin": [],
    }

    for start in range(0, max(len(product_ids), 1), batch_size):
        batch = product_ids[start : start + batch_size]
        if not batch:
            break
        products_by_id, listings_by_pid = _load_batch(session, batch, chains)

        findings["1_name_vs_norm"].extend(audit_name_vs_norm(products_by_id, listings_by_pid))
        findings["2_raw_vs_canonical"].extend(
            audit_raw_vs_canonical(products_by_id, listings_by_pid)
        )
        findings["3_cross_retailer"].extend(
            audit_cross_retailer_conflicts(products_by_id, listings_by_pid)
        )
        findings["4_single_multipack"].extend(
            audit_single_multipack_collision(products_by_id, listings_by_pid)
        )
        findings["5_gtin"].extend(audit_gtin_integrity(products_by_id))

    return findings


# ── CLI entry point ───────────────────────────────────────────────────────────

_CATEGORY_FIELDS = {
    "1_name_vs_norm": _FIELDS_1,
    "2_raw_vs_canonical": _FIELDS_2,
    "3_cross_retailer": _FIELDS_3,
    "4_single_multipack": _FIELDS_4,
    "5_gtin": _FIELDS_5,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only catalog integrity audit (Story #101)")
    parser.add_argument(
        "--out-dir",
        default="audit_output",
        help="Directory for CSV and Markdown output (created if absent)",
    )
    parser.add_argument(
        "--chains",
        default=None,
        help="Comma-separated chain names to restrict to (default: all chains)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max products to audit per category (0 = unlimited)",
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

    # Enforce read-only at the PostgreSQL session level so accidental writes
    # are rejected by the database itself, not just by convention.
    @event.listens_for(engine, "connect")
    def _set_readonly(dbapi_conn, _record):
        if hasattr(dbapi_conn, "set_session"):  # psycopg2
            dbapi_conn.set_session(readonly=True)

    with Session(engine) as session:
        if engine.dialect.name == "postgresql":
            session.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        print("Loading catalog data…")
        findings = run_audit(session, chains=chains, limit=args.limit)

    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    chains_filter = ", ".join(sorted(chains)) if chains else None

    for key, rows in findings.items():
        csv_path = out_dir / f"findings_{key}.csv"
        _write_csv(csv_path, _CATEGORY_FIELDS[key], rows)
        print(f"  {key}: {len(rows)} findings → {csv_path}")

    counts = {k: len(v) for k, v in findings.items()}
    _write_summary(out_dir, counts, run_at, chains_filter)
    print(f"\nSummary written to {out_dir / 'audit_summary.md'}")
    print(f"Total findings: {sum(counts.values())}")


if __name__ == "__main__":
    main()

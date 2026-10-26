#!/usr/bin/env python3
"""
Multi-Retailer Fresh Crawl & Catalog Integrity Validation

Validates the complete crawler-to-database pipeline using real retailer data.
Originally written for Woolworths (Story #109); made chain-agnostic in Story #114.

Usage:
  python scripts/validate_crawl_109.py [options]

Options:
  --chain CHAIN          Retailer to crawl and ingest (default: woolworths).
                         Supported: woolworths, aldi, iga, coles.
                         Must be registered in scrapers/catalog_registry.py.
  --category-name NAME   Category name to target (default: Beer).
                         The available names are chain-specific — check the
                         discovery log on a dry-run if unsure.
  --max-pages N          Max pages per category, 36 products/page (default: 2)
  --out-dir DIR          Directory for the validation report (default: validation_output)
  --dry-run              Parse and display products without writing to the database
  --chains CHAINS        Comma-separated chains for audit scope.
                         Defaults to the selected --chain when omitted.

Connects to DATABASE_URL (dev database expected).  Never connects to production.

IMPORTANT: This script commits changes on the first ingest and on the repeat
ingest independently.  A failed validation (network error mid-run, unexpected
crash) may leave partial data in the database.  Always run against a disposable
development or test database.  The script queries SELECT current_database() to
verify the target is a recognised dev/test database before any writes.

Pipeline steps:
  1. Validate --chain against the scraper registry; abort if unknown
  2. Verify database name contains a safe suffix (dev / test / local / staging / ci)
  3. Capture baseline DB metrics for the selected chain's products
  4. Capture baseline confirmed-norm-mismatch count (before ingest) for AC5
  5. Run bounded crawl (one category, max N pages)
  6. Ingest the scraped products (first run); capture listing-to-product map
  7. Capture post-ingest metrics and repeat run (idempotency)
  8. Run package-size audit
  9. Run remediation planner (post-ingest) and compare against baseline count
  10. Evaluate the five acceptance criteria
  11. Write Markdown report

Acceptance criteria:
  AC1  At least one scraped product was enriched from count-only to a multipack
       norm (proves combine_count_with_volume fired on real data).
  AC2  No canonical product created by this crawl appears in the audit's
       single/multipack collision list.
  AC3  No two scraped products with different norms were merged into the same
       canonical product (proves the symmetric soft veto held).
  AC4  After repeat ingest, every listing still points to the same canonical
       product it was assigned on the first run (proves idempotency).
  AC5  The count of confirmed_norm_mismatch rows did not increase after ingest
       (proves no new normalisation defects were introduced).

Errors in audit or planner are surfaced as failures — they are never silently
ignored.

Exit code:
  0  All acceptance criteria met and no errors
  1  One or more criteria failed or an error occurred (see report)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ── sys.path bootstrap (run from backend/) ───────────────────────────────────
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Product, ProductRetailerListing
from app.services.catalog_ingest import ingest_catalog_products
from app.services.package_size import normalize_package_size
from scrapers.catalog_base import CatalogProduct
from scrapers.catalog_registry import get_catalog_scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("validate_crawl_109")

# Database names that are recognised as safe dev/test targets.
_SAFE_DB_FRAGMENTS = ("dev", "test", "local", "staging", "ci")

# Regex matching count-only size strings (no per-unit volume).
# Shared across Woolworths, ALDI, and Coles — each uses a flat string size field.
# Matches: "6PK", "6 Pack", "6EA", "6 each", "30 pack", "100 pack", "12 each", etc.
# Count=1 is excluded (scraper suppresses "1EA" / "1 each" to None).
#
# Coles: the raw item["size"] field can hold a bare count (e.g. "18 Pack").
# These items never get enriched by combine_count_with_volume (the Coles scraper
# uses _augment_with_pack_count instead), so AC1 is Not Exercised for those
# categories — which is acceptable per the completeness rules.
#
# IGA uses a nested dict unitOfSize={"size": N, "type": "each"/"pk"/...} — see
# _is_iga_count_only() for that chain's count-only detection.
_RE_COUNT_ONLY_API_SIZE = re.compile(r"^(\d+)\s*(?:ea|each|pk|pack)$", re.IGNORECASE)

# Per-chain field names for the raw product dict returned by crawl_category.
# Each entry maps to (external_id_field, package_size_field).
# IGA's size field (unitOfSize) is a dict — _identify_count_only_external_ids
# handles it via _is_iga_count_only() rather than the regex path.
_CHAIN_ITEM_FIELDS: dict[str, tuple[str, str]] = {
    "woolworths": ("Stockcode", "PackageSize"),
    "aldi": ("sku", "sellingSize"),
    "iga": ("productId", "unitOfSize"),
    "coles": ("id", "size"),
}


def _extract_raw_items(raw: dict, chain: str) -> list[dict]:
    """Extract the flat list of raw product dicts from a crawl_category response.

    Different chains use different top-level structures:
      woolworths: {"products": [item, ...]}
      aldi:       {"pages": [{"data": [item, ...]}, ...]}
      iga:        {"pages": [{"total": N, "count": N, "items": [item, ...]}, ...]}
      coles:      {"pages": [{"pageProps": {"searchResults": {"results": [item, ...]}}}, ...]}
    Returns an empty list for unknown chains (count-only detection will be skipped).
    """
    if chain == "aldi":
        return [item for page in raw.get("pages", []) for item in page.get("data", [])]
    if chain == "iga":
        return [item for page in raw.get("pages", []) for item in page.get("items", [])]
    if chain == "coles":
        items: list[dict] = []
        for page in raw.get("pages", []):
            props = page.get("pageProps") or {}
            search = props.get("searchResults") or {}
            page_items = (
                search.get("results")
                or props.get("results")
                or page.get("results")
                or []
            )
            if isinstance(page_items, list):
                items.extend(page_items)
        return items
    return raw.get("products", [])


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class NormDistribution:
    """Count of products in each package_size_norm bucket for one chain."""

    total: int = 0
    with_norm: int = 0
    null_norm: int = 0
    multipack_norm: int = 0  # norm contains 'x' e.g. "6x375mL"


@dataclass
class ValidationResult:
    """Accumulated results for one validation run."""

    # Scraper output
    scraped_products: list[CatalogProduct] = field(default_factory=list)

    # DB state snapshots
    baseline: NormDistribution = field(default_factory=NormDistribution)
    post_ingest: NormDistribution = field(default_factory=NormDistribution)

    # Ingest counts
    ingest_counts_first: dict = field(default_factory=dict)
    ingest_counts_repeat: dict = field(default_factory=dict)

    # Listing maps: {external_id: str(product_id)}, populated after each ingest.
    # Used by AC3 (merge detection) and AC4 (stability check).
    listing_map_first: dict[str, str] = field(default_factory=dict)
    listing_map_repeat: dict[str, str] = field(default_factory=dict)

    # Canonical product IDs created/used by this crawl (from first-ingest listings).
    # Used by AC2 (collision detection scoped to new products).
    new_product_ids: set[str] = field(default_factory=set)

    # External IDs of scraped products whose raw API PackageSize was count-only
    # (e.g. "6PK").  AC1 checks that at least one of these was enriched to a
    # multipack norm by combine_count_with_volume.
    count_only_external_ids: set[str] = field(default_factory=set)

    # Expected normalized size per count-only external ID.
    # Populated in main() from CatalogProduct.package_size via normalize_package_size().
    # Format: {external_id: normalize_package_size(cp.package_size)} — None when the
    # scraper could not enrich (no mL/L volume in the product name).
    count_only_expected_norms: dict[str, str | None] = field(default_factory=dict)

    # Persisted package_size_norm per count-only external ID.
    # Populated after first ingest by querying Product.package_size_norm via the listing
    # map.  Keyed by external_id (not canonical product id) so AC1 can compare expected
    # vs persisted per-SKU without aggregating by canonical.
    count_only_persisted_norms: dict[str, str | None] = field(default_factory=dict)

    # Confirmed-norm-mismatch counts before and after ingest.
    # -1 means "not yet measured".  Used by AC5.
    baseline_plan_confirmed_count: int = -1
    post_plan_confirmed_count: int = -1

    # Audit/plan output
    audit_findings: dict[str, list[dict]] = field(default_factory=dict)
    plan_findings: list[dict] = field(default_factory=list)

    # Evaluation
    criteria_results: dict[str, bool | None] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ── DB helpers ────────────────────────────────────────────────────────────────


def _check_db_name(session: Session) -> str | None:
    """Return the actual database name, or None if the query fails."""
    try:
        return session.execute(text("SELECT current_database()")).scalar()
    except Exception as exc:
        logger.warning("Could not query database name: %s", exc)
        return None


def _norm_distribution(session: Session, chain: str) -> NormDistribution:
    """Query current norm distribution for products with a listing in *chain*."""
    rows = session.execute(
        text(
            """
            SELECT p.package_size_norm
            FROM products p
            JOIN product_retailer_listings l ON l.product_id = p.id
            WHERE l.chain = :chain
            """
        ),
        {"chain": chain},
    ).fetchall()
    dist = NormDistribution(total=len(rows))
    for (norm,) in rows:
        if norm is None:
            dist.null_norm += 1
        else:
            dist.with_norm += 1
            if "x" in norm:
                dist.multipack_norm += 1
    return dist


def _get_listing_map(session: Session, external_ids: list[str], chain: str) -> dict[str, str]:
    """Return {external_id: str(product_id)} for listings in *chain*."""
    if not external_ids:
        return {}
    rows = session.execute(
        select(ProductRetailerListing.external_id, ProductRetailerListing.product_id).where(
            ProductRetailerListing.external_id.in_(external_ids),
            ProductRetailerListing.chain == chain,
        )
    ).fetchall()
    return {row[0]: str(row[1]) for row in rows}


def _get_count_only_persisted_norms(
    session: Session,
    count_only_external_ids: set[str],
    listing_map: dict[str, str],
) -> dict[str, str | None]:
    """Return {external_id: package_size_norm} from the Product table.

    Keyed by external_id (not canonical product id) so AC1 can compare the
    expected norm per-SKU against the persisted value without aggregating by
    canonical.  Querying the DB directly (not the parsed CatalogProduct) is
    what proves the ingest layer stored the correct value.
    """
    canonical_to_ext: dict[str, list[str]] = {}
    for ext_id in count_only_external_ids:
        if ext_id in listing_map:
            canonical_id = listing_map[ext_id]
            canonical_to_ext.setdefault(canonical_id, []).append(ext_id)

    if not canonical_to_ext:
        return {}

    rows = session.execute(
        select(Product.id, Product.package_size_norm).where(Product.id.in_(canonical_to_ext.keys()))
    ).fetchall()
    canonical_norm: dict[str, str | None] = {str(row[0]): row[1] for row in rows}

    result: dict[str, str | None] = {}
    for canonical_id, ext_ids in canonical_to_ext.items():
        norm = canonical_norm.get(canonical_id)
        for ext_id in ext_ids:
            result[ext_id] = norm
    return result


# ── Planner helper ────────────────────────────────────────────────────────────


def _load_script(name: str):
    """Load a script from the scripts/ directory by filename and return the module."""
    path = _BACKEND / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _count_confirmed_mismatches(session: Session, chains: set[str]) -> int:
    """Run the remediation planner and count confirmed_norm_mismatch rows."""
    mod = _load_script("remediation_planner.py")
    rows = mod.run_plan(session, chains=chains)
    return sum(1 for r in rows if r.get("subtype") == "confirmed_norm_mismatch")


# ── Crawl helpers ─────────────────────────────────────────────────────────────


def _is_iga_count_only(item: dict) -> bool:
    """Return True if an IGA raw item has a count-only unitOfSize (N≥2 each/ea/pk/pack).

    IGA's size is a nested dict {"size": value, "type": "each"|"ea"|"pk"|"pack"|...}
    rather than a plain string — the regex path used for Woolworths/ALDI cannot apply.
    """
    uos = item.get("unitOfSize") or {}
    if not isinstance(uos, dict):
        return False
    size_type = (uos.get("type") or "").lower()
    if size_type not in ("each", "ea", "pk", "pack"):
        return False
    try:
        return int(float(uos.get("size") or 0)) > 1
    except (TypeError, ValueError):
        return False


def _identify_count_only_external_ids(raw_items: list[dict], chain: str = "woolworths") -> set[str]:
    """Return external IDs of raw items whose size field is count-only (e.g. '6PK', '30 pack').

    These are the candidates where combine_count_with_volume should fire.
    AC1 verifies at least one was successfully enriched.

    count=1 (e.g. "1EA", "1 each") is excluded because scrapers suppress it to None
    rather than enriching it — matching each scraper's own count > 1 filter.

    Field names are chain-specific: Woolworths uses "Stockcode"/"PackageSize";
    ALDI uses "sku"/"sellingSize"; IGA uses "productId"/"unitOfSize" (dict, not string).
    Unknown chains fall back to Woolworths fields.
    """
    id_field, size_field = _CHAIN_ITEM_FIELDS.get(chain, _CHAIN_ITEM_FIELDS["woolworths"])
    result: set[str] = set()
    for item in raw_items:
        if chain == "iga":
            if not _is_iga_count_only(item):
                continue
        else:
            size = item.get(size_field) or ""
            m = _RE_COUNT_ONLY_API_SIZE.match(str(size))
            if not m or int(m.group(1)) <= 1:
                continue
        ext_id = str(item.get(id_field, ""))
        if ext_id:
            result.add(ext_id)
    return result


async def _bounded_crawl(
    chain: str,
    category_name: str,
    max_pages: int,
) -> tuple[list[CatalogProduct], list[dict], list[str]]:
    """Authenticate, find target category, crawl up to *max_pages*.

    Returns (products, raw_products, errors).
    Products are fully parsed CatalogProduct objects.
    raw_products are the unmodified API dicts (used to identify count-only items).
    redis_client=None is intentional — auth is not cached for a one-shot run.
    """
    errors: list[str] = []
    scraper = get_catalog_scraper(chain, redis_client=None)

    # Limit page depth for the validation run
    scraper.config.max_pages_per_category = max_pages
    # Tighter delays — still polite but faster for an interactive validation
    scraper.config.min_delay_s = 1.5
    scraper.config.max_delay_s = 3.5

    logger.info("Authenticating with %s…", chain)
    try:
        auth = await scraper._get_or_refresh_auth()
    except Exception as exc:
        errors.append(f"Authentication failed: {exc}")
        return [], [], errors

    logger.info("Discovering categories…")
    try:
        categories = await scraper.discover_categories()
    except Exception as exc:
        errors.append(f"Category discovery failed: {exc}")
        return [], [], errors

    logger.info("Found %d categories", len(categories))
    target = next(
        (c for c in categories if category_name.lower() in c.name.lower()),
        None,
    )
    if target is None:
        names = [c.name for c in categories[:20]]
        errors.append(f"Category '{category_name}' not found. Available (first 20): {names}")
        return [], [], errors

    logger.info(
        "Crawling category '%s' (id=%s, max_pages=%d)…",
        target.name,
        target.id,
        max_pages,
    )
    try:
        raw = await scraper.crawl_category(target, auth)
    except Exception as exc:
        errors.append(f"crawl_category failed for '{target.name}': {exc}")
        return [], [], errors

    raw_products = _extract_raw_items(raw, chain)
    logger.info("Received %d raw product dicts from '%s'", len(raw_products), target.name)
    try:
        products = await scraper.parse_products(raw, target)  # parse_products expects the full dict
    except Exception as exc:
        errors.append(f"parse_products failed: {exc}")
        return [], raw_products, errors

    logger.info("Parsed %d CatalogProduct objects", len(products))
    return products, raw_products, errors


# ── Ingest helpers ────────────────────────────────────────────────────────────


def _run_ingest(products: list[CatalogProduct], chain: str, session: Session) -> dict:
    counts = ingest_catalog_products(products, chain, session)
    session.commit()
    return {"created": counts.created, "updated": counts.updated}


# ── Acceptance criteria evaluation ────────────────────────────────────────────


def _evaluate_criteria(result: ValidationResult) -> None:
    """Populate result.criteria_results.

    All checks operate exclusively on data from THIS crawl.  Nothing passes
    by accident from historical database state.
    """
    products = result.scraped_products

    # ── AC1 ───────────────────────────────────────────────────────────────────
    # At least one product that originally had a count-only API size (e.g. "6PK")
    # was enriched by the scraper to a multipack norm AND that exact norm was
    # persisted to package_size_norm in the database.  A persisted value that
    # merely contains "x" is insufficient — it must match the expected norm for
    # that specific SKU exactly, to rule out stale data from a prior ingest
    # (e.g. an existing "4x375mL" would otherwise make AC1 pass falsely).
    enriched_ext_ids = {
        ext_id
        for ext_id, expected in result.count_only_expected_norms.items()
        if expected is not None and "x" in expected
    }
    if not result.count_only_external_ids:
        result.criteria_results["ac1_enrichment_fired_on_scraped_data"] = False
        logger.warning(
            "AC1 FAIL: no count-only API products found in scraped set "
            "(count_only_external_ids is empty — category may not have pack products)"
        )
    elif not enriched_ext_ids:
        # All count-only products exist but none produced a multipack norm.
        # Distinguish: every item genuinely lacks volume evidence (Not Exercised)
        # vs the norm dict was never populated at all (structural FAIL).
        # The empty-dict guard prevents a vacuous all() from passing as Not Exercised.
        all_unenrichable = bool(result.count_only_expected_norms) and all(
            v is None for v in result.count_only_expected_norms.values()
        )
        if all_unenrichable:
            result.criteria_results["ac1_enrichment_fired_on_scraped_data"] = None
            logger.info(
                "AC1 NOT EXERCISED: %d count-only product(s) found but none had volume "
                "evidence in product names (e.g. produce bundles, kit items). "
                "Enrichment path exists but cannot fire on this category.",
                len(result.count_only_expected_norms),
            )
        else:
            result.criteria_results["ac1_enrichment_fired_on_scraped_data"] = False
            logger.warning(
                "AC1 FAIL: scraper produced no multipack norms for count-only products "
                "(combine_count_with_volume may not have fired or no volume was in product names)"
            )
    elif not result.count_only_persisted_norms:
        result.criteria_results["ac1_enrichment_fired_on_scraped_data"] = False
        logger.warning(
            "AC1 FAIL: count_only_persisted_norms not populated — "
            "cannot verify persisted package_size_norm values"
        )
    else:
        mismatched: list[tuple[str, str | None, str | None]] = []
        verified = 0
        for ext_id in enriched_ext_ids:
            expected = result.count_only_expected_norms[ext_id]
            persisted = result.count_only_persisted_norms.get(ext_id)
            if persisted == expected:
                verified += 1
            else:
                mismatched.append((ext_id, expected, persisted))
        if mismatched:
            result.criteria_results["ac1_enrichment_fired_on_scraped_data"] = False
            for ext_id, expected, persisted in mismatched:
                logger.warning(
                    "AC1 FAIL: external_id %s expected norm '%s' but DB has '%s'",
                    ext_id,
                    expected,
                    persisted,
                )
        else:
            result.criteria_results["ac1_enrichment_fired_on_scraped_data"] = True
            logger.info(
                "AC1 PASS: %d count-only product(s) verified with exact persisted norms",
                verified,
            )

    # ── AC2 ───────────────────────────────────────────────────────────────────
    # No canonical product created/used by this crawl appears in the audit's
    # single_multipack_collision list.  An empty new_product_ids set (e.g.
    # dry-run or all products already existed) is handled as "no evidence of
    # collision" — the check only has teeth when new_product_ids is populated.
    collisions = result.audit_findings.get("single_multipack_collision", [])
    collision_ids = {str(c.get("product_id")) for c in collisions}
    result.criteria_results["ac2_no_unexpected_merges"] = not bool(
        result.new_product_ids & collision_ids
    )

    # ── AC3 ───────────────────────────────────────────────────────────────────
    # Among the listings created by this crawl, no two products with different
    # package_size_norms (one known, one null) were merged into the same
    # canonical product.  A match would mean the symmetric soft veto failed.
    products_by_canonical: dict[str, list[CatalogProduct]] = {}
    for p in products:
        if not p.external_id:
            continue
        canonical_id = result.listing_map_first.get(p.external_id)
        if canonical_id is None:
            continue
        products_by_canonical.setdefault(canonical_id, []).append(p)

    ac3_pass = True
    for canonical_id, group in products_by_canonical.items():
        norms = {normalize_package_size(p.package_size) for p in group}
        if len(norms) > 1:
            # More than one distinct norm (including None) in the same canonical
            # product — the symmetric soft veto should have prevented this.
            ac3_pass = False
            logger.warning(
                "AC3 FAIL: canonical product %s holds listings with different norms "
                "from this crawl — soft veto may have failed. Norms found: %s",
                canonical_id,
                norms,
            )
            break
    result.criteria_results["ac3_separate_products"] = ac3_pass

    # ── AC4 ───────────────────────────────────────────────────────────────────
    # Two independent checks, both must pass:
    #   (a) Repeat ingest created zero new canonical products.
    #       A non-zero count means a duplicate was introduced.
    #   (b) Every listing still points to the same canonical product it was
    #       assigned on the first run.  A mismatch means a listing was reassigned.
    if result.listing_map_first and result.listing_map_repeat:
        repeat_created = result.ingest_counts_repeat.get("created", 0)
        if repeat_created > 0:
            logger.warning(
                "AC4 FAIL: repeat ingest created %d new canonical products",
                repeat_created,
            )
        moved = {
            eid: (result.listing_map_first[eid], result.listing_map_repeat.get(eid))
            for eid in result.listing_map_first
            if result.listing_map_first[eid] != result.listing_map_repeat.get(eid)
        }
        if moved:
            logger.warning("AC4 FAIL: listings reassigned after repeat ingest: %s", moved)
        result.criteria_results["ac4_idempotent_repeat_ingest"] = repeat_created == 0 and not bool(
            moved
        )
    else:
        # No listing data available (e.g. ingestion errors)
        result.criteria_results["ac4_idempotent_repeat_ingest"] = None

    # ── AC5 ───────────────────────────────────────────────────────────────────
    # The count of confirmed_norm_mismatch rows did not increase after ingest.
    # If the planner failed to run (count == -1), the criterion is treated as
    # failed rather than silently ignored.
    if result.baseline_plan_confirmed_count == -1 or result.post_plan_confirmed_count == -1:
        # Planner did not run — cannot certify; treat as failure
        result.criteria_results["ac5_no_new_audit_defects"] = False
        logger.warning(
            "AC5 FAIL: planner did not run (baseline=%d post=%d); " "cannot certify no new defects",
            result.baseline_plan_confirmed_count,
            result.post_plan_confirmed_count,
        )
    else:
        grew = result.post_plan_confirmed_count > result.baseline_plan_confirmed_count
        if grew:
            logger.warning(
                "AC5 FAIL: confirmed_norm_mismatch count grew from %d to %d",
                result.baseline_plan_confirmed_count,
                result.post_plan_confirmed_count,
            )
        result.criteria_results["ac5_no_new_audit_defects"] = not grew


_EXPECTED_CRITERIA: frozenset[str] = frozenset(
    {
        "ac1_enrichment_fired_on_scraped_data",
        "ac2_no_unexpected_merges",
        "ac3_separate_products",
        "ac4_idempotent_repeat_ingest",
        "ac5_no_new_audit_defects",
    }
)
"""All five criteria that must be present in a completed live-run result."""

_NOT_EXERCISED_ALLOWED: frozenset[str] = frozenset({"ac1_enrichment_fired_on_scraped_data"})
"""Criteria that may be None (Not Exercised) without blocking an overall PASS.

AC1 is the only allowed not-exercised criterion: the enrichment path exists but
cannot fire when the chosen category contains no enrichable count-only products
(e.g. produce bundles, kit items with no mL in the name).  All other criteria
must be explicitly True — a None there indicates incomplete verification.
"""


def _all_criteria_passed(result: ValidationResult) -> bool:
    """Return True only when all five criteria are present and all pass.

    Rules:
    - All five keys from _EXPECTED_CRITERIA must be present; a partial result
      (e.g. only AC2=True) must never report success.
    - Any explicit False blocks PASS.
    - A None on AC2–AC5 blocks PASS (incomplete verification).
    - AC1=None is acceptable (Not Exercised — enrichment path confirmed present
      but no enrichable count-only products in the sampled category).
    - Errors always block PASS.
    """
    if result.errors:
        return False
    if not _EXPECTED_CRITERIA.issubset(result.criteria_results):
        return False
    for k, v in result.criteria_results.items():
        if v is False:
            return False
        if v is None and k not in _NOT_EXERCISED_ALLOWED:
            return False
    return True


# ── Report generation ─────────────────────────────────────────────────────────


def _write_report(
    result: ValidationResult,
    out_dir: Path,
    chain: str,
    category_name: str,
    max_pages: int,
    dry_run: bool,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"validation_{ts}_{chain}.md"

    if dry_run:
        # Dry-run only evaluates AC1 at parse level; AC2–AC5 are None by design.
        ac1 = result.criteria_results.get("ac1_enrichment_fired_on_scraped_data")
        all_passed = ac1 is True and not result.errors
        status = "AC1 PASSED (parse-level only — no DB writes)" if all_passed else "FAILED"
    else:
        # Live run: every criterion must be explicitly True; None counts as failure.
        all_passed = _all_criteria_passed(result)
        status = "PASSED" if all_passed else "FAILED"

    lines = [
        "# Story #109 — Fresh Crawl Validation Report",
        "",
        f"**Chain:** {chain}  ",
        f"**Category:** {category_name}  ",
        f"**Max pages:** {max_pages} ({max_pages * 36} products max)  ",
        f"**Mode:** {'DRY RUN (no DB writes)' if dry_run else 'Live ingest'}  ",
        f"**Run at:** {datetime.now(timezone.utc).isoformat()}  ",
        f"**Overall result: {status}**",
        "",
        "---",
        "",
        "## Scraped products",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Products scraped | {len(result.scraped_products)} |",
        f"| With a package size | {sum(1 for p in result.scraped_products if p.package_size)} |",
        f"| Enriched (norm is multipack) | {sum(1 for p in result.scraped_products if p.package_size and 'x' in (normalize_package_size(p.package_size) or ''))} |",
        f"| Count-only fallback (norm=None) | {sum(1 for p in result.scraped_products if p.package_size and normalize_package_size(p.package_size) is None)} |",
        f"| No package size | {sum(1 for p in result.scraped_products if not p.package_size)} |",
        "",
    ]

    if not dry_run:
        lines += [
            "## Database metrics",
            "",
            "| Metric | Baseline | Post-ingest | Delta |",
            "|---|---|---|---|",
            f"| Total {chain} products | {result.baseline.total} | {result.post_ingest.total} | +{result.post_ingest.total - result.baseline.total} |",
            f"| With norm | {result.baseline.with_norm} | {result.post_ingest.with_norm} | +{result.post_ingest.with_norm - result.baseline.with_norm} |",
            f"| Null norm | {result.baseline.null_norm} | {result.post_ingest.null_norm} | +{result.post_ingest.null_norm - result.baseline.null_norm} |",
            f"| Multipack norm (NxVol) | {result.baseline.multipack_norm} | {result.post_ingest.multipack_norm} | +{result.post_ingest.multipack_norm - result.baseline.multipack_norm} |",
            "",
            "## Ingest summary",
            "",
            "| Run | Created | Updated |",
            "|---|---|---|",
            f"| First ingest | {result.ingest_counts_first.get('created', '—')} | {result.ingest_counts_first.get('updated', '—')} |",
            f"| Repeat ingest | {result.ingest_counts_repeat.get('created', '—')} | {result.ingest_counts_repeat.get('updated', '—')} |",
            f"| Confirmed-mismatch rows (baseline → post) | {result.baseline_plan_confirmed_count} → {result.post_plan_confirmed_count} | |",
            "",
        ]

    criteria_labels = {
        "ac1_enrichment_fired_on_scraped_data": "AC1 — Count-only pack enriched and persisted as multipack norm in DB",
        "ac2_no_unexpected_merges": "AC2 — No new canonical product appears in single/multipack collision list",
        "ac3_separate_products": "AC3 — No two scraped products with different norms share a canonical product",
        "ac4_idempotent_repeat_ingest": "AC4 — Zero new products on repeat ingest; listing associations stable",
        "ac5_no_new_audit_defects": "AC5 — Confirmed-norm-mismatch count did not increase",
    }
    lines += [
        "## Acceptance criteria",
        "",
        "| Key | Criterion | Result |",
        "|---|---|---|",
    ]
    for key, label in criteria_labels.items():
        passed = result.criteria_results.get(key)
        if passed is None:
            icon = "⬜ N/A"
        elif passed:
            icon = "✅ Pass"
        else:
            icon = "❌ Fail"
        lines.append(f"| {key} | {label} | {icon} |")

    if result.audit_findings:
        lines += [
            "",
            "## Audit findings",
            "",
            "| Category | Findings |",
            "|---|---|",
        ]
        for cat, findings in result.audit_findings.items():
            lines.append(f"| {cat} | {len(findings)} |")

    if result.plan_findings:
        subtypes: dict[str, int] = {}
        for row in result.plan_findings:
            st = row.get("subtype", "unknown")
            subtypes[st] = subtypes.get(st, 0) + 1
        lines += [
            "",
            "## Remediation plan",
            "",
            "| Subtype | Count |",
            "|---|---|",
        ]
        for st, n in sorted(subtypes.items()):
            lines.append(f"| {st} | {n} |")

    if result.errors:
        lines += ["", "## Errors", ""]
        for e in result.errors:
            lines.append(f"- {e}")

    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# ── Sample product display (dry-run / informational) ─────────────────────────


def _print_sample(products: list[CatalogProduct], n: int = 20) -> None:
    print(f"\n{'─'*80}")
    print(f"Sample products (first {min(n, len(products))} of {len(products)}):")
    print(f"{'─'*80}")
    for p in products[:n]:
        norm = normalize_package_size(p.package_size)
        print(
            f"  [{p.external_id:>10}] {p.product_name[:50]:<50} "
            f"raw={str(p.package_size):<20} norm={str(norm)}"
        )
    print(f"{'─'*80}\n")


# ── Main ──────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Multi-retailer fresh-crawl & catalog integrity validation"
    )
    p.add_argument(
        "--chain",
        default="woolworths",
        help=(
            "Retailer to crawl and ingest (default: woolworths). "
            "Must be registered in scrapers/catalog_registry.py. "
            "Supported: woolworths, aldi, iga, coles."
        ),
    )
    p.add_argument(
        "--category-name",
        default="Beer",
        help="Category name to target (default: Beer). Available names are chain-specific.",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=2,
        help="Max pages per category, 36 products/page (default: 2)",
    )
    p.add_argument(
        "--out-dir",
        default="validation_output",
        help="Output directory for the report (default: validation_output)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and display products without writing to the database",
    )
    p.add_argument(
        "--chains",
        default=None,
        help=(
            "Comma-separated chains for audit scope. "
            "Defaults to the selected --chain when omitted."
        ),
    )
    p.add_argument(
        "--confirm-db",
        default=None,
        metavar="DB_NAME",
        dest="confirm_db",
        help=(
            "Required for live (non-dry-run) runs. Pass the exact name of the database "
            "you intend to write to. The script queries SELECT current_database() and "
            "aborts if the names do not match exactly (case-insensitive). "
            "Example: --confirm-db dealfindio_dev"
        ),
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    chain = args.chain
    audit_chains = {c.strip() for c in args.chains.split(",")} if args.chains else {chain}
    out_dir = Path(args.out_dir)

    # ── Chain registry validation ─────────────────────────────────────────────
    # Validate before any network or DB operations so an unknown chain name is
    # caught immediately with a clear message.
    try:
        get_catalog_scraper(chain, redis_client=None)
    except ValueError as exc:
        logger.error("Unknown chain '%s': %s", chain, exc)
        return 1

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        logger.error("DATABASE_URL is not set. Aborting.")
        return 1

    # Build a dedicated engine for this script run — we do not go through
    # app.deps.init_database because the script runs standalone and does not
    # start the FastAPI app lifecycle.
    _engine = create_engine(db_url, pool_pre_ping=True, echo=False)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    result = ValidationResult()

    # ── DB safety check ───────────────────────────────────────────────────────
    # Open a throwaway session just to query the database name before doing
    # anything else.  URL-text heuristics are not reliable (connection strings
    # can be aliases); querying the server directly is authoritative.
    _safety_session: Session = _SessionLocal()
    try:
        db_name = _check_db_name(_safety_session) or ""
    finally:
        _safety_session.close()

    if not any(frag in db_name.lower() for frag in _SAFE_DB_FRAGMENTS):
        logger.error(
            "Database name '%s' does not contain a recognised safe fragment %s. "
            "This script must only run against a development or isolated database. "
            "If this is intentional, rename the database to include one of those "
            "fragments.",
            db_name,
            _SAFE_DB_FRAGMENTS,
        )
        return 1

    # ── Explicit identity confirmation for live (write) runs ─────────────────
    # --confirm-db requires the caller to explicitly name the target database.
    # This prevents accidental writes when DATABASE_URL is an alias or points to
    # a different environment than expected.
    if not args.dry_run:
        if not args.confirm_db:
            logger.error(
                "Live runs require --confirm-db <database-name>. "
                "The currently connected database is '%s'. "
                "If this is the intended target, re-run with: --confirm-db %s",
                db_name,
                db_name,
            )
            return 1
        if args.confirm_db.lower() != db_name.lower():
            logger.error(
                "--confirm-db '%s' does not match the actual database '%s'. Aborting.",
                args.confirm_db,
                db_name,
            )
            return 1
        logger.info("Database identity confirmed: '%s' (matches --confirm-db).", db_name)
    else:
        logger.info("Dry-run mode: connected to '%s' (no writes will be made).", db_name)

    # ── Crawl ─────────────────────────────────────────────────────────────────
    logger.info(
        "Starting bounded %s crawl — category='%s', max_pages=%d",
        chain,
        args.category_name,
        args.max_pages,
    )
    products, raw_products, crawl_errors = asyncio.run(
        _bounded_crawl(chain, args.category_name, args.max_pages)
    )
    result.scraped_products = products
    result.count_only_external_ids = _identify_count_only_external_ids(raw_products, chain)
    result.errors.extend(crawl_errors)

    if not products:
        logger.error("No products scraped. Errors: %s", crawl_errors)
        _print_sample([], 0)
        report_path = _write_report(
            result, out_dir, chain, args.category_name, args.max_pages, args.dry_run
        )
        logger.info("Report written to %s", report_path)
        return 1

    _print_sample(products)

    external_ids = [p.external_id for p in products if p.external_id]
    _ext_id_to_cp = {cp.external_id: cp for cp in products if cp.external_id}
    result.count_only_expected_norms = {
        ext_id: normalize_package_size(_ext_id_to_cp[ext_id].package_size)
        for ext_id in result.count_only_external_ids
        if ext_id in _ext_id_to_cp
    }

    if args.dry_run:
        logger.info("Dry run: %d products parsed. Skipping DB writes.", len(products))
        # Only AC1 can be evaluated without a DB; uses count_only_external_ids.
        count_only_products = [
            p for p in products if p.external_id in result.count_only_external_ids
        ]
        enriched = sum(
            1 for p in count_only_products if "x" in (normalize_package_size(p.package_size) or "")
        )
        result.criteria_results = {
            "ac1_enrichment_fired_on_scraped_data": bool(count_only_products) and enriched > 0,
            "ac2_no_unexpected_merges": None,
            "ac3_separate_products": None,
            "ac4_idempotent_repeat_ingest": None,
            "ac5_no_new_audit_defects": None,
        }
        report_path = _write_report(
            result, out_dir, chain, args.category_name, args.max_pages, args.dry_run
        )
        logger.info("Dry-run report: %s", report_path)
        return 0

    # ── Live DB operations ────────────────────────────────────────────────────
    session: Session = _SessionLocal()
    try:
        # Baseline metrics
        logger.info("Capturing baseline DB metrics…")
        result.baseline = _norm_distribution(session, chain)
        logger.info(
            "Baseline: total=%d with_norm=%d null_norm=%d multipack=%d",
            result.baseline.total,
            result.baseline.with_norm,
            result.baseline.null_norm,
            result.baseline.multipack_norm,
        )

        # Baseline planner count for AC5 before/after comparison
        logger.info("Capturing baseline confirmed-norm-mismatch count (for AC5)…")
        try:
            result.baseline_plan_confirmed_count = _count_confirmed_mismatches(
                session, audit_chains
            )
            logger.info(
                "Baseline confirmed_norm_mismatch count: %d",
                result.baseline_plan_confirmed_count,
            )
        except Exception as exc:
            logger.warning("Baseline planner failed: %s", exc)
            result.errors.append(f"Baseline planner error: {exc}")
            # Leave at -1 — AC5 will fail

        # First ingest
        logger.info("Ingesting %d products (first run)…", len(products))
        result.ingest_counts_first = _run_ingest(products, chain, session)
        logger.info(
            "First ingest: created=%d updated=%d",
            result.ingest_counts_first["created"],
            result.ingest_counts_first["updated"],
        )

        # Capture listing map and new product IDs after first ingest (for AC2, AC3, AC4)
        result.listing_map_first = _get_listing_map(session, external_ids, chain)
        result.new_product_ids = set(result.listing_map_first.values())

        # Query persisted package_size_norm for count-only products (for AC1)
        result.count_only_persisted_norms = _get_count_only_persisted_norms(
            session, result.count_only_external_ids, result.listing_map_first
        )
        logger.info(
            "Count-only products: %d identified, %d with expected multipack norm, "
            "%d with exact persisted match",
            len(result.count_only_external_ids),
            sum(1 for n in result.count_only_expected_norms.values() if n and "x" in n),
            sum(
                1
                for ext_id, exp in result.count_only_expected_norms.items()
                if exp and "x" in exp and result.count_only_persisted_norms.get(ext_id) == exp
            ),
        )

        # Post-ingest distribution
        result.post_ingest = _norm_distribution(session, chain)
        logger.info(
            "Post-ingest: total=%d with_norm=%d null_norm=%d multipack=%d",
            result.post_ingest.total,
            result.post_ingest.with_norm,
            result.post_ingest.null_norm,
            result.post_ingest.multipack_norm,
        )

        # Repeat ingest (idempotency)
        logger.info("Re-ingesting %d products (idempotency check)…", len(products))
        result.ingest_counts_repeat = _run_ingest(products, chain, session)
        logger.info(
            "Repeat ingest: created=%d updated=%d",
            result.ingest_counts_repeat["created"],
            result.ingest_counts_repeat["updated"],
        )
        if result.ingest_counts_repeat.get("created", 0) > 0:
            logger.warning(
                "IDEMPOTENCY ISSUE: repeat ingest created %d new products",
                result.ingest_counts_repeat["created"],
            )

        # Capture listing map after repeat ingest (for AC4 comparison)
        result.listing_map_repeat = _get_listing_map(session, external_ids, chain)

        # Audit
        logger.info("Running package-size audit (chains=%s)…", audit_chains)
        try:
            audit_mod = _load_script("audit_package_sizes.py")
            result.audit_findings = audit_mod.run_audit(session, chains=audit_chains)
            total_findings = sum(len(v) for v in result.audit_findings.values())
            logger.info(
                "Audit: %d findings across %d categories",
                total_findings,
                len(result.audit_findings),
            )
        except Exception as exc:
            logger.warning("Audit failed: %s", exc)
            result.errors.append(f"Audit error: {exc}")

        # Post-ingest planner count for AC5
        logger.info("Running post-ingest planner (chains=%s)…", audit_chains)
        try:
            result.post_plan_confirmed_count = _count_confirmed_mismatches(session, audit_chains)
            # Also store full plan for the report
            planner_mod = _load_script("remediation_planner.py")
            result.plan_findings = planner_mod.run_plan(session, chains=audit_chains)
            logger.info(
                "Post-ingest confirmed_norm_mismatch count: %d",
                result.post_plan_confirmed_count,
            )
        except Exception as exc:
            logger.warning("Post-ingest planner failed: %s", exc)
            result.errors.append(f"Post-ingest planner error: {exc}")
            # Leave post_plan_confirmed_count at -1 — AC5 will fail

    finally:
        session.close()

    # ── Criteria evaluation ───────────────────────────────────────────────────
    _evaluate_criteria(result)

    # Every criterion must be explicitly True; None and errors both block PASS.
    all_passed = _all_criteria_passed(result)

    report_path = _write_report(
        result, out_dir, chain, args.category_name, args.max_pages, args.dry_run
    )
    logger.info("Report written to %s", report_path)

    if all_passed:
        logger.info("All acceptance criteria PASSED.")
        return 0
    else:
        failed = [k for k, v in result.criteria_results.items() if v is False]
        logger.error("Acceptance criteria FAILED: %s", failed)
        return 1


if __name__ == "__main__":
    sys.exit(main())

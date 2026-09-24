"""
Purge Woolworths marketplace products from the database.

Woolworths injects third-party Everyday Market products into regular category
pages. These have 10-digit stockcodes (range 1094737927–1141985970) — a
distinct range from real Woolworths grocery SKUs which are ≤7 digits.

This script deletes all products from catalog_woolworths whose external_id
has 10 digits, along with their price_reports.

The scraper fix (IsMarketProduct check in _parse_item) prevents re-ingestion.

Usage:
    cd backend
    python3 scripts/purge_woolworths_marketplace.py [--dry-run]

Options:
    --dry-run   Show counts and sample names without touching the DB.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("SECRET_KEY", "cleanup-script")

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PriceReport, Product

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# All Woolworths marketplace (Everyday Market) products have 10-digit stockcodes.
# Real grocery SKUs are ≤7 digits. This is a clean, verified signal — no API
# calls needed.
_MARKETPLACE_STOCKCODE_LENGTH = 10


def run(dry_run: bool) -> None:
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)

    with Session(engine) as session:
        # Count and sample before doing anything
        count_result = session.execute(
            select(func.count(Product.id)).where(
                Product.external_source == "catalog_woolworths",
                func.length(Product.external_id) == _MARKETPLACE_STOCKCODE_LENGTH,
            )
        ).scalar_one()

        logger.info("Found %d marketplace products to delete", count_result)

        if count_result == 0:
            logger.info("Nothing to do.")
            return

        # Show a sample
        samples = session.execute(
            select(Product.external_id, Product.name, Product.category)
            .where(
                Product.external_source == "catalog_woolworths",
                func.length(Product.external_id) == _MARKETPLACE_STOCKCODE_LENGTH,
            )
            .limit(10)
        ).all()
        logger.info("Sample products:")
        for sc, name, cat in samples:
            logger.info("  [%s] (%s) %s", sc, cat, name[:80])

        # Count affected price_reports
        pr_count = session.execute(
            select(func.count(PriceReport.id))
            .where(
                PriceReport.product_id.in_(
                    select(Product.id).where(
                        Product.external_source == "catalog_woolworths",
                        func.length(Product.external_id) == _MARKETPLACE_STOCKCODE_LENGTH,
                    )
                )
            )
        ).scalar_one()
        logger.info("Associated price_reports: %d", pr_count)

        if dry_run:
            logger.info("DRY RUN — no changes made.")
            return

        logger.info("Deleting price_reports…")
        pr_del = session.execute(
            delete(PriceReport).where(
                PriceReport.product_id.in_(
                    select(Product.id).where(
                        Product.external_source == "catalog_woolworths",
                        func.length(Product.external_id) == _MARKETPLACE_STOCKCODE_LENGTH,
                    )
                )
            )
        )
        logger.info("Deleted %d price_reports", pr_del.rowcount)

        logger.info("Deleting products…")
        p_del = session.execute(
            delete(Product).where(
                Product.external_source == "catalog_woolworths",
                func.length(Product.external_id) == _MARKETPLACE_STOCKCODE_LENGTH,
            )
        )
        logger.info("Deleted %d products", p_del.rowcount)

        session.commit()
        logger.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purge Woolworths marketplace products")
    parser.add_argument("--dry-run", action="store_true", help="Show counts without deleting")
    args = parser.parse_args()
    run(dry_run=args.dry_run)

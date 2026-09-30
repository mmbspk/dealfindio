"""remove Dan Murphy's data from all tables

Revision ID: h3c4d5e6f7a8
Revises: g2b3c4d5e6f7
Create Date: 2026-09-30 00:00:00.000000

Dan Murphy's is a liquor-only retailer and has been removed from dealfindio.
This migration purges all remaining data: stores, products (via catalog_dan_murphys
external_source), price_reports, dedup candidates, and crawl run history.
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "h3c4d5e6f7a8"
down_revision: str | None = "g2b3c4d5e6f7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Price reports for DM products (by product FK)
    conn.execute(sa.text("""
        DELETE FROM price_reports
        WHERE product_id IN (
            SELECT id FROM products WHERE external_source = 'catalog_dan_murphys'
        )
    """))

    # 2. Price reports for DM stores (by store FK)
    conn.execute(sa.text("""
        DELETE FROM price_reports
        WHERE store_id IN (
            SELECT id FROM stores WHERE chain = 'dan_murphys'
        )
    """))

    # 3. Product match candidates referencing DM products
    conn.execute(sa.text("""
        DELETE FROM product_match_candidates
        WHERE product_a_id IN (
            SELECT id FROM products WHERE external_source = 'catalog_dan_murphys'
        )
        OR product_b_id IN (
            SELECT id FROM products WHERE external_source = 'catalog_dan_murphys'
        )
    """))

    # 4. Products
    conn.execute(sa.text(
        "DELETE FROM products WHERE external_source = 'catalog_dan_murphys'"
    ))

    # 5. Catalog crawl run history
    conn.execute(sa.text(
        "DELETE FROM catalog_crawl_runs WHERE chain = 'dan_murphys'"
    ))

    # 6. Stores
    conn.execute(sa.text(
        "DELETE FROM stores WHERE chain = 'dan_murphys'"
    ))


def downgrade() -> None:
    # Data is gone — downgrade is a no-op (re-run the seed script to restore stores).
    pass

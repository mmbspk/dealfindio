"""dedup products and add unique constraints on (external_source, external_id) and gtin

Revision ID: i4d5e6f7a8b9
Revises: h3c4d5e6f7a8
Create Date: 2026-09-30 00:00:00.000000
"""

from alembic import op

revision = "i4d5e6f7a8b9"
down_revision = "h3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1 — build a mapping of (drop_id -> keep_id) for duplicate products.
    # Keep the row with the most-recent created_at (highest id as tiebreak).
    # All duplicates are on (external_source, external_id).
    op.execute(
        """
        CREATE TEMP TABLE _product_dedup AS
        WITH ranked AS (
            SELECT
                p.id,
                FIRST_VALUE(p.id) OVER (
                    PARTITION BY p.external_source, p.external_id
                    ORDER BY p.created_at DESC NULLS LAST, p.id DESC
                ) AS keep_id
            FROM products p
            WHERE p.external_source IS NOT NULL
              AND p.external_id   IS NOT NULL
        )
        SELECT id AS drop_id, keep_id
        FROM ranked
        WHERE id <> keep_id
        """
    )

    # Step 2 — re-point price_reports from the dropped product to the keeper.
    op.execute(
        """
        UPDATE price_reports pr
        SET product_id = d.keep_id
        FROM _product_dedup d
        WHERE pr.product_id = d.drop_id
        """
    )

    # Step 3 — delete product_match_candidates that reference dropped rows.
    # These are auto-generated and will be recreated on the next crawl.
    op.execute(
        """
        DELETE FROM product_match_candidates
        WHERE product_a_id IN (SELECT drop_id FROM _product_dedup)
           OR product_b_id IN (SELECT drop_id FROM _product_dedup)
        """
    )

    # Step 4 — delete the duplicate product rows.
    op.execute(
        """
        DELETE FROM products
        WHERE id IN (SELECT drop_id FROM _product_dedup)
        """
    )

    op.execute("DROP TABLE _product_dedup")

    # Step 5 — unique partial index on (external_source, external_id) where both non-null.
    # Partial so NULL values (products without a known SKU) are never constrained.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_products_external_source_id
        ON products (external_source, external_id)
        WHERE external_source IS NOT NULL AND external_id IS NOT NULL
        """
    )

    # Step 6 — unique partial index on gtin where non-null.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_products_gtin
        ON products (gtin)
        WHERE gtin IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_products_gtin")
    op.execute("DROP INDEX IF EXISTS uq_products_external_source_id")

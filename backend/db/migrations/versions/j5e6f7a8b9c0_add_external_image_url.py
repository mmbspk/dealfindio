"""add_external_image_url

Adds ``external_image_url`` to products — the original retailer CDN URL scraped
by catalog crawlers.  ``image_url`` is now reserved exclusively for self-hosted
images written by the OFF enrichment pipeline.

This migration also backfills existing rows: any ``image_url`` that looks like a
retailer CDN URL is moved to ``external_image_url`` and ``image_url`` is set to
NULL, so the enrichment task can find and process those rows.

Revision ID: j5e6f7a8b9c0
Revises: a1f2c3d4e5f6
Create Date: 2026-10-02 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'j5e6f7a8b9c0'
down_revision: str | None = 'i4d5e6f7a8b9'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Retailer CDN host fragments — any image_url containing one of these is
# treated as a hotlinked retailer image and moved to external_image_url.
_CDN_PATTERNS = (
    'woolworths.media',
    'coles.',
    'aldi.',
    'igasupermarkets',
    'drakes',
)


def upgrade() -> None:
    op.add_column(
        'products',
        sa.Column('external_image_url', sa.String(length=500), nullable=True),
    )

    # Backfill: copy retailer CDN URLs → external_image_url, NULL out image_url.
    conn = op.get_bind()
    for pattern in _CDN_PATTERNS:
        conn.execute(
            sa.text(
                "UPDATE products "
                "SET external_image_url = image_url, image_url = NULL "
                "WHERE image_url IS NOT NULL "
                "  AND external_image_url IS NULL "
                f"  AND image_url LIKE '%{pattern}%'"  # noqa: S608 — migration-only, no user input
            )
        )

    # Update the partial index so it still covers rows with no self-hosted image.
    # (The index was created in the previous migration; it's still valid — rows
    # with image_url NULL are exactly what the enricher should process.)


def downgrade() -> None:
    # Restore hotlinked URLs back to image_url where we moved them.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE products "
            "SET image_url = external_image_url "
            "WHERE image_url IS NULL AND external_image_url IS NOT NULL"
        )
    )
    op.drop_column('products', 'external_image_url')

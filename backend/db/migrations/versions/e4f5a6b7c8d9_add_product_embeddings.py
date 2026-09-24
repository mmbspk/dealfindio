"""add_product_embeddings

Adds pgvector extension and a 384-dim name_embedding column to products for
semantic search (Phase B). Uses HNSW index for fast approximate nearest-neighbour
queries. Embedding model: all-MiniLM-L6-v2.

Revision ID: e4f5a6b7c8d9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-05 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Add the column as native vector(384) — required for HNSW index and cosine ops.
    op.execute("""
        ALTER TABLE products
        ADD COLUMN IF NOT EXISTS name_embedding vector(384)
    """)

    # HNSW index for fast ANN cosine search.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_products_name_embedding_hnsw
        ON products
        USING hnsw (name_embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        WHERE name_embedding IS NOT NULL
    """)

    # Partial index so the backfill task can cheaply find un-embedded rows.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_products_embedding_null
        ON products (id)
        WHERE name_embedding IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_products_name_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_products_embedding_null")
    op.drop_column("products", "name_embedding")
    # Intentionally do NOT drop the vector extension — other tables may use it.

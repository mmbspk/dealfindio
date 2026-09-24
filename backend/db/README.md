# Database Schema

**Alembic migrations are the single source of truth for the dealfindio schema.**

The SQLAlchemy ORM models in `app/models/` define the schema; Alembic
migrations in `db/migrations/versions/` are generated from those models and are
what actually build the database.

## Create / update the database

```bash
cd backend/db/migrations
poetry run alembic upgrade head        # apply all migrations
```

The baseline migration (`d1ddbe5a0eef`) creates all tables plus the
extensions (postgis, pg_trgm, uuid-ossp, cube, earthdistance) and the
specialized indexes that autogenerate can't infer:

- `idx_products_name_trgm` — trigram GIN index for fuzzy product-name search
- `idx_stores_location` / `idx_price_reports_location` — earthdistance GiST
  indexes for nearby-store / nearby-report queries

## Add a new migration

After changing an ORM model:

```bash
cd backend/db/migrations
poetry run alembic revision --autogenerate -m "describe your change"
# review the generated file, then:
poetry run alembic upgrade head
```

Autogenerate does **not** detect expression/trigram/spatial indexes — add those
manually via `op.execute("CREATE INDEX ...")` in the migration, as the baseline
does.

## `schema.reference.sql`

`schema.reference.sql` (formerly `schema.sql`) is the original hand-written DDL.
It is kept for reference only and is **not** applied — it had drifted from the
ORM models (e.g. it was missing `products.image_url`). Do not use it to build
the database; use Alembic.

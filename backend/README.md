# Backend

FastAPI + SQLAlchemy + Celery backend for dealfindio.

## Setup

```bash
# Install dependencies
make install

# Start development environment (PostgreSQL + Redis)
make dev

# Run migrations
make migrate-upgrade

# Start API server
poetry run uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

## Project Structure

```
app/
├── main.py              # FastAPI app factory
├── config.py            # Pydantic settings
├── deps.py              # Dependency injection
├── routers/             # REST endpoints
├── models/              # SQLAlchemy ORM
├── schemas/             # Pydantic request/response
├── services/            # Business logic
└── workers/             # Celery tasks
scrapers/               # Web scraping (Playwright, PDF, OCR)
ml/                     # ML models (Prophet, ARIMA)
db/                     # Database schema + migrations
tests/                  # pytest suite + fixtures
```

## Development

```bash
# Run tests
make test

# Run with coverage
make test-cov

# Format code
make format

# Type checking
make type-check

# Lint
make lint

# View Docker logs
make dev-logs

# Reset dev database
make dev-reset

# Stop services
make dev-stop
```

## Migrations

```bash
# Create new migration
make migrate-create msg="add_new_table"

# Apply migrations
make migrate-upgrade

# Rollback one migration
make migrate-downgrade
```

## Architecture

### Database

- PostgreSQL 16 + PostGIS (geospatial queries)
- SQLAlchemy 2.0 ORM
- Alembic for migrations
- Tables: stores, products, price_reports, user_profiles, watchlist, notifications_log, predictions, flyer_scrape_runs

### API

- FastAPI 0.104+
- Firebase JWT token verification
- Rate limiting
- CORS middleware
- Automatic OpenAPI/Swagger docs

### Workers

- Celery 5.3+
- Redis broker
- Beat scheduler for periodic tasks
- Tasks: flyer scraping, ML refit, notification dispatch, report validation

### Scrapers

- Playwright for DOM extraction
- pdfplumber + pytesseract for PDF/OCR
- Chain-specific: Coles, Woolworths, ALDI, IGA

### ML

- Prophet for time-series forecasting
- ARIMA fallback for sparse data
- Weekly model refit

## Testing

The default lane runs on SQLite in-memory (fast, no services needed) and
enforces a coverage gate (fail under 60%).

```bash
# Default lane: all non-pg tests + coverage gate
poetry run pytest

# Specific file
poetry run pytest tests/test_api.py -v

# HTML coverage report
poetry run pytest --cov=app --cov-report=html
```

### Postgres + PostGIS lane

Some behavior is Postgres-only (PostGIS `ll_to_earth`, psycopg2 DSN, trigram
search) and can't be exercised on SQLite. Those tests are marked `pg` and are
skipped by default. To run them against a real Postgres+PostGIS DB:

```bash
# One-time: create the test database (extensions are created by the fixture)
createdb -O dealfindio dealfindio_test

# Run the pg lane (smoke tests: health, trigram search, nearby-stores)
TEST_DATABASE_URL=postgresql://dealfindio:dealfindio_dev@localhost:5432/dealfindio_test \
  poetry run pytest -m pg --no-cov
```

Run the pg lane locally before deploying and in CI — it catches the class of
Postgres-only bug that the SQLite lane cannot.


## Environment Variables

See `.env.example`. Copy to `.env` and fill in secrets.

Key variables:
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `FIREBASE_SERVICE_ACCOUNT_JSON`: Firebase service account key path
- `ANTHROPIC_API_KEY`: Claude API key
- `ENVIRONMENT`: development / production
- `LOG_LEVEL`: DEBUG / INFO / WARNING / ERROR

## Code Quality

```bash
# Lint with ruff
ruff check .

# Format with black
black .

# Type check with pyright
pyright
```

Pre-commit hooks are configured (see `.pre-commit-config.yaml`):

```bash
pre-commit install
pre-commit run --all-files
```

## Deployment

Docker image builds from Dockerfile (multi-stage, production-optimized).

Railway deployment configured in `../../infra/railway.toml`.

```bash
# Build locally
docker build -t dealfindio-backend .

# Run container
docker run -p 8000:8000 --env-file .env dealfindio-backend
```

## Documentation

API docs auto-generated at `/docs` (Swagger) and `/redoc` (ReDoc).

Database schema in `db/schema.sql`.

Full project plan: `../../.claude/plans/rosy-dreaming-lemon.md`

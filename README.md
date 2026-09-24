# dealfindio

Australian grocery deal-finding app. Find local deals near you, build a watchlist, and get notified when prices drop.

**Status:** Phase 1 - Core infrastructure setup

## Quick Start (Native — no Docker)

```bash
# 1. Install backend deps + Playwright browser (one-time)
cd backend
poetry install
poetry run playwright install chromium

# 2. Run migrations
poetry run alembic upgrade head

# 3. Start everything (API + Celery worker + beat + web) in separate Terminal tabs
cd ..
./dev.sh
```

- API → http://localhost:8000
- Web → http://localhost:5173
- Admin → http://localhost:5173/admin

## Development

### Backend

```bash
# Run tests
make test

# Run with coverage
make test-cov

# Format code
make format

# Type checking
make type-check

# View logs
make dev-logs

# Stop services
make dev-stop
```

### Project Structure

```
dealfindio/
├── backend/          # FastAPI + SQLAlchemy + Celery
├── web/              # React + Vite + TypeScript
├── mobile/           # React Native + Expo
└── infra/            # Railway + deployment config
```

## Architecture

- **Backend:** Python 3.12, FastAPI, PostgreSQL (PostGIS), Redis, Celery
- **Frontend:** React + TypeScript + Vite, React Native (Expo)
- **Auth:** Firebase Auth
- **Notifications:** Firebase Cloud Messaging (FCM)
- **Hosting:** Railway (backend) + Vercel (web)

## Tech Stack

See `backend/pyproject.toml` for full dependencies.

Core libraries:
- FastAPI: API framework
- SQLAlchemy 2.0: ORM
- Celery: Task queue
- Playwright: Web scraping
- Prophet: ML predictions
- anthropic: Claude API integration

## Environment Variables

Copy `.env.example` to `.env` in each directory and fill in values:

```bash
cd backend
cp .env.example .env
```

## Testing

```bash
cd backend
make test              # Run all tests
make test-cov          # With coverage report
```

## Code Quality

```bash
cd backend
make lint              # Check with ruff
make format            # Format with black + ruff
make type-check        # Type check with pyright
```

## Deployment

Configured for Railway. See `infra/railway.toml`.

```bash
# Deploy to production
git push origin main   # Triggers CI/CD pipeline
```

## Contributing

1. Create a feature branch
2. Make changes
3. Run `make format && make type-check && make test`
4. Submit PR

## License

Proprietary - dealfindio 2026

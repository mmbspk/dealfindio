# Getting Started

Local development setup for DealFindio (backend API, web frontend, mobile app).

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | [python.org](https://python.org) or `pyenv` |
| Poetry | 1.8+ | `curl -sSL https://install.python-poetry.org \| python3 -` |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) or `nvm` |
| Docker Desktop | Latest | [docker.com](https://docker.com) |
| Xcode (macOS, optional) | 16+ | Mac App Store — for iOS Simulator |
| Android Studio (optional) | Latest | [developer.android.com](https://developer.android.com/studio) — for Android Emulator |

You will also need accounts / keys for:
- **Firebase** — authentication and (optionally) push notifications
- **Anthropic** — Claude API key for receipt OCR and AI deduplication
- **Resend** — email notifications (optional in dev — alerts just won't send)

---

## 1. Clone the repository

```bash
git clone https://github.com/mmbspk/dealfindio.git
cd dealfindio
```

---

## 2. Start infrastructure

PostgreSQL 16 + PostGIS and Redis 7 are provided via Docker Compose:

```bash
docker-compose -f docker-compose.dev.yml up -d
```

This starts:
- PostgreSQL on `localhost:5432` (user: `dealfindio`, password: `dealfindio_dev`, DB: `dealfindio_dev`)
- Redis on `localhost:6379`

---

## 3. Install backend dependencies

```bash
cd backend
poetry install
poetry run playwright install chromium   # downloads Chromium for scraper tests
```

---

## 4. Configure backend environment

```bash
cp .env.example .env
```

Then edit `.env`. Required variables:

```dotenv
DATABASE_URL=postgresql+asyncpg://dealfindio:dealfindio_dev@localhost:5432/dealfindio_dev
REDIS_URL=redis://localhost:6379/0
FIREBASE_PROJECT_ID=your-firebase-project-id
FIREBASE_SERVICE_ACCOUNT_JSON=/path/to/your-service-account-key.json
ANTHROPIC_API_KEY=sk-ant-...
SECRET_KEY=any-random-secret-string
```

See [[Architecture#Environment-Variables]] for the full variable reference.

---

## 5. Run database migrations

```bash
cd backend
poetry run alembic upgrade head
```

---

## 6. Install web dependencies

```bash
cd web
npm install
```

Copy the web env file:

```bash
cp .env.example .env
```

The defaults work for local dev (`VITE_API_URL=http://localhost:8010`). You only need to set `VITE_FIREBASE_*` values from your Firebase project settings.

---

## 7. Start all services

On macOS, one command opens four Terminal tabs:

```bash
# from the repo root
./dev.sh
```

Or start each service manually in separate terminals:

```bash
# Terminal 1 — FastAPI
cd backend && poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8010

# Terminal 2 — Celery worker
cd backend && poetry run celery -A app.workers.celery_app worker -Q scraper,catalog,celery --loglevel=info -c 2

# Terminal 3 — Celery beat (scheduled tasks)
cd backend && poetry run celery -A app.workers.celery_app beat --loglevel=info

# Terminal 4 — Vite web dev server
cd web && npm run dev
```

---

## 8. Verify it's running

```bash
curl http://localhost:8010/health
# → {"status":"ok","version":"0.1.0","checks":{"database":"ok","redis":"ok","firebase":"ok"}}
```

Then open:
- **Web app** → http://localhost:5173
- **API docs** → http://localhost:8010/docs
- **Admin panel** → http://localhost:5173/admin

---

## Firebase Setup

1. Go to [console.firebase.google.com](https://console.firebase.google.com)
2. Create a project (or use an existing one)
3. Enable **Authentication → Sign-in method → Email/Password** (and/or Google)
4. Download the service account key:
   - **Project settings → Service accounts → Generate new private key**
   - Save the JSON file somewhere on your machine (outside the repo)
   - Set `FIREBASE_SERVICE_ACCOUNT_JSON=/absolute/path/to/key.json` in `backend/.env`
5. Copy web SDK config values:
   - **Project settings → Your apps → Web app → SDK setup and configuration**
   - Paste the values into `web/.env` as `VITE_FIREBASE_*`

---

## Mobile App Setup

```bash
cd mobile
npm install
```

Then start:

```bash
npm start       # Expo Go — scan the QR code with your phone
npm run ios     # iOS Simulator (requires Xcode on macOS)
npm run android # Android Emulator
```

For physical device testing, update `app.config.js` (or `app.json`) to point `expo.extra.apiUrl` to your machine's LAN IP address, e.g. `http://192.168.1.100:8010`.

Full mobile guide → [docs/local-mobile-development.md](https://github.com/mmbspk/dealfindio/blob/main/docs/local-mobile-development.md)

---

## Useful Makefile Targets (backend)

```bash
cd backend

make format          # black + ruff --fix
make lint            # ruff check
make type-check      # pyright
make test            # pytest
make test-cov        # pytest + HTML coverage report (opens htmlcov/index.html)
make migrate-create msg="add_new_column"   # new Alembic migration
make migrate-upgrade # apply all pending migrations
make dev-reset       # wipe and recreate the dev database
```

---

## Common Setup Issues

### `asyncpg.exceptions.UndefinedTableError`
You probably haven't run migrations yet. Run `poetry run alembic upgrade head` from the `backend/` directory.

### `firebase_admin.exceptions.InvalidArgumentError: Failed to create credential`
The `FIREBASE_SERVICE_ACCOUNT_JSON` path is wrong or the file is malformed. Check the path and ensure the file is the downloaded service account key JSON (not the web SDK config).

### Celery worker can't connect to Redis
Make sure Docker is running: `docker-compose -f docker-compose.dev.yml ps`. If Redis isn't listed, run `docker-compose -f docker-compose.dev.yml up -d`.

### Playwright `chromium` not found
Run `poetry run playwright install chromium` from the `backend/` directory.

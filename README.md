# DealFindio

**Australian grocery deal-finding platform.** Track prices across major supermarket chains, get notified when prices drop, report community prices, and let AI help you decide when to buy.

---

## Mobile quick-start

DealFindio is primarily a **mobile app** (Expo / React Native). Get the iOS Simulator running in three steps:

```bash
make doctor        # check prerequisites
make setup-mobile  # install Node dependencies
make ios           # build + launch on iOS Simulator
```

**Xcode 27 / Device Hub users:** `Simulator.app` is gone — `make ios` handles Device Hub automatically.

Full guide → [docs/local-mobile-development.md](docs/local-mobile-development.md)

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [Features](#2-features)
3. [Architecture Overview](#3-architecture-overview)
4. [Tech Stack](#4-tech-stack)
5. [Repository Structure](#5-repository-structure)
6. [Getting Started](#6-getting-started)
7. [Environment Variables](#7-environment-variables)
8. [Backend Deep-Dive](#8-backend-deep-dive)
9. [Web Frontend Deep-Dive](#9-web-frontend-deep-dive)
10. [Mobile App Deep-Dive](#10-mobile-app-deep-dive)
11. [Data Pipeline](#11-data-pipeline)
12. [AI & ML Features](#12-ai--ml-features)
13. [API Reference](#13-api-reference)
14. [Testing](#14-testing)
15. [Deployment](#15-deployment)
16. [Contributing](#16-contributing)

---

## 1. Product Overview

DealFindio solves a simple problem: grocery prices in Australia vary significantly between chains, and specials rotate weekly. Most shoppers either miss deals or waste time manually comparing catalogues across Coles, Woolworths, ALDI, IGA, and others.

**DealFindio aggregates those prices automatically** and surfaces the best deals near you — on the web and in your pocket.

### Who it's for

| User | What they get |
|------|---------------|
| Bargain-conscious shoppers | A single feed of the best deals near them right now |
| Meal planners | Price history charts to know when something is genuinely cheap vs. just marketed as a deal |
| Community contributors | A reputation system that rewards accurate price reports |

### How it works (end-to-end)

```
Store catalogues / flyers
        ↓
   Scraper pipeline (Playwright + PDF/OCR)
        ↓
   AI deduplication & catalogue matching (Claude)
        ↓
   PostgreSQL + pgvector + PostGIS
        ↓
   FastAPI REST API
        ↓
   Web (React) + Mobile (React Native/Expo)
        ↓
   User: browse deals, set watchlist, get push notifications
```

---

## 2. Features

### Core

- **Deal feed** — location-aware feed of current specials, sorted by discount depth
- **Product search** — full-text + semantic (vector) search across the entire catalogue
- **Price history charts** — 90-day price trend per product, broken down by chain
- **Price predictions** — ML-driven "buy now vs. wait" recommendations (Prophet/ARIMA)
- **Store locator** — map and list of nearby stores with real-time distance

### Community

- **Price reporting** — users can submit a price they see in-store (geofenced ≤200m from store, rate-limited 20/24h)
- **Receipt OCR upload** — photograph a receipt; Claude Vision extracts line items and creates reports automatically
- **Upvote / dispute** — crowd-sourced accuracy scoring on community reports
- **Reputation system** — contributors earn points that weight their reports; tiers: Bronze / Silver / Gold

### Personal

- **Watchlist** — track products with a target price or % off trigger
- **Push notifications** — FCM alerts when a watched product hits your target
- **Notification preferences** — granular control over what triggers a push vs. email
- **User profile** — contribution count, tier badge, report history

### Admin

- **Scraper management** — trigger / cancel catalogue crawls per chain from the admin panel
- **Deduplication review** — inspect and approve AI-generated product merge candidates
- **App config** — runtime feature flags stored in the database

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Clients                              │
│   React SPA (Vite)          Expo React Native App           │
│   Vercel (CDN)              iOS / Android                   │
└─────────────┬───────────────────────────┬───────────────────┘
              │ HTTPS / REST              │ HTTPS / REST
              ▼                           ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI  (Railway)                             │
│   /api/v1/*   Firebase JWT auth   Rate limiting             │
│   Auto-generated OpenAPI docs at /docs                      │
└──────┬───────────────────────────────────────┬─────────────┘
       │ SQLAlchemy 2.0 async                  │ Redis (Celery broker)
       ▼                                       ▼
┌─────────────────────┐             ┌──────────────────────────┐
│  PostgreSQL 16      │             │  Celery Workers           │
│  + PostGIS          │             │  scraper_tasks            │
│  + pg_trgm          │             │  catalog_tasks            │
│  + pgvector         │             │  notification_tasks       │
│  + earthdistance    │             │  embedding_tasks          │
│  (Railway managed)  │             │  ml_tasks / image_tasks   │
└─────────────────────┘             └──────────────────────────┘
                                              │
                          ┌───────────────────┴──────────────────┐
                          │          External Services            │
                          │  Firebase Auth + FCM (notifications)  │
                          │  Anthropic Claude (OCR, AI dedup)     │
                          │  Cloudflare R2 (object storage)       │
                          │  Open Food Facts (product images)     │
                          │  Sentry (error tracking)              │
                          └───────────────────────────────────────┘
```

### Key design decisions

- **Async everywhere** — FastAPI + SQLAlchemy 2.0 async, `asyncpg` driver; all I/O non-blocking
- **Firebase Auth** — the API never stores passwords; it verifies Firebase JWTs on every request
- **Celery for background work** — scraping, ML, notifications, and image enrichment all run off the main request path
- **Vector search** — product embeddings stored in pgvector; semantic search falls back gracefully when similarity is low
- **PostGIS for geo** — `earthdistance`/`ll_to_earth` functions power the "nearby stores" and "geofence validation" features
- **Community confidence** — every price report has a `confidence` score (0.0–1.0) that rises with upvotes and reporter reputation, used to weight the displayed price

---

## 4. Tech Stack

### Backend

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| API framework | FastAPI 0.104+ |
| ORM | SQLAlchemy 2.0 (async) |
| Database | PostgreSQL 16 + PostGIS + pgvector |
| Task queue | Celery 5.3 + Redis |
| Migrations | Alembic |
| Scraping | Playwright (Chromium), pdfplumber, pytesseract |
| ML | Prophet, ARIMA (statsmodels) |
| AI | Anthropic Claude (receipt OCR, product dedup) |
| Auth | Firebase Admin SDK (JWT verification) |
| Push | Firebase Cloud Messaging (FCM) |
| Email | Configurable (SMTP / SES) |
| Object storage | Cloudflare R2 (or local filesystem in dev) |
| Testing | pytest, pytest-asyncio, pytest-cov |
| Linting / fmt | ruff, black, pyright |

### Web Frontend

| Layer | Technology |
|-------|-----------|
| Language | TypeScript (strict) |
| Framework | React 18 |
| Build tool | Vite 5 |
| Styling | Tailwind CSS 3 |
| State management | Zustand |
| HTTP client | Axios (with auth interceptors) |
| Charts | Recharts |
| Routing | React Router v6 |
| Auth | Firebase JS SDK |

### Mobile

| Layer | Technology |
|-------|-----------|
| Language | TypeScript (strict) |
| Framework | React Native 0.81.5 + Expo SDK 54 |
| Navigation | Expo Router (file-based) |
| State management | Zustand |
| HTTP client | Axios + AsyncStorage token |
| Auth | Firebase JS SDK |
| Camera | expo-camera (barcode scanning) |
| Location | expo-location (geofence validation) |
| Images | expo-image-picker (receipt capture) |
| Build | EAS Build (iOS + Android) |

### Infrastructure

| Concern | Tool |
|---------|------|
| Backend hosting | Railway |
| Web hosting | Vercel |
| Database | Railway managed PostgreSQL |
| Cache / broker | Railway managed Redis |
| Object storage | Cloudflare R2 |
| CI/CD | GitHub Actions |
| Error tracking | Sentry |
| Monitoring | Railway metrics |

---

## 5. Repository Structure

```
dealfindio/
├── .github/
│   └── workflows/
│       ├── ci.yml              # Tests + lint on every push/PR
│       └── deploy.yml          # Deploy to Railway + Vercel on merge to main
│
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app factory + middleware
│   │   ├── config.py           # Pydantic settings (env vars)
│   │   ├── deps.py             # Dependency injection (DB session, current user)
│   │   ├── models/             # SQLAlchemy ORM models
│   │   ├── routers/            # FastAPI route handlers
│   │   ├── schemas/            # Pydantic request/response models
│   │   ├── services/           # Business logic (search, notifications, OCR…)
│   │   └── workers/            # Celery task definitions
│   ├── scrapers/               # Per-chain scraper implementations
│   ├── ml/                     # Price prediction models (Prophet/ARIMA)
│   ├── db/
│   │   ├── migrations/         # Alembic migration scripts
│   │   └── schema.reference.sql
│   ├── scripts/                # One-off data backfill scripts
│   ├── tests/                  # pytest suite
│   ├── docs/                   # API documentation
│   ├── pyproject.toml          # Python dependencies (Poetry)
│   ├── alembic.ini
│   ├── Dockerfile
│   └── Makefile                # Dev commands
│
├── web/
│   ├── src/
│   │   ├── pages/              # Route-level page components
│   │   ├── components/         # Shared UI components
│   │   ├── api/client.ts       # Axios instance
│   │   ├── store/store.ts      # Zustand global state
│   │   ├── contexts/           # React context (Auth)
│   │   ├── hooks/              # Custom React hooks
│   │   ├── lib/                # Utility functions
│   │   └── types/              # TypeScript interfaces
│   ├── public/                 # Static assets + icons
│   ├── package.json
│   ├── vite.config.ts
│   └── tailwind.config.js
│
├── mobile/
│   ├── app/                    # Expo Router file-based routes
│   │   └── (tabs)/             # Tab navigator screens
│   ├── src/
│   │   ├── api/client.ts
│   │   ├── store/store.ts
│   │   └── types/
│   ├── plugins/
│   │   └── withFixPodsDeploymentTarget.js  # CocoaPods deployment-target fix
│   ├── app.config.js           # Dynamic Expo config (API URL + plugins)
│   ├── app.json                # Static Expo config (base)
│   └── eas.json                # EAS Build config
│
├── scripts/
│   └── dev/
│       ├── doctor.sh           # Environment health check
│       ├── ios.sh              # iOS Simulator build + launch
│       └── ios-test.sh         # Simulator smoke tests
│
├── docs/
│   └── local-mobile-development.md
│
├── infra/
│   └── railway.toml            # Railway service definitions
│
├── docker-compose.dev.yml      # PostgreSQL + Redis for local dev
├── Procfile                    # Process definitions (Railway / Heroku)
├── dev.sh                      # One-command local dev startup (macOS)
└── .pre-commit-config.yaml     # Pre-commit hook config
```

---

## 6. Getting Started

### Prerequisites

- Python 3.12+ and [Poetry](https://python-poetry.org/)
- Node.js 18+ and npm
- Docker Desktop (for PostgreSQL + Redis)
- A Firebase project (for auth — see [Firebase setup](#firebase-setup))

### 1. Clone and install dependencies

```bash
git clone https://github.com/mmbspk/dealfindio.git
cd dealfindio

# Backend
cd backend
poetry install
poetry run playwright install chromium   # One-time browser download

# Web
cd ../web
npm install

# Mobile (optional)
cd ../mobile
npm install
```

### 2. Start infrastructure (PostgreSQL + Redis)

```bash
# From repo root
docker-compose -f docker-compose.dev.yml up -d
```

This starts:
- PostgreSQL 16 + PostGIS on port `5432`
- Redis 7 on port `6379`

### 3. Configure environment variables

```bash
cd backend
cp .env.example .env
# Edit .env — at minimum set FIREBASE_SERVICE_ACCOUNT_JSON and ANTHROPIC_API_KEY

cd ../web
cp .env.example .env
# VITE_API_URL defaults to http://localhost:8010
```

### 4. Run database migrations

```bash
cd backend
poetry run alembic upgrade head
```

### 5. Start all services

```bash
# From repo root — opens Terminal tabs on macOS
./dev.sh

# Or start manually in separate terminals:
# API
cd backend && poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8010

# Celery worker
cd backend && poetry run celery -A app.workers.celery_app worker -Q scraper,catalog,celery --loglevel=info -c 2

# Celery beat (scheduler)
cd backend && poetry run celery -A app.workers.celery_app beat --loglevel=info

# Web dev server
cd web && npm run dev
```

### Service URLs

| Service | URL |
|---------|-----|
| API | http://localhost:8010 |
| API docs (Swagger) | http://localhost:8010/docs |
| API docs (ReDoc) | http://localhost:8010/redoc |
| Web app | http://localhost:5173 |
| Admin panel | http://localhost:5173/admin |

### Firebase Setup

1. Create a project at [console.firebase.google.com](https://console.firebase.google.com)
2. Enable **Authentication → Sign-in method → Email/Password** (and/or Google)
3. Enable **Cloud Messaging** for push notifications
4. Download the service account JSON: **Project settings → Service accounts → Generate new private key**
5. Save it somewhere safe (outside the repo) and set `FIREBASE_SERVICE_ACCOUNT_JSON=/path/to/key.json` in `backend/.env`
6. Add the Firebase web config to `web/.env` (copy values from **Project settings → Your apps → Web app**)

---

## 7. Environment Variables

### Backend (`backend/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL DSN (`postgresql+asyncpg://...`) |
| `REDIS_URL` | Yes | Redis DSN (`redis://localhost:6379/0`) |
| `FIREBASE_PROJECT_ID` | Yes | Firebase project ID |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes | Path to Firebase service account key file |
| `ANTHROPIC_API_KEY` | Yes | Claude API key (receipt OCR + AI dedup) |
| `SECRET_KEY` | Yes | Random secret for session security |
| `ENVIRONMENT` | No | `development` / `production` (default: `development`) |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |
| `OBJECT_STORAGE_BACKEND` | No | `local` / `r2` / `s3` (default: `local`) |
| `R2_ACCOUNT_ID` | If using R2 | Cloudflare account ID |
| `R2_BUCKET` | If using R2 | R2 bucket name |
| `R2_ACCESS_KEY_ID` | If using R2 | R2 access key |
| `R2_SECRET_ACCESS_KEY` | If using R2 | R2 secret key |
| `SENTRY_DSN` | No | Sentry DSN for error tracking |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins |
| `CATALOG_COLES_ENABLED` | No | Enable automated Coles full-catalogue crawl |
| `CATALOG_WOOLWORTHS_ENABLED` | No | Enable Woolworths full-catalogue crawl |
| `OPEN_FOOD_FACTS_USER_AGENT` | No | UA string for Open Food Facts image enrichment |

### Web Frontend (`web/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_API_URL` | No | Backend API base URL (default: `http://localhost:8010`) |
| `VITE_ENV` | No | `development` / `production` |

---

## 8. Backend Deep-Dive

### Models

| Model | Table | Purpose |
|-------|-------|---------|
| `Product` | `products` | Canonical product (name, brand, GTIN, category, embeddings) |
| `Store` | `stores` | Store location (chain, address, PostGIS point) |
| `PriceReport` | `price_reports` | A price observation (scraped, community, or receipt OCR) |
| `Watchlist` | `watchlist` | A user's product watch with target price/pct |
| `WatchlistList` | `watchlist_lists` | Named lists for grouping watchlist items |
| `User` | `user_profiles` | Firebase UID, reputation score, tier, FCM token |
| `Prediction` | `predictions` | ML-generated buy/wait recommendation |
| `Notification` | `notifications_log` | Push/email notification log |
| `CatalogRun` | `catalog_runs` | Audit log of catalogue crawl jobs |
| `ProductMatch` | `product_match_candidates` | AI dedup merge candidates pending review |
| `AppConfig` | `app_config` | Runtime feature flags |

### Services

| Service | File | What it does |
|---------|------|-------------|
| Search | `services/search.py` | Full-text (`pg_trgm`) + vector (`pgvector`) hybrid search |
| AI Dedup | `services/ai_dedup.py` | Uses Claude to identify duplicate products across chains |
| Product Dedup | `services/product_dedup.py` | Deterministic GTIN + name similarity dedup |
| Catalog Ingest | `services/catalog_ingest.py` | Normalises raw scraper output into DB records |
| Category Normaliser | `services/category_normaliser.py` | Maps chain-specific categories to a canonical taxonomy |
| Embeddings | `services/embeddings.py` | Generates pgvector embeddings for semantic search |
| Receipt Parser | `services/receipt_parser.py` | Claude Vision: photo → line items → product matches |
| Reputation | `services/reputation.py` | 3σ outlier detection + confidence scoring for reports |
| Push Sender | `services/push_sender.py` | Firebase FCM push notifications |
| Email Sender | `services/email_sender.py` | Transactional email dispatch |
| Geo | `services/geo.py` | PostGIS distance queries, geofence validation |
| Object Store | `services/object_store.py` | Abstraction over local / R2 / S3 storage |
| Image Enricher | `services/oft_image_enricher.py` | Backfills product images from Open Food Facts |
| Config Store | `services/config_store.py` | Read/write runtime feature flags from DB |

### Celery Workers & Tasks

Workers consume from three queues: `scraper`, `catalog`, `celery` (default).

| Task | Queue | Trigger | What it does |
|------|-------|---------|-------------|
| `run_scraper` | scraper | Beat / admin | Scrapes weekly flyer for one chain |
| `run_catalog_crawl` | catalog | Admin panel | Full product catalogue crawl for one chain |
| `validate_community_report` | celery | On POST /price-reports | Outlier detection + confidence scoring |
| `check_watchlist_for_price` | celery | After price report saved | Triggers notifications for matching watchlists |
| `send_push_notification` | celery | Async | Dispatches FCM push to device |
| `refit_ml_models` | celery | Weekly beat | Retrains Prophet/ARIMA on latest price history |
| `generate_embeddings_batch` | celery | On new products | Generates pgvector embeddings |
| `enrich_product_images` | celery | Nightly beat | Fills missing images from Open Food Facts |

### Database Migrations

```bash
# Create a new migration
cd backend
make migrate-create msg="add_new_column"

# Apply all pending migrations
make migrate-upgrade

# Roll back the last migration
make migrate-downgrade
```

### Makefile Targets

```bash
make install         # Install Python dependencies
make dev             # Start Docker services (PostgreSQL + Redis)
make dev-stop        # Stop Docker services
make dev-reset       # Wipe and recreate the dev database
make dev-logs        # Tail Docker service logs
make migrate-upgrade # Apply Alembic migrations
make test            # Run test suite
make test-cov        # Tests + HTML coverage report
make format          # black + ruff --fix
make lint            # ruff check (no fix)
make type-check      # pyright
```

---

## 9. Web Frontend Deep-Dive

### Pages

| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | `Home.tsx` | Landing page with feature highlights |
| `/products` | `Products.tsx` | Product catalogue browser |
| `/products/:id` | `Product.tsx` | Product detail with 90-day price chart |
| `/deals` | `Deals.tsx` | Curated deals feed |
| `/stores` | `Stores.tsx` | Store locator (map + list) |
| `/watchlist` | `Watchlist.tsx` | User's price watchlist |
| `/notifications` | `Notifications.tsx` | Notification history |
| `/notifications/preferences` | `NotificationPreferences.tsx` | Push/email opt-in controls |
| `/profile` | `Profile.tsx` | Reputation, tier, contribution history |
| `/login` | `Login.tsx` | Firebase Auth sign-in |
| `/admin` | `Admin.tsx` | Admin panel (scraper control, dedup review) |

### State Management (Zustand)

Global store handles: current user, search query/results, UI loading states, and notification count badge. Firebase auth state is handled separately via `AuthContext`.

### API Client

`web/src/api/client.ts` — Axios instance that:
- Reads `VITE_API_URL` from env
- Attaches the Firebase ID token to every request (`Authorization: Bearer ...`)
- Redirects to `/login` on 401

### Key Scripts

```bash
npm run dev          # Dev server at http://localhost:5173
npm run build        # Production build → dist/
npm run preview      # Preview production build locally
npm run lint         # ESLint
npm run type-check   # tsc --noEmit
npm run format       # Prettier
```

---

## 10. Mobile App Deep-Dive

### Screens

| Screen | Route | Purpose |
|--------|-------|---------|
| Deals | `(tabs)/deals` | Location-aware deals feed |
| Search | `(tabs)/search` | Product search |
| Watchlist | `(tabs)/watchlist` | Tracked products |
| Profile | `(tabs)/profile` | Reputation + tier |
| Scan | `scan` | Barcode scanner → product lookup |
| Report Price | `report-price` | Community price report (geofenced) |
| Upload Receipt | `upload-receipt` | Receipt photo → OCR |
| Notifications | `notifications` | Notification history |
| Notification Preferences | `notification-preferences` | Push opt-in settings |

### Permissions Required

| Permission | Why |
|------------|-----|
| Camera | Barcode scanning, receipt photo capture |
| Location (when in use) | Nearby deals feed; geofence validation for price reports (must be ≤200m from store) |
| Push notifications | Watchlist price alerts |

### Running the App

```bash
cd mobile
npm start              # Expo Go — scan QR with your phone
npm run ios            # iOS Simulator
npm run android        # Android Emulator
npm run type-check     # TypeScript check
```

For physical device testing, update `expo.extra.apiUrl` in `app.json` to your machine's LAN IP.

### Building for Production (EAS)

```bash
npx eas build --platform ios
npx eas build --platform android
```

EAS configuration is in `mobile/eas.json`.

---

## 11. Data Pipeline

### Price Report Sources

| Source | `source` value | Confidence | How |
|--------|---------------|-----------|-----|
| Automated scraper | `scraper` | 0.9 | Playwright / PDF / OCR per chain |
| Community report | `community` | 0.5–1.0 | User submission via app (geofenced) |
| Receipt OCR | `receipt_ocr` | 0.7 | Claude Vision on receipt photos |

### Scraper Coverage

| Chain | Scraper | Catalogue crawl |
|-------|---------|----------------|
| Coles | `scrapers/coles.py` / `catalog_coles.py` | Yes |
| Woolworths | `scrapers/woolworths.py` / `catalog_woolworths.py` | Yes |
| ALDI | `scrapers/aldi.py` / `catalog_aldi.py` | Yes |
| IGA | `scrapers/iga.py` / `catalog_iga.py` | Yes |
| Dan Murphy's | `scrapers/catalog_danmurphys.py` | Yes |
| Drakes | `scrapers/drakes.py` / `catalog_drakes.py` | Yes |

Weekly flyer scrapes run via Celery beat. Full catalogue crawls are on-demand from the admin panel (or via cron env vars per chain).

### Product Deduplication

Products from different chains for the same physical item are merged via a two-stage pipeline:

1. **Deterministic** — exact GTIN match or high name+brand similarity
2. **AI-assisted** — Claude compares product details and images; produces merge candidates for human review via the admin panel

---

## 12. AI & ML Features

### Claude Integration (`ANTHROPIC_API_KEY`)

| Feature | File | How Claude is used |
|---------|------|--------------------|
| Receipt OCR | `services/receipt_parser.py` | Vision: photo → structured line items |
| AI deduplication | `services/ai_dedup.py` | Compare product pairs and recommend merge/keep |
| Category normalisation | `services/category_normaliser.py` | Map raw chain categories to canonical taxonomy |

### Price Predictions (`ml/predictor.py`)

For each product with sufficient price history (≥14 data points):

1. **Prophet** is the primary model — handles seasonality (weekly specials cycles) and trend
2. **ARIMA** (statsmodels) is the fallback for sparse data
3. Models are refit weekly via a Celery beat task
4. Predictions are stored in the `predictions` table and surfaced via the `/api/v1/predictions` endpoint as "buy now" / "wait" recommendations with a confidence score

---

## 13. API Reference

Full interactive docs are available when the API is running:

- **Swagger UI** → http://localhost:8010/docs
- **ReDoc** → http://localhost:8010/redoc

### Endpoint Groups

| Group | Base path | Auth |
|-------|-----------|------|
| Products | `/api/v1/products` | Public (search/browse), Auth (watchlist) |
| Deals | `/api/v1/deals` | Public |
| Stores | `/api/v1/stores` | Public |
| Price Reports | `/api/v1/price-reports` | Auth (submit/vote) |
| Watchlist | `/api/v1/watchlist` | Auth |
| Users | `/api/v1/users` | Auth |
| Notifications | `/api/v1/notifications` | Auth |
| Receipts | `/api/v1/receipts` | Auth |
| Predictions | `/api/v1/predictions` | Public |
| Admin | `/api/v1/admin` | Auth + admin role |
| Health | `/api/v1/health` | Public |

### Authentication

All protected endpoints expect a Firebase ID token:

```
Authorization: Bearer <firebase-id-token>
```

The backend verifies the JWT signature using the Firebase Admin SDK. No passwords are stored.

---

## 14. Testing

### Backend

```bash
cd backend

# Run all tests (SQLite in-memory, fast)
make test

# With coverage report (HTML in htmlcov/)
make test-cov

# Run a specific test file
poetry run pytest tests/test_api.py -v

# Run PostgreSQL-specific tests (requires a real Postgres DB)
TEST_DATABASE_URL=postgresql://dealfindio:dealfindio_dev@localhost:5432/dealfindio_test \
  poetry run pytest -m pg --no-cov
```

The default test lane uses SQLite in-memory (no services needed) and enforces a **60% coverage gate**. Tests marked `pg` exercise PostGIS-only features and must be run against real PostgreSQL before deploying.

### Web Frontend

```bash
cd web
npm run lint          # ESLint
npm run type-check    # TypeScript strict mode check
```

### Mobile

```bash
cd mobile
npm run type-check    # tsc --noEmit
```

---

## 15. Deployment

### CI/CD (GitHub Actions)

- **`.github/workflows/ci.yml`** — runs on every push and PR: linting, type checks, and the full backend test suite
- **`.github/workflows/deploy.yml`** — runs on merge to `main`: deploys backend to Railway, web to Vercel

### Backend → Railway

Railway picks up the `Procfile` at the repo root:

```
api:     cd backend && poetry run uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker:  cd backend && poetry run celery -A app.workers.celery_app worker ...
beat:    cd backend && poetry run celery -A app.workers.celery_app beat ...
```

Service definitions and environment overrides are in `infra/railway.toml`.

Set all required environment variables in the Railway dashboard. Never commit real secrets to the repo.

### Web → Vercel

1. Connect the GitHub repo to a Vercel project
2. Set **Root directory** to `web`
3. Set `VITE_API_URL` to your Railway backend URL
4. Push to `main` — Vercel auto-deploys

### Manual Deployment

```bash
# Deploy backend Docker image
cd backend
docker build -t dealfindio-backend .
docker run -p 8000:8000 --env-file .env dealfindio-backend

# Deploy web
cd web
npm run build          # Output in dist/
# Serve dist/ from any static host
```

---

## 16. Contributing

### Workflow

```bash
# 1. Create a branch
git checkout -b feat/your-feature-name

# 2. Make changes

# 3. Run checks before committing
cd backend
make format && make type-check && make test

cd ../web
npm run lint && npm run type-check

# 4. Commit and push
git push origin feat/your-feature-name

# 5. Open a pull request — CI runs automatically
```

### Code Style

- **Python** — `black` for formatting, `ruff` for linting, `pyright` for types. No `any` types without a comment explaining why.
- **TypeScript** — strict mode, `Prettier` + `ESLint`. No `any`.
- **Commits** — follow the Conventional Commits format: `feat:`, `fix:`, `chore:`, `test:`, `docs:`, `refactor:`.

### Pre-commit Hooks

```bash
pre-commit install      # Install hooks (one-time)
pre-commit run --all-files   # Run manually
```

Hooks enforce: trailing whitespace, YAML validity, `ruff` lint, `black` format.

---

## License

Proprietary — DealFindio 2026. All rights reserved.

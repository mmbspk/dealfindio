# DealFindio

**Australian grocery deal-finding platform.** Track prices across major supermarket chains, get notified when prices drop, verify your receipt for overcharges, and let AI help you decide when to buy.

> **Mobile-first.** The primary experience is the Expo / React Native mobile app. The web app is a full-featured companion.

---

## Quick start

```bash
# macOS — opens all services in separate Terminal tabs
./dev.sh
```

| Service | URL |
|---------|-----|
| API | http://localhost:8010 |
| Swagger docs | http://localhost:8010/docs |
| Web app | http://localhost:5173 |
| Admin panel | http://localhost:5173/admin |

Full setup → [Getting Started](#6-getting-started) · Mobile guide → [docs/local-mobile-development.md](docs/local-mobile-development.md)

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

Grocery prices in Australia vary significantly between chains, and specials rotate weekly. Most shoppers either miss deals or waste time manually comparing catalogues across Coles, Woolworths, ALDI, IGA, and others.

**DealFindio aggregates those prices automatically** and surfaces the best deals near you — on the web and in your pocket.

### Who it's for

| User | What they get |
|------|---------------|
| Bargain-conscious shoppers | A single feed of the best deals near them right now |
| Meal planners | Price history charts to know when something is genuinely cheap vs. just marketed as a deal |
| Community contributors | A reputation system that rewards accurate price reports |
| Careful shoppers | Receipt scanning that flags overcharges and tells you exactly what the store owes you |

### How it works (end-to-end)

```
Store catalogues / flyers
        ↓
   Scraper pipeline (Playwright + Catalog crawlers)
        ↓
   AI deduplication & catalogue integrity pipeline (Claude)
        ↓
   PostgreSQL + pgvector + PostGIS
        ↓
   FastAPI REST API
        ↓
   Web (React) + Mobile (React Native/Expo)
        ↓
   User: browse deals, set watchlist, scan receipt, get push/email notifications
```

---

## 2. Features

### Price Discovery

- **Deal feed** — location-aware feed of current specials, sorted by discount depth
- **Product catalogue search** — full-text (`pg_trgm`) + semantic (pgvector) hybrid search across all chains
- **Price history charts** — 90-day price trend per product, broken down by chain
- **Price predictions** — ML-driven "buy now vs. wait" recommendations (Prophet/ARIMA)
- **Unit price comparison** — normalised `package_size_norm` and unit price allow true like-for-like comparison (e.g. $/100g) across pack sizes
- **Store locator** — map and list of nearby stores with real-time distance

### Community & Contributions

- **Price reporting** — users submit a price they see in-store; geofenced ≤200m from the store, rate-limited 20/24h
- **Receipt OCR upload** — photograph a receipt; Claude Vision extracts line items and creates community price reports automatically
- **Receipt price verification** — upload a receipt to check what was charged against dealfindio's known shelf prices; flags overcharges and explains the Australian **Scanning Code of Practice** remedy (first overcharged item free at Coles, Woolworths, ALDI, and IGA)
- **Upvote / dispute** — crowd-sourced accuracy scoring on community reports
- **Reputation system** — contributors earn points that weight their reports; tiers: Bronze / Silver / Gold

### Watchlist & Notifications

- **Watchlist** — track products with a target price, a % off threshold, or "alert on prediction"
- **Watchlist lists** — organise watched products into named, emoji-tagged lists (e.g. 🛒 Weekly Groceries, 🎂 Baking)
- **In-app bell** — unread notification badge in the header; notification history page
- **Push notifications** — Expo Push (covers iOS APNs + Android FCM) alerts when a watched product hits your target; app icon badge count reflects unread in-app notifications
- **Email alerts** — Resend-powered price alert and weekly digest emails with product image cards; grouped by watchlist list
- **Notification preferences** — granular per-channel (push / email) control for price match, % off, flash sale, prediction, and weekly digest triggers; timezone-aware quiet hours
- **Per-list channel override** — silence or redirect alerts for individual watchlist lists without affecting others

### Admin & Operations

- **Scraper management** — trigger / cancel catalogue crawls per chain from the admin panel; live log streaming via Redis
- **Catalog integrity audit** — read-only scanner that classifies all products by 7 subtypes of data quality issues (missing package size, bad unit price, category mismatches, etc.)
- **Approval-gated remediation** — generates a remediation plan from the audit; admin reviews and approves before any data is changed
- **Deduplication review** — inspect and approve AI-generated product merge candidates; full audit trail in `ProductMergeLog`
- **App config** — runtime feature flags stored in the database; change behaviour without a deploy

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Clients                              │
│   React SPA (Vite)              Expo React Native App       │
│   Vercel (CDN)                  iOS / Android               │
└─────────────┬───────────────────────────┬───────────────────┘
              │ HTTPS / REST              │ HTTPS / REST
              ▼                           ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI  (Railway)                             │
│   /api/v1/*   Firebase JWT auth   Rate limiting             │
│   Auto-generated OpenAPI docs at /docs                      │
└──────┬───────────────────────────────────────┬─────────────┘
       │ SQLAlchemy 2.0 async                  │ Redis (Celery broker + task logs)
       ▼                                       ▼
┌─────────────────────┐             ┌──────────────────────────┐
│  PostgreSQL 16      │             │  Celery Workers           │
│  + PostGIS          │             │  catalog_tasks            │
│  + pg_trgm          │             │  notification_tasks       │
│  + pgvector         │             │  embedding_tasks          │
│  + earthdistance    │             │  ml_tasks / image_tasks   │
│  (Railway managed)  │             │  report_validator         │
└─────────────────────┘             └──────────────────────────┘
                                              │
                          ┌───────────────────┴──────────────────┐
                          │          External Services            │
                          │  Firebase Auth (JWT verification)     │
                          │  Expo Push API (iOS APNs + Android)   │
                          │  Resend (transactional email)         │
                          │  Anthropic Claude (OCR, AI dedup)     │
                          │  Cloudflare R2 (object storage)       │
                          │  Open Food Facts (product images)     │
                          │  Sentry (error tracking)              │
                          └───────────────────────────────────────┘
```

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| **Async everywhere** | FastAPI + SQLAlchemy 2.0 async, `asyncpg` driver — all I/O non-blocking |
| **Firebase Auth** | The API never stores passwords; it verifies Firebase JWTs on every request |
| **Celery for background work** | Scraping, ML, notifications, and image enrichment all run off the main request path |
| **Vector search** | Product embeddings in pgvector; semantic search falls back gracefully when similarity is low |
| **PostGIS for geo** | `earthdistance`/`ll_to_earth` functions power "nearby stores" and geofence validation |
| **Community confidence** | Every price report has a `confidence` score (0–1) that rises with upvotes and reporter reputation |
| **ProductRetailerListing** | Retailer-specific pricing lives in a separate table so the canonical `Product` record is chain-agnostic |
| **Approval-gated remediation** | Audit runs read-only; data changes require an explicit admin approval step to prevent accidental bulk mutations |

---

## 4. Tech Stack

### Backend

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| API framework | FastAPI 0.104+ |
| ORM | SQLAlchemy 2.0 (async) |
| Database | PostgreSQL 16 + PostGIS + pgvector + pg_trgm |
| Task queue | Celery 5.3 + Redis |
| Migrations | Alembic |
| Scraping | Playwright (Chromium), per-chain catalog crawlers |
| ML | Prophet, ARIMA (statsmodels) |
| AI | Anthropic Claude (receipt OCR, product dedup, category normalisation) |
| Auth | Firebase Admin SDK (JWT verification) |
| Push | Expo Push API (covers iOS APNs + Android FCM) |
| Email | Resend API |
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
| HTTP client | Axios (with Firebase auth interceptors) |
| Charts | Recharts |
| Routing | React Router v6 |
| Auth | Firebase JS SDK |

### Mobile App

| Layer | Technology |
|-------|-----------|
| Language | TypeScript (strict) |
| Framework | React Native 0.81.5 + Expo SDK 54 |
| Navigation | Expo Router (file-based) |
| State management | Zustand |
| HTTP client | Axios + AsyncStorage token |
| Auth | Firebase JS SDK |
| Camera | expo-camera (barcode scanning) |
| Location | expo-location (geofence validation for price reports) |
| Images | expo-image-picker (receipt capture) |
| Push | Expo Notifications (APNs + FCM via Expo Push API) |
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
│   ├── scrapers/               # Per-chain catalog scraper implementations
│   ├── ml/                     # Price prediction models (Prophet/ARIMA)
│   ├── db/
│   │   ├── migrations/         # Alembic migration scripts
│   │   └── schema.reference.sql
│   ├── scripts/                # One-off data backfill / audit scripts
│   ├── tests/                  # pytest suite
│   ├── docs/                   # API and operations documentation
│   ├── pyproject.toml          # Python dependencies (Poetry)
│   └── Makefile                # Dev commands
│
├── web/
│   ├── src/
│   │   ├── pages/              # Route-level page components
│   │   ├── components/         # Shared UI components
│   │   ├── api/client.ts       # Axios instance + auth interceptors
│   │   ├── store/store.ts      # Zustand global state
│   │   ├── contexts/           # React context (Auth)
│   │   ├── hooks/              # Custom React hooks
│   │   └── types/              # TypeScript interfaces
│   ├── public/                 # Static assets + icons
│   ├── package.json
│   └── vite.config.ts
│
├── mobile/
│   ├── app/                    # Expo Router file-based routes
│   │   └── (tabs)/             # Tab navigator screens
│   ├── src/
│   │   ├── api/client.ts
│   │   ├── store/store.ts
│   │   └── types/
│   ├── app.config.js           # Dynamic Expo config (API URL + plugins)
│   └── eas.json                # EAS Build config
│
├── docs/
│   ├── local-mobile-development.md
│   └── notification-release-validation.md
│
├── infra/
│   └── railway.toml            # Railway service definitions
│
├── docker-compose.dev.yml      # PostgreSQL + Redis for local dev
├── dev.sh                      # One-command local dev startup (macOS)
└── Procfile                    # Process definitions (Railway)
```

---

## 6. Getting Started

### Prerequisites

- Python 3.12+ and [Poetry](https://python-poetry.org/)
- Node.js 18+ and npm
- Docker Desktop (for PostgreSQL + Redis)
- A Firebase project (see [Firebase Setup](#firebase-setup))
- An Anthropic API key (for Claude-powered features — receipt OCR, AI dedup)
- A Resend API key (for email notifications — optional in dev)

### 1. Clone and install dependencies

```bash
git clone https://github.com/mmbspk/dealfindio.git
cd dealfindio

# Backend
cd backend && poetry install
poetry run playwright install chromium   # one-time browser download

# Web
cd ../web && npm install

# Mobile (optional)
cd ../mobile && npm install
```

### 2. Start infrastructure (PostgreSQL + Redis)

```bash
docker-compose -f docker-compose.dev.yml up -d
```

This starts:
- PostgreSQL 16 + PostGIS on port `5432`
- Redis 7 on port `6379`

### 3. Configure environment variables

```bash
cd backend && cp .env.example .env
# Edit .env — at minimum set FIREBASE_SERVICE_ACCOUNT_JSON and ANTHROPIC_API_KEY

cd ../web && cp .env.example .env
# VITE_API_URL defaults to http://localhost:8010
```

### 4. Run database migrations

```bash
cd backend && poetry run alembic upgrade head
```

### 5. Start all services

```bash
# From repo root — opens Terminal tabs on macOS
./dev.sh

# Or start manually in separate terminals:
cd backend && poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8010
cd backend && poetry run celery -A app.workers.celery_app worker -Q scraper,catalog,celery,notification --loglevel=info -c 2
cd backend && poetry run celery -A app.workers.celery_app beat --loglevel=info
cd web && npm run dev
```

### Firebase Setup

1. Create a project at [console.firebase.google.com](https://console.firebase.google.com)
2. Enable **Authentication → Sign-in method → Email/Password** (and/or Google)
3. Enable **Cloud Messaging** for push notifications
4. Download the service account JSON: **Project settings → Service accounts → Generate new private key**
5. Set `FIREBASE_SERVICE_ACCOUNT_JSON=/path/to/key.json` in `backend/.env`
6. Copy the web config values from **Project settings → Your apps → Web app** into `web/.env`

### Mobile quick-start

```bash
make doctor        # check prerequisites
make setup-mobile  # install Node dependencies
make ios           # build + launch on iOS Simulator
```

Full guide → [docs/local-mobile-development.md](docs/local-mobile-development.md)

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
| `RESEND_API_KEY` | No | Resend API key (email notifications) |
| `SECRET_KEY` | Yes | Random secret for signing (e.g. unsubscribe tokens) |
| `ENVIRONMENT` | No | `development` / `production` (default: `development`) |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |
| `OBJECT_STORAGE_BACKEND` | No | `local` / `r2` / `s3` (default: `local`) |
| `R2_ACCOUNT_ID` | If using R2 | Cloudflare account ID |
| `R2_BUCKET` | If using R2 | R2 bucket name |
| `R2_ACCESS_KEY_ID` | If using R2 | R2 access key |
| `R2_SECRET_ACCESS_KEY` | If using R2 | R2 secret key |
| `SENTRY_DSN` | No | Sentry DSN for error tracking |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins |
| `CATALOG_COLES_CRON` | No | Cron schedule for automated Coles delta crawl |
| `CATALOG_WOOLWORTHS_CRON` | No | Cron schedule for automated Woolworths delta crawl |

### Web Frontend (`web/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_API_URL` | No | Backend API base URL (default: `http://localhost:8010`) |
| `VITE_FIREBASE_*` | Yes | Firebase web SDK config values |

---

## 8. Backend Deep-Dive

### Models

| Model | Table | Purpose |
|-------|-------|---------|
| `Product` | `products` | Canonical product (name, brand, GTIN, category, unit info, embeddings) |
| `ProductRetailerListing` | `product_retailer_listings` | Retailer-specific SKU, name, and pricing metadata for a product |
| `Store` | `stores` | Store location (chain, address, PostGIS point) |
| `PriceReport` | `price_reports` | A price observation (scraped, community, or receipt OCR) with confidence score |
| `UserWatchlist` | `watchlist` | A user's product watch with target price / % off / prediction trigger |
| `WatchlistList` | `watchlist_lists` | Named, emoji-tagged lists for grouping watchlist items; optional per-list notification channel override |
| `User` | `user_profiles` | Firebase UID, reputation score, tier, Expo push token, notification preferences |
| `Prediction` | `predictions` | ML-generated buy / wait recommendation with confidence |
| `Notification` | `notifications_log` | Push / email / in-app notification log with read state |
| `CatalogRun` | `catalog_runs` | Audit log of catalogue crawl jobs (chain, mode, status, timing) |
| `ProductMatch` | `product_match_candidates` | AI dedup merge candidates pending admin review |
| `ProductMergeLog` | `product_merge_log` | Permanent audit trail of approved product merges |
| `PackageSizeRemediationLog` | `package_size_remediation_log` | Audit trail of catalog integrity remediations |
| `AppConfig` | `app_config` | Runtime feature flags stored in the database |

### Services

| Service | File | What it does |
|---------|------|-------------|
| Search | `services/search.py` | Full-text (`pg_trgm`) + vector (`pgvector`) hybrid search |
| AI Dedup | `services/ai_dedup.py` | Uses Claude to identify duplicate products across chains |
| Product Dedup | `services/product_dedup.py` | Deterministic GTIN + name similarity deduplication |
| Catalog Ingest | `services/catalog_ingest.py` | Normalises raw scraper output into DB records |
| Listing Helpers | `services/listing_helpers.py` | Utilities for managing `ProductRetailerListing` records |
| Category Normaliser | `services/category_normaliser.py` | Maps chain-specific categories to canonical taxonomy |
| Package Size | `services/package_size.py` | Extracts and normalises pack size strings (e.g. "375mL 6-pack") |
| Specials Schedule | `services/specials_schedule.py` | Knows when each chain's specials rotate; drives crawl scheduling |
| Embeddings | `services/embeddings.py` | Generates pgvector embeddings via Claude for semantic search |
| Receipt Parser | `services/receipt_parser.py` | Claude Vision: receipt photo → structured line items |
| Reputation | `services/reputation.py` | 3σ outlier detection + confidence scoring for community reports |
| Push Sender | `services/push_sender.py` | Expo Push API — covers iOS APNs and Android FCM |
| Email Sender | `services/email_sender.py` | Resend API — price alerts, weekly digest, recall alerts |
| Geo | `services/geo.py` | PostGIS distance queries, geofence validation |
| Object Store | `services/object_store.py` | Abstraction over local / Cloudflare R2 / S3 storage |
| Image Enricher | `services/oft_image_enricher.py` | Backfills product images from Open Food Facts |
| Config Store | `services/config_store.py` | Read/write runtime feature flags from the database |

### Celery Workers & Tasks

Workers consume from queues: `scraper`, `catalog`, `celery` (default), `notification`.

| Task | Queue | Trigger | What it does |
|------|-------|---------|-------------|
| `run_catalog_crawl` | catalog | Admin panel / cron | Full or delta product catalogue crawl for one chain |
| `run_all_catalog_crawls` | catalog | Admin panel | Delta crawl for all configured chains |
| `expire_stale_specials` | celery | Beat | Marks expired specials as inactive |
| `validate_community_report` | celery | On POST /price-reports | Outlier detection + confidence scoring |
| `recalculate_reporter_reputation` | celery | After report validation | Updates contributor tier and score |
| `check_watchlist_for_price` | notification | After price report saved | Triggers push/email/in-app for matching watchlists |
| `notify_prediction_drops` | notification | Beat (hourly) | Checks ML predictions against watchlists; sends alerts |
| `send_weekly_digest` | notification | Beat (Wednesday 22:00 UTC) | Sends per-user weekly deal digest email |
| `embed_product` | celery | On new product | Generates pgvector embedding for semantic search |
| `backfill_product_embeddings` | celery | On-demand | Batch-generates embeddings for products missing them |
| `enrich_product_images` | celery | Nightly beat | Fills missing product images from Open Food Facts |
| `refit_and_predict_all` | celery | Weekly beat | Retrains Prophet/ARIMA models on latest price history |

### Database Migrations

```bash
cd backend

# Create a new migration
make migrate-create msg="add_new_column"

# Apply all pending migrations
make migrate-upgrade

# Roll back the last migration
make migrate-downgrade
```

### Makefile Targets

```bash
make install          # Install Python dependencies
make dev              # Start Docker services (PostgreSQL + Redis)
make dev-stop         # Stop Docker services
make dev-reset        # Wipe and recreate the dev database
make migrate-upgrade  # Apply Alembic migrations
make test             # Run test suite
make test-cov         # Tests + HTML coverage report
make format           # black + ruff --fix
make lint             # ruff check
make type-check       # pyright
```

---

## 9. Web Frontend Deep-Dive

### Pages

| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | `Home.tsx` | Landing page with feature highlights |
| `/products` | `Products.tsx` | Product catalogue browser with search and filters |
| `/products/:id` | `Product.tsx` | Product detail — price history chart, price by chain, AI prediction |
| `/deals` | `Deals.tsx` | Curated deals feed sorted by discount depth |
| `/stores` | `Stores.tsx` | Store locator — interactive map + sortable list |
| `/watchlist` | `Watchlist.tsx` | Watchlist manager — lists, targets, and items |
| `/notifications` | `Notifications.tsx` | Notification history with read/unread state |
| `/notifications/preferences` | `NotificationPreferences.tsx` | Per-channel opt-in controls, quiet hours, timezone |
| `/profile` | `Profile.tsx` | Reputation tier, contribution count, report history |
| `/login` | `Login.tsx` | Firebase Auth sign-in |
| `/admin` | `Admin.tsx` | Admin panel: scraper control, dedup review, app config |

### Key Components

| Component | Purpose |
|-----------|---------|
| `Header.tsx` | Top nav with unread notification bell badge (polls `/notifications/unread-count`) |
| `ProductCard.tsx` | Compact product tile used in search results and deals feed |
| `DealCard.tsx` | Deal card with discount badge and price comparison |
| `PriceChart.tsx` | Recharts 90-day price history breakdown by chain |
| `PredictionCard.tsx` | "Buy now / Wait" AI recommendation card |
| `SearchBar.tsx` | Hybrid search with debounce |
| `SaveToListButton.tsx` | Add product to a watchlist list |
| `StoresMap.tsx` | Leaflet map of nearby stores |

### API Client

`web/src/api/client.ts` — Axios instance that:
- Reads `VITE_API_URL` from env
- Attaches the Firebase ID token to every request (`Authorization: Bearer ...`)
- Refreshes the token silently on expiry
- Redirects to `/login` on 401

---

## 10. Mobile App Deep-Dive

### Screens

| Screen | Route | Purpose |
|--------|-------|---------|
| Deals | `(tabs)/deals` | Location-aware deals feed |
| Search | `(tabs)/search` | Product search with barcode scan shortcut |
| Watchlist | `(tabs)/watchlist` | Tracked products organised by list |
| Stores | `(tabs)/stores` | Nearby store map and list |
| Profile | `(tabs)/profile` | Reputation tier, notifications history |
| Scan | `scan` | Barcode scanner → product detail |
| Product | `product/[id]` | Product detail with price history and watchlist controls |
| Report Price | `report-price` | Community price report (geofenced ≤200m) |
| Upload Receipt | `upload-receipt` | Receipt photo → OCR → community price reports |
| Notifications | `notifications` | Notification history with deep-link tap handling |
| Notification Preferences | `notification-preferences` | Push / email opt-in settings |

### Permissions Required

| Permission | Why |
|------------|-----|
| Camera | Barcode scanning, receipt photo capture |
| Location (when in use) | Nearby deals feed; geofence validation for price reports |
| Push notifications | Watchlist price alerts and weekly digest |

### Running the App

```bash
cd mobile
npm start              # Expo Go — scan QR with your phone
npm run ios            # iOS Simulator (handles Xcode 27 / Device Hub automatically)
npm run android        # Android Emulator
npm run type-check     # TypeScript check
```

For physical device testing, set `expo.extra.apiUrl` in `app.json` to your machine's LAN IP.

### Building for Production (EAS)

```bash
npx eas build --platform ios
npx eas build --platform android
```

---

## 11. Data Pipeline

### Price Report Sources

| Source | `source` value | Default confidence | How |
|--------|---------------|-------------------|-----|
| Automated catalog crawl | `flyer_coles`, `flyer_woolworths`, etc. | 0.9 | Playwright catalog scrapers |
| Community report | `community` | 0.5–1.0 | User submission via app (geofenced) |
| Receipt OCR | `receipt_ocr` | 0.7 | Claude Vision on receipt photos |

### Scraper Coverage

| Chain | Catalog scraper | Special notes |
|-------|-----------------|---------------|
| Coles | `scrapers/catalog_coles.py` | Full catalogue + delta mode; pack-size extraction hardened |
| Woolworths | `scrapers/catalog_woolworths.py` | Marketplace products excluded (10-digit stockcode filter) |
| ALDI | `scrapers/catalog_aldi.py` | Weekly specials + ALDI-specific pricing model |
| IGA | `scrapers/catalog_iga.py` | Regional availability-aware |
| Drakes | `scrapers/catalog_drakes.py` | SA/QLD independent chain |

Full crawls are on-demand from the admin panel. Delta crawls run automatically via Celery beat (configured per chain via `CATALOG_{CHAIN}_CRON` env vars).

### Product Deduplication

Products from different chains representing the same physical item are merged via a two-stage pipeline:

1. **Deterministic** — exact GTIN match, then high name+brand similarity (pg_trgm)
2. **AI-assisted** — Claude compares product details; generates merge candidates for human review in the admin panel
3. **Audit trail** — every merge is recorded in `ProductMergeLog` with the before/after state and the approving admin

### Catalog Integrity Audit

A read-only audit tool (`scripts/catalog_integrity_audit.py`) classifies all products into 7 issue subtypes:

| Subtype | Description |
|---------|-------------|
| `missing_package_size` | No `package_size` or `package_size_norm` |
| `bad_unit_price` | `unit_price` present but `unit_price_label` missing or inconsistent |
| `category_mismatch` | Chain category doesn't map to any canonical category |
| `duplicate_listing` | Two `ProductRetailerListing` rows for the same product+chain |
| `stale_price` | Most recent `PriceReport` is older than the expected crawl interval |
| `missing_image` | No `image_url` or `external_image_url` |
| `orphaned_listing` | `ProductRetailerListing` with no matching `Product` |

The audit produces a remediation plan. An admin reviews and approves before any changes are applied — protecting against accidental bulk mutations.

---

## 12. AI & ML Features

### Claude Integration (`ANTHROPIC_API_KEY`)

| Feature | File | How Claude is used |
|---------|------|--------------------|
| Receipt OCR | `services/receipt_parser.py` | Vision model: photo → structured line items (name, price, qty) |
| AI deduplication | `services/ai_dedup.py` | Compare product pairs; recommend merge, keep, or flag for review |
| Category normalisation | `services/category_normaliser.py` | Map raw chain category strings to the canonical taxonomy |
| Product embeddings | `services/embeddings.py` | Text embeddings via Claude for pgvector semantic search |

The AI dedup service supports loading the API key and proxy URL from the database (`app_config` table), allowing key rotation without a deploy.

### Price Predictions

For each product with ≥14 price history data points:

1. **Prophet** (primary) — handles weekly specials seasonality and trend
2. **ARIMA** (fallback) — for sparse price histories
3. Models refit weekly via Celery beat
4. Predictions stored in `predictions` table; surfaced as "buy now / wait" cards with a confidence percentage

---

## 13. API Reference

Full interactive documentation is available when the API is running:

- **Swagger UI** → http://localhost:8010/docs
- **ReDoc** → http://localhost:8010/redoc

### Endpoint Groups

| Group | Base path | Auth |
|-------|-----------|------|
| Products | `/api/v1/products` | Public (browse), Auth (watchlist) |
| Deals | `/api/v1/deals` | Public |
| Stores | `/api/v1/stores` | Public |
| Price Reports | `/api/v1/price-reports` | Auth |
| Watchlist | `/api/v1/watchlist` | Auth |
| Users | `/api/v1/users` | Auth |
| Notifications | `/api/v1/notifications` | Auth |
| Receipts | `/api/v1/receipts` | Auth |
| Predictions | `/api/v1/predictions` | Public |
| Admin | `/api/v1/admin` | Auth + admin role |
| Health | `/health` | Public |

### Notable Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/receipts/upload` | Upload receipt photo → creates community price reports |
| `POST` | `/api/v1/receipts/verify` | Upload receipt → compare against shelf prices → returns overcharge flags + SCOP remedy |
| `GET` | `/api/v1/notifications/unread-count` | Count of unread in-app notifications (for bell badge) |
| `PUT` | `/api/v1/notifications/preferences` | Update push/email/quiet-hours preferences |
| `GET` | `/api/v1/admin/catalog-audit` | Run read-only catalog integrity audit |
| `POST` | `/api/v1/admin/catalog-remediate` | Execute an approved remediation plan |

### Authentication

All protected endpoints expect a Firebase ID token:

```http
Authorization: Bearer <firebase-id-token>
```

---

## 14. Testing

### Backend

```bash
cd backend

# Run all tests
make test

# With HTML coverage report (htmlcov/)
make test-cov

# Single file
poetry run pytest tests/test_api.py -v

# PostgreSQL-specific tests (require a real Postgres DB)
TEST_DATABASE_URL=postgresql://dealfindio:dealfindio_dev@localhost:5432/dealfindio_test \
  poetry run pytest -m pg --no-cov
```

The default test lane uses SQLite in-memory (no services needed) and enforces a **60% coverage gate**. Tests marked `pg` exercise PostGIS-only features and must pass against real PostgreSQL before deploying.

### Web Frontend

```bash
cd web
npm run lint          # ESLint
npm run type-check    # TypeScript strict mode
```

### Mobile

```bash
cd mobile
npm run type-check    # tsc --noEmit
```

---

## 15. Deployment

### CI/CD (GitHub Actions)

- **`ci.yml`** — runs on every push/PR: lint, type checks, full backend test suite
- **`deploy.yml`** — runs on merge to `main`: deploys backend to Railway, web to Vercel

### Backend → Railway

Railway reads the `Procfile`:

```
api:     cd backend && poetry run uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker:  cd backend && poetry run celery -A app.workers.celery_app worker -Q scraper,catalog,celery,notification --loglevel=info
beat:    cd backend && poetry run celery -A app.workers.celery_app beat --loglevel=info
```

Service definitions in `infra/railway.toml`. Set all required environment variables in the Railway dashboard — never commit secrets to the repo.

### Web → Vercel

1. Connect the GitHub repo to a Vercel project
2. Set **Root directory** to `web`
3. Set `VITE_API_URL` to your Railway backend URL
4. Push to `main` — Vercel auto-deploys

---

## 16. Contributing

### Workflow

```bash
# 1. Create a branch (follow naming convention)
git checkout -b story/123-short-slug   # for a story
git checkout -b epic/123-short-slug    # for an epic
git checkout -b fix/short-description  # for a bug fix

# 2. Make changes and run checks
cd backend && make format && make type-check && make test
cd web && npm run lint && npm run type-check

# 3. Commit using Conventional Commits
git commit -m "feat(watchlist): add per-list notification channel override"

# 4. Push and open a PR against main
git push origin story/123-short-slug
```

Never push directly to `main`. All work goes through a PR. CI must pass before merging.

### Code Style

- **Python** — `black` for formatting, `ruff` for linting, `pyright` for types
- **TypeScript** — strict mode, `Prettier` + `ESLint`; no `any`
- **Commits** — Conventional Commits: `feat|fix|refactor|chore|test|docs(scope): message`

### Pre-commit Hooks

```bash
pre-commit install          # install hooks (one-time)
pre-commit run --all-files  # run manually
```

---

## License

Proprietary — DealFindio 2026. All rights reserved.

---

## Wiki

Detailed documentation including feature walkthroughs, screenshots, and developer guides is available in the [GitHub Wiki](https://github.com/mmbspk/dealfindio/wiki).

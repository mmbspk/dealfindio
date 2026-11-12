# Architecture

System design, tech stack, and key design decisions.

---

## System Diagram

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
│   Auto-generated OpenAPI docs at /docs and /redoc           │
└──────┬───────────────────────────────────────┬─────────────┘
       │ SQLAlchemy 2.0 async (asyncpg)        │ Celery (Redis broker)
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

---

## Tech Stack

### Backend

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| API framework | FastAPI 0.104+ |
| ORM | SQLAlchemy 2.0 (async) |
| DB driver | asyncpg |
| Database | PostgreSQL 16 + PostGIS + pgvector + pg_trgm |
| Task queue | Celery 5.3 + Redis |
| Migrations | Alembic |
| Scraping | Playwright (Chromium), per-chain catalog crawlers |
| ML | Prophet, ARIMA (statsmodels) |
| AI | Anthropic Claude (receipt OCR, dedup, category normalisation, embeddings) |
| Auth | Firebase Admin SDK (JWT verification — no passwords stored) |
| Push | Expo Push API (covers iOS APNs and Android FCM) |
| Email | Resend API |
| Object storage | Cloudflare R2 (or local filesystem in dev) |
| Testing | pytest, pytest-asyncio, pytest-cov |

### Web Frontend

| Layer | Technology |
|-------|-----------|
| Language | TypeScript (strict) |
| Framework | React 18 |
| Build tool | Vite 5 |
| Styling | Tailwind CSS 3 |
| State | Zustand |
| HTTP | Axios (Firebase auth interceptors) |
| Charts | Recharts |
| Routing | React Router v6 |

### Mobile

| Layer | Technology |
|-------|-----------|
| Language | TypeScript (strict) |
| Framework | React Native 0.81.5 + Expo SDK 54 |
| Navigation | Expo Router (file-based) |
| State | Zustand |
| HTTP | Axios + AsyncStorage token |
| Camera | expo-camera |
| Location | expo-location |
| Images | expo-image-picker |
| Push | Expo Notifications |
| Build | EAS Build |

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

## Key Design Decisions

### Async everywhere
FastAPI + SQLAlchemy 2.0 async + `asyncpg` — all I/O is non-blocking. This keeps the API responsive under heavy concurrent scraper writes.

### Firebase Auth (no passwords)
The API verifies Firebase JWTs on every request. It never stores passwords. This keeps the auth surface small and delegates session management to Firebase.

### Celery for all background work
Scraping, ML refit, notifications, image enrichment, and community report validation all run off the main request path. Celery workers consume from separate queues (`scraper`, `catalog`, `celery`, `notification`) so heavy catalog crawls don't block notification delivery.

### PostgreSQL extensions over separate services
`pgvector` (semantic search), `pg_trgm` (fuzzy text search), and PostGIS (`earthdistance`, geofencing) are all PostgreSQL extensions — no Elasticsearch or separate vector DB needed.

### ProductRetailerListing (retailer separation)
Each chain's price for a product lives in a `ProductRetailerListing` row, not on the `Product` table directly. This allows one canonical product record to have per-chain prices, names, SKUs, and availability without duplication.

### Approval-gated remediation
The catalog integrity audit runs read-only. Data changes require an explicit admin approval step. This prevents a bug in the audit script from accidentally bulk-mutating thousands of product records.

### Expo Push over direct APNs/FCM
The Expo Push API abstracts over both iOS APNs and Android FCM with a single request format. This avoids managing two separate push credential sets and simplifies the notification pipeline.

### Community confidence scoring
Community price reports are not shown directly. Each report gets a confidence score (0–1) via 3σ outlier detection weighted by the reporter's reputation tier. Low-confidence reports are surfaced but marked as unverified.

---

## Database Schema Overview

| Table | Purpose |
|-------|---------|
| `products` | Canonical product (name, brand, GTIN, category, embeddings, image) |
| `product_retailer_listings` | Chain-specific SKU, name, URL for a product |
| `stores` | Store location (chain, address, PostGIS point) |
| `price_reports` | Price observation (scraped, community, or receipt OCR) + confidence |
| `watchlist` | User's product watch with target price / % off / trigger type |
| `watchlist_lists` | Named emoji-tagged groups; optional per-list notification channel override |
| `user_profiles` | Firebase UID, reputation, tier, Expo push token, notification preferences |
| `predictions` | ML buy/wait recommendation per product with confidence |
| `notifications_log` | Push/email/in-app notification log with read state |
| `catalog_runs` | Audit log of catalogue crawl jobs |
| `product_match_candidates` | AI dedup merge candidates pending admin review |
| `product_merge_log` | Permanent audit trail of approved product merges |
| `package_size_remediation_log` | Audit trail of catalog integrity remediations |
| `app_config` | Runtime feature flags |

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://...` |
| `REDIS_URL` | Yes | `redis://localhost:6379/0` |
| `FIREBASE_PROJECT_ID` | Yes | Firebase project ID |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes | Path to Firebase service account key file |
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `RESEND_API_KEY` | No | Resend email API key |
| `SECRET_KEY` | Yes | Random secret for signing tokens |
| `ENVIRONMENT` | No | `development` / `production` |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` |
| `OBJECT_STORAGE_BACKEND` | No | `local` / `r2` / `s3` |
| `R2_ACCOUNT_ID` | If R2 | Cloudflare account ID |
| `R2_BUCKET` | If R2 | R2 bucket name |
| `R2_ACCESS_KEY_ID` | If R2 | R2 access key |
| `R2_SECRET_ACCESS_KEY` | If R2 | R2 secret |
| `SENTRY_DSN` | No | Sentry DSN |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins |

### Web (`web/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_API_URL` | No | Backend base URL (default: `http://localhost:8010`) |
| `VITE_FIREBASE_API_KEY` | Yes | Firebase web API key |
| `VITE_FIREBASE_AUTH_DOMAIN` | Yes | Firebase auth domain |
| `VITE_FIREBASE_PROJECT_ID` | Yes | Firebase project ID |
| `VITE_FIREBASE_APP_ID` | Yes | Firebase app ID |

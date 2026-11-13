# DealFindio Wiki

> **Australian grocery deal-finding platform** — track prices across major supermarket chains, get notified when prices drop, verify your receipt for overcharges, and let AI help you decide when to buy.

DealFindio is **mobile-first**. The primary experience is the Expo / React Native app, with a full-featured web companion.

---

## Quick Navigation

| Section | What's inside |
|---------|---------------|
| [[Features]] | Complete feature overview with screenshots |
| [[Getting-Started]] | Local development setup — backend, web, and mobile |
| [[Architecture]] | System diagram, design decisions, and tech stack |
| [[Data-Pipeline]] | Scraper pipeline, deduplication, catalog integrity audit |
| [[AI-and-ML]] | Claude integrations — receipt OCR, AI dedup, price predictions |
| [[API-Reference]] | Endpoint groups, authentication, notable endpoints |
| [[Mobile-App]] | Expo/React Native screens, permissions, build guide |
| [[Notifications]] | In-app bell, push (Expo/APNs/FCM), email (Resend) |
| [[Contributing]] | Branch naming, code style, PR workflow |

---

## Platform at a Glance

```
Store catalogues (Coles · Woolworths · ALDI · IGA · Drakes)
         ↓
  Playwright catalog crawlers  ←  Admin-triggered or cron
         ↓
  AI deduplication + catalog integrity (Claude)
         ↓
  PostgreSQL + pgvector + PostGIS  (Railway)
         ↓
  FastAPI REST API  (Railway)
         ↙                ↘
React Web App         Expo React Native App
(Vercel)              (iOS · Android)
         ↓
  User: browse deals · watchlist · scan receipt · get notified
```

---

## Supported Chains

| Chain | Catalog scraper | Notes |
|-------|-----------------|-------|
| Coles | `catalog_coles.py` | Full + delta crawl |
| Woolworths | `catalog_woolworths.py` | Marketplace SKUs excluded |
| ALDI | `catalog_aldi.py` | Weekly specials model |
| IGA | `catalog_iga.py` | Regional availability-aware |
| Drakes | `catalog_drakes.py` | SA/QLD independent chain |

---

## Latest Epics

| Epic | Status | Focus |
|------|--------|-------|
| #60 Watchlist Notifications | Backlog | In-app bell · Expo push · Resend email |
| #73 Product Recall Alerts | Backlog | ACCC ingestion · recall badges · notifications |
| #87 Receipt Price Verification | Backlog | Claude OCR · SCOP policy · overcharge detection |
| #48 Canonical Product Data Model | Active | ProductRetailerListing · package size normalisation |
| #17 AI Deduplication | Active | Validation · vector matching · ProductMergeLog |

Full board → https://github.com/users/mmbspk/projects/1

---

## Repository

→ [github.com/mmbspk/dealfindio](https://github.com/mmbspk/dealfindio)

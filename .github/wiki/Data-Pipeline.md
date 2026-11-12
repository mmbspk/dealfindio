# Data Pipeline

How prices get from supermarket catalogues to the DealFindio database.

---

## End-to-End Flow

```
1. Trigger           Admin panel button  OR  Celery Beat cron
        ↓
2. Scraper           Playwright catalog crawlers (per chain)
        ↓
3. Normalise         catalog_ingest.py: raw → Product + PriceReport
        ↓
4. Dedup             Deterministic (GTIN) → AI-assisted (Claude)
        ↓
5. Integrity         Catalog audit: classify issues
        ↓
6. Remediate         Admin approves remediation plan → apply changes
        ↓
7. Enrich            Images (Open Food Facts), embeddings (pgvector), predictions (ML)
        ↓
8. Serve             FastAPI: deals feed, search, price history, predictions
```

---

## Price Report Sources

Every price in the system is stored as a `PriceReport` with a `source` value and a `confidence` score.

| Source | `source` value | Default confidence | Created by |
|--------|---------------|-------------------|------------|
| Automated catalog crawl | `flyer_coles`, `flyer_woolworths`, etc. | 0.9 | Playwright catalog scrapers |
| Community report | `community` | 0.5–1.0 (scored) | User submission via app (geofenced) |
| Receipt OCR | `receipt_ocr` | 0.7 | Claude Vision on receipt photos |

---

## Catalog Scrapers

| Chain | File | Mode | Notes |
|-------|------|------|-------|
| Coles | `scrapers/catalog_coles.py` | Full + delta | Hardened pack-size extraction |
| Woolworths | `scrapers/catalog_woolworths.py` | Full + delta | 10-digit stockcode filter excludes marketplace |
| ALDI | `scrapers/catalog_aldi.py` | Weekly specials | ALDI-specific pricing model |
| IGA | `scrapers/catalog_iga.py` | Full + delta | Regional availability-aware |
| Drakes | `scrapers/catalog_drakes.py` | Full | SA/QLD independent chain |

**Delta mode** only fetches products whose prices have changed since the last crawl — faster and less load on the chain's servers.

### Scheduling

Full crawls are triggered on-demand from the admin panel. Delta crawls run on a per-chain Celery Beat schedule, configured via environment variables:

```dotenv
CATALOG_COLES_CRON=0 3 * * *
CATALOG_WOOLWORTHS_CRON=30 3 * * *
```

Each chain's specials rotate on a known schedule (`services/specials_schedule.py` encodes these). The Beat schedule is designed to run the delta crawl after the specials rotation so prices are up-to-date by the time users open the app.

---

## Product Deduplication

Products scraped from different chains may represent the same physical item. DealFindio merges these through a two-stage pipeline:

### Stage 1 — Deterministic

1. **GTIN match** — if two products share the same EAN-13 barcode, they are automatically merged
2. **High-similarity match** — name + brand similarity above a threshold (using `pg_trgm`) queues a merge candidate for review

### Stage 2 — AI-assisted

For products that pass a similarity threshold but don't match deterministically, Claude compares the product pair (name, brand, category, package size, image) and recommends:

- **Merge** — same product, proceed
- **Keep separate** — different products that happen to have similar names
- **Flag for human review** — uncertain; needs admin decision

Merge candidates land in `product_match_candidates`. Admins review and approve via the admin panel.

### Audit Trail

Every approved merge is recorded in `ProductMergeLog` with:
- Before state of both products
- Merge direction (which product became the canonical record)
- Approving admin
- Timestamp

---

## Catalog Integrity Audit

A read-only audit tool (`scripts/catalog_integrity_audit.py`) classifies all products by 7 issue subtypes:

| Subtype | Description |
|---------|-------------|
| `missing_package_size` | No `package_size` or `package_size_norm` |
| `bad_unit_price` | `unit_price` present but `unit_price_label` missing or inconsistent |
| `category_mismatch` | Chain category doesn't map to any canonical category |
| `duplicate_listing` | Two `ProductRetailerListing` rows for the same product + chain |
| `stale_price` | Most recent `PriceReport` is older than expected crawl interval |
| `missing_image` | No `image_url` or `external_image_url` |
| `orphaned_listing` | `ProductRetailerListing` with no matching canonical `Product` |

The audit produces a JSON remediation plan. An admin reviews it in the admin panel and approves before any changes are applied.

Remediation is recorded in `PackageSizeRemediationLog` (and similar per-subtype logs) for full auditability.

---

## Product Enrichment

After ingestion and deduplication, products are enriched in the background:

| Enrichment | Task | Source |
|------------|------|--------|
| Product images | `enrich_product_images` | Open Food Facts (by GTIN) |
| pgvector embeddings | `embed_product` / `backfill_product_embeddings` | Anthropic Claude text embeddings |
| Price predictions | `refit_and_predict_all` | Prophet / ARIMA models on price history |
| Unit price normalisation | `package_size.py` | Regex + rule-based pack size extraction |

---

## Community Report Validation

When a user submits a price report via the app:

1. **Geofence check** — report is rejected if the user's GPS is > 200m from the nominated store
2. **Rate limit check** — max 20 community reports per user per 24 hours
3. **Confidence scoring** (async, via Celery) — `validate_community_report` task:
   - Fetches the last 30 price reports for the same product + store
   - Runs 3σ outlier detection weighted by reporter reputation
   - Sets `confidence` score on the new report
4. **Reputation update** — `recalculate_reporter_reputation` runs after validation; updates contributor tier

---

## Specials Expiry

Specials that have passed their `valid_to` date are marked inactive by the `expire_stale_specials` Beat task. This runs every 15 minutes to ensure the deals feed never shows expired specials.

# AI & ML

DealFindio uses Anthropic Claude for several AI-powered features and runs local ML models for price predictions.

---

## Claude Integrations

Claude-backed features require configured Anthropic credentials; the backend can resolve credentials from runtime database configuration as well as environment settings. Local embeddings do not require an Anthropic key.

### Receipt OCR — `services/receipt_parser.py`

**Model:** `claude-sonnet-4-5` (vision)

User uploads a photo of a store receipt. Claude extracts structured line items:

```json
{
  "store_name": "Coles Chatswood",
  "receipt_date": "2026-10-15",
  "total": 47.85,
  "products": [{"name":"Full Cream Milk 2L","price":2.85,"quantity":1,"unit_price":2.85}]
}
```

The extracted items are then matched to known products in the database using fuzzy name matching (`ilike` query). Each matched item creates a `PriceReport` with `source='receipt_ocr'` and `confidence=0.7`.

**Limits:**
- Maximum image size: 5 MB
- Minimum OCR confidence: 0.7 (items below this are returned as unmatched)
- Supported formats: JPEG, PNG, WebP (HEIC is not directly supported)

### Receipt Price Verification — planned (Epic #87)

The implemented endpoint is `POST /api/v1/receipts/upload`; it extracts line items and may create community reports. There is **no registered** `/api/v1/receipts/verify` route or SCOP remedy response. Read-only comparison against shelf prices and any retailer-specific policy guidance remain proposed capabilities, not production API functionality.

### AI-Assisted Deduplication — `services/ai_dedup.py`

**Default model:** `claude-sonnet-4-6` (configurable)

For product pairs that pass the deterministic similarity threshold but aren't a certain match, Claude reviews:
- Product names and brands
- Category assignments
- Package sizes
- Images (if available)

The AI judge returns `same_product`, `confidence`, `keep` (`a` or `b`) and `reason`; deterministic same-chain and brand-conflict guards take precedence over the model's output.

The API key can be overridden from the `app_config` database table — useful for rotating keys without a deploy.

### Category Normalisation — `services/category_normaliser.py`

**Model:** `claude-sonnet-4-5`

Each chain uses its own category taxonomy. This service maps raw chain categories (e.g. "Chilled & Dairy", "Frais", "Fresh Food > Dairy") to DealFindio's canonical category tree. Results are cached per chain+category string to avoid repeated Claude calls.

### Product Embeddings — `services/embeddings.py`

**Model:** local SentenceTransformers `all-MiniLM-L6-v2` (384-dimensional; no Claude embedding API)

Each product's name, brand, category, and description are embedded into a pgvector vector. These enable semantic search — e.g. "greek yoghurt" finds products named "Natural Yoghurt Thick & Creamy" even without exact word overlap.

Embeddings are generated asynchronously via the `embed_product` Celery task when a new product is created, and batch-backfilled via `backfill_product_embeddings`.

---

## Price Predictions — ML

### Models

| Model | When used | Minimum history |
|-------|-----------|-----------------|
| Prophet | Default | ≥14 data points |
| ARIMA | Fallback for sparse history | ≥7 data points |
| Rule-based | No history | Returns null |

### How it works

1. Price history for each product is aggregated as a weekly time series
2. Prophet models weekly specials seasonality + long-term trend
3. The model predicts the next 8 weeks of prices
4. A "buy now" signal fires when the current price is at or below the model's predicted minimum for the next 4 weeks
5. Confidence is the model's prediction interval width — wider intervals produce lower confidence scores

### Refit schedule

Models refit weekly via a Celery Beat task (`refit_and_predict_all`). Predictions are stored in the `predictions` table and expire after 8 days (a new prediction replaces them on the next refit).

### Watchlist integration

The `notify_prediction_drops` task is registered but **not scheduled hourly** in the current Celery Beat configuration. Prediction notifications require an explicitly configured dispatch path.

---

## Semantic Search

Search queries go through a hybrid pipeline:

1. **Full-text** (`pg_trgm`) — fast trigram similarity, handles typos and partial matches
2. **Semantic** (pgvector) — embed the query and find nearest-neighbour products
3. **Merge** — combine results with a weighted rank; full-text results are prioritised for exact or near-exact matches; semantic results dominate for concept matches

The semantic leg gracefully degrades: if a query embedding can't be generated (local embedding inference unavailable), the API falls back to full-text only.

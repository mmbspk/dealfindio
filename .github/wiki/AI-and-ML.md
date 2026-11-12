# AI & ML

DealFindio uses Anthropic Claude for several AI-powered features and runs local ML models for price predictions.

---

## Claude Integrations

All Claude features require `ANTHROPIC_API_KEY` to be set in `backend/.env`.

### Receipt OCR — `services/receipt_parser.py`

**Model:** `claude-sonnet-4-5` (vision)

User uploads a photo of a store receipt. Claude extracts structured line items:

```json
{
  "store_name": "Coles Chatswood",
  "receipt_date": "2026-10-15",
  "subtotal": 47.85,
  "total": 47.85,
  "items": [
    {
      "name": "Full Cream Milk 2L",
      "quantity": 1,
      "unit_price": 2.85,
      "total_price": 2.85
    }
  ]
}
```

The extracted items are then matched to known products in the database using fuzzy name matching (`ilike` query). Each matched item creates a `PriceReport` with `source='receipt_ocr'` and `confidence=0.7`.

**Limits:**
- Maximum image size: 5 MB
- Minimum OCR confidence: 0.7 (items below this are returned as unmatched)
- Supported formats: JPEG, PNG, WebP, HEIC

### Receipt Price Verification — `routers/receipts.py`

The `/api/v1/receipts/verify` endpoint reuses `ReceiptParser` but instead of creating price reports, it:

1. Extracts line items (Claude Vision)
2. For each item, looks up the current shelf price in DealFindio's database
3. Compares: `charged_price` vs `shelf_price`
4. Flags any item where `charged_price > shelf_price`
5. Returns the overcharge amount and the relevant **Scanning Code of Practice (SCOP)** remedy

**SCOP remedy text** is per-chain and explains what the store owes the customer (typically: the first overcharged item free, subsequent items at the shelf price).

### AI-Assisted Deduplication — `services/ai_dedup.py`

**Model:** `claude-sonnet-4-5`

For product pairs that pass the deterministic similarity threshold but aren't a certain match, Claude reviews:
- Product names and brands
- Category assignments
- Package sizes
- Images (if available)

Claude responds with one of: `merge`, `keep_separate`, or `review`. Only `merge` candidates are added to `product_match_candidates` for admin review. The `keep_separate` decision is cached to avoid re-querying the same pair.

The API key can be overridden from the `app_config` database table — useful for rotating keys without a deploy.

### Category Normalisation — `services/category_normaliser.py`

**Model:** `claude-sonnet-4-5`

Each chain uses its own category taxonomy. This service maps raw chain categories (e.g. "Chilled & Dairy", "Frais", "Fresh Food > Dairy") to DealFindio's canonical category tree. Results are cached per chain+category string to avoid repeated Claude calls.

### Product Embeddings — `services/embeddings.py`

**Model:** Claude text embeddings

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

Users can set a watchlist trigger of type `prediction`. When the ML model emits a "buy now" signal for a watched product, the `notify_prediction_drops` Beat task (hourly) fires push/email/in-app notifications to matching users.

---

## Semantic Search

Search queries go through a hybrid pipeline:

1. **Full-text** (`pg_trgm`) — fast trigram similarity, handles typos and partial matches
2. **Semantic** (pgvector) — embed the query and find nearest-neighbour products
3. **Merge** — combine results with a weighted rank; full-text results are prioritised for exact or near-exact matches; semantic results dominate for concept matches

The semantic leg gracefully degrades: if a query embedding can't be generated (API key missing, timeout), the API falls back to full-text only.

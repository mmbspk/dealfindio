# Features

Complete feature catalogue for DealFindio.

---

## Price Discovery

### Deal Feed
A location-aware feed of current specials sorted by discount depth. Filters by category, chain, and distance. Powered by PostGIS proximity queries.

> **Screenshot placeholder:** `docs/screenshots/deals-feed.png`

### Product Catalogue Search
Full-text (`pg_trgm`) + semantic (`pgvector`) hybrid search across all chains. Results are ranked by relevance, with typo tolerance and synonym expansion.

### Price History Charts
90-day rolling price chart per product, broken down by chain. Built with Recharts on the web and a custom chart component on mobile.

> **Screenshot placeholder:** `docs/screenshots/price-history-chart.png`

### Price Predictions — "Buy Now or Wait?"
ML-driven recommendations powered by Prophet (primary) and ARIMA (fallback). Each prediction shows:
- Current price vs. predicted next lowest price
- Days until next predicted low
- Confidence percentage

### Unit Price Comparison
Normalised `package_size_norm` and `unit_price_label` fields let you compare true cost (e.g. $/100g or $/L) across different pack sizes from different chains — apples to apples.

### Store Locator
Interactive map and sortable list of nearby stores. Powered by PostGIS `earthdistance`/`ll_to_earth`. Tap a store to see its currently active specials.

> **Screenshot placeholder:** `docs/screenshots/store-locator.png`

---

## Community & Contributions

### Community Price Reports
Users can submit a price they see in-store. Reports are:
- Geofenced (≤200m from the nominated store, validated via device GPS)
- Rate-limited (20 reports per 24 hours)
- Scored for confidence using 3σ outlier detection weighted by reporter reputation

### Receipt OCR Upload
Photograph a store receipt. Claude Vision extracts all line items (name, quantity, unit price) and creates community price reports automatically — no manual entry.

> **Screenshot placeholder:** `docs/screenshots/receipt-upload.png`

### Receipt Price Verification _(Epic #87)_
A separate, read-only receipt flow:

1. User photographs their receipt
2. Claude Vision extracts line items
3. DealFindio compares each line against known shelf prices
4. Any overcharges are flagged with the actual vs. charged price
5. The **Australian Scanning Code of Practice (SCOP)** remedy is explained for each overcharge — at Coles, Woolworths, ALDI, and IGA the first overcharged item is free

> **Screenshot placeholder:** `docs/screenshots/receipt-verify-results.png`

### Upvote / Dispute
Users can upvote or dispute any price report. Crowd consensus raises or lowers a report's confidence score.

### Reputation System
Contributors earn points that weight their reports. Tiers:

| Tier | Minimum points | Effect on confidence |
|------|---------------|---------------------|
| Bronze | 0 | Baseline weighting |
| Silver | 500 | +10% confidence boost |
| Gold | 2 000 | +25% confidence boost |

---

## Watchlist & Notifications

### Watchlist
Track a product with any combination of:
- **Target price** — alert me when the price drops below $X
- **% off threshold** — alert me when the product is ≥ N% off
- **Prediction alert** — alert me when the ML model says "buy now"

### Watchlist Lists _(Epic #60)_
Organise watched products into named, emoji-tagged lists (e.g. 🛒 Weekly Groceries, 🎂 Baking, 🍷 Entertaining). Each list supports a per-list notification channel override.

> **Screenshot placeholder:** `docs/screenshots/watchlist-lists.png`

### In-App Notification Bell _(Epic #60)_
An unread count badge on the notification bell icon in the header. Badge updates in real-time via polling the `/api/v1/notifications/unread-count` endpoint. Tapping opens a full notification history page.

### Push Notifications _(Epic #60)_
DealFindio uses the **Expo Push API** which abstracts over iOS APNs and Android FCM. Alerts include:
- Product name, store, current price, and discount %
- Deep link back to the product page
- App icon badge count reflecting unread in-app notifications

### Email Alerts _(Epic #60)_
Powered by **Resend API**. Templates include:
- **Price match alert** — sent immediately when a watched product hits its target
- **Flash sale alert** — sent within minutes of a special going live
- **Weekly digest** — sent every Wednesday at 22:00 UTC; shows top deals grouped by watchlist list with product image cards
- **Recall alert** — sent when a watched product has a new recall notice _(Epic #73)_

All emails comply with RFC 8058 `List-Unsubscribe-Post` for one-click unsubscribe.

### Notification Preferences _(Epic #60)_
Granular per-channel (push / email) control for each trigger type:

| Trigger | Push | Email |
|---------|------|-------|
| Price match | ✓ | ✓ |
| % off threshold | ✓ | ✓ |
| Flash sale | ✓ | ✓ |
| ML prediction — "buy now" | ✓ | ✓ |
| Weekly digest | — | ✓ |
| Product recall | ✓ | ✓ |

Also supports timezone-aware quiet hours (no push during 22:00–08:00 local time by default).

---

## Admin & Operations

### Scraper Management
Trigger or cancel catalogue crawls per chain from the admin panel. Full-crawl and delta-crawl modes. Live log streaming via Redis pub/sub.

### Catalog Integrity Audit
A read-only scanner that classifies all products by 7 issue subtypes. Produces a report admins can review before any remediation runs.

### Approval-gated Remediation
The audit produces a remediation plan. Admins review and approve before any data changes are applied — prevents accidental bulk mutations.

### Deduplication Review
Inspect and approve AI-generated product merge candidates. Full audit trail kept in `ProductMergeLog`.

### Product Recall Management _(Epic #73)_
Sync ACCC product recall data (Australian Government's productsafety.gov.au), match recalls to products by GTIN, and surface recall badges on product cards.

### App Config
Runtime feature flags stored in the `app_config` database table. Change behaviour without a deploy.

---

## Data Quality Features

| Feature | Purpose |
|---------|---------|
| `package_size_norm` | Normalised pack size (e.g. "375mL 6-pack" → 2250mL) for unit price comparisons |
| `ProductRetailerListing` | Retailer-specific pricing separated from the canonical `Product` record |
| GTIN deduplication | Products with matching GTIN (EAN-13 barcode) are merged automatically |
| AI-assisted deduplication | Claude compares product pairs across chains and recommends merges |
| Marketplace exclusion | Woolworths marketplace (10-digit stockcode) SKUs filtered from the catalogue |
| Community confidence scoring | 3σ outlier detection + reporter reputation scoring on every price report |

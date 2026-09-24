# dealfindio API — Phase 2 Endpoints

Phase 2 adds community reporting, watchlists, notifications, and receipt OCR on
top of the Phase 1 product/store/deals API. All authenticated endpoints expect
a Firebase JWT in the `Authorization: Bearer <token>` header.

Full interactive docs are auto-generated at `/docs` (Swagger) and `/redoc`.

## Community Price Reports

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/price-reports/` | ✓ | Submit a community price report (geofenced ≤200m, rate-limited 20/24h) |
| GET | `/api/v1/moderation/reports?status=flagged` | ✓ | List flagged reports for review |
| POST | `/api/v1/price-reports/{id}/upvote` | ✓ | Upvote a report (+reputation to reporter) |
| POST | `/api/v1/price-reports/{id}/dispute` | ✓ | Flag a report as inaccurate |

**Submit body:**
```json
{
  "product_gtin": "9300617006552",
  "store_id": "uuid",
  "price_cents": 350,
  "was_price_cents": 500,
  "latitude": -33.87,
  "longitude": 151.21
}
```

On submit the report is stored with `source='community'`, `confidence=0.5`, then
a Celery task (`validate_community_report`) runs 3σ outlier detection and
reputation-weighted confidence scoring (see `app/services/reputation.py`).

## Users & Reputation

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/users/me` | ✓ | Current user profile with tier |
| GET | `/api/v1/users/{user_id}/profile` | — | Public profile: reputation, tier, top reports |

Reputation is 0.0–5.0 (new users start at 1.0). Tier is derived from
`contribution_count`: bronze (0–10), silver (11–50), gold (50+).

## Watchlist

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/watchlist` | ✓ | List the user's watchlist |
| POST | `/api/v1/watchlist` | ✓ | Add a product (target price and/or % off) |
| GET | `/api/v1/watchlist/{id}` | ✓ | Watchlist item detail |
| PUT | `/api/v1/watchlist/{id}` | ✓ | Update targets/chains |
| DELETE | `/api/v1/watchlist/{id}` | ✓ | Soft-delete (`is_active=false`) |

**Match rules** (evaluated by `check_watchlist_for_price` after every report):
- **price_match** — `price_cents <= target_price_cents`
- **pct_off_match** — `sale_pct_off >= target_pct_off`
- Deduped per (user, product, trigger_type) within 24h
- The reporter is never notified about their own report

## Notifications

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/notifications?limit=50&offset=0` | ✓ | Notification history (newest first) |
| PUT | `/api/v1/notifications/{id}/read` | ✓ | Mark read (`opened_at`) |
| POST | `/api/v1/notifications/fcm-token` | ✓ | Register device FCM token |
| DELETE | `/api/v1/notifications/{id}` | ✓ | Delete a notification |

Push delivery uses Firebase Cloud Messaging via the Admin SDK.

## Receipts (Claude Vision OCR)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/receipts/upload` | ✓ | Upload a receipt photo; extract line items and create `receipt_ocr` reports |

- Accepts `multipart/form-data`: `file` (JPEG/PNG/WebP, ≤5MB), optional `store_id`.
- Extracts products via Claude vision (`app/services/receipt_parser.py`),
  fuzzy-matches each to a known product, and creates `source='receipt_ocr'`
  reports (`confidence=0.7`) for matches. Unmatched items are returned for
  manual lookup rather than failing the request.

**Response:**
```json
{
  "status": "partial",
  "extracted_data": { "products": [ ... ], "store_name": "Coles", "total": 12.30 },
  "created_reports": [ { "price_report_id": "uuid", "product_name": "Milk 2L", "confidence": 0.7 } ],
  "unmatched_items": [ { "name": "Unknown item", "price": 4.80, "reason": "No matching product found" } ],
  "message": "Extracted 3 product(s). 2 matched, 1 require manual lookup."
}
```

## New environment variables

```env
FIREBASE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json   # FCM push
ANTHROPIC_API_KEY=sk-ant-...                                  # receipt OCR
```

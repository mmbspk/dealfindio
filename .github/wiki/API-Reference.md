# API Reference

DealFindio exposes a RESTful JSON API built with FastAPI.

**Interactive docs (when API is running):**
- Swagger UI → http://localhost:8010/docs
- ReDoc → http://localhost:8010/redoc

---

## Base URL

| Environment | URL |
|-------------|-----|
| Local dev | `http://localhost:8010` |
| Production | Set by Railway — configured in web/mobile env files |

---

## Authentication

All protected endpoints require a Firebase ID token:

```http
Authorization: Bearer <firebase-id-token>
```

To get a Firebase ID token (client-side):

```javascript
import { getAuth } from 'firebase/auth';
const token = await getAuth().currentUser.getIdToken();
```

The API verifies the token with Firebase Admin SDK on every request. If the token is expired or invalid, the API returns `401 Unauthorized`.

---

## Endpoint Groups

| Group | Base path | Auth required |
|-------|-----------|--------------|
| Products | `/api/v1/products` | Public (browse), Auth (watchlist actions) |
| Deals | `/api/v1/deals` | Public |
| Stores | `/api/v1/stores` | Public |
| Price Reports | `/api/v1/price-reports` | Auth |
| Watchlist | `/api/v1/watchlist` | Auth |
| Watchlist Lists | `/api/v1/watchlist/lists` | Auth |
| Users | `/api/v1/users` | Auth |
| Notifications | `/api/v1/notifications` | Auth |
| Receipts | `/api/v1/receipts` | Auth |
| Predictions | `/api/v1/predictions` | Public |
| Admin | `/api/v1/admin` | Auth + admin role |
| Health | `/health` | Public |

---

## Selected Endpoints

### Products

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/products` | List/search products — supports `q`, `category`, `chain`, `page`, `page_size` |
| `GET` | `/api/v1/products/{id}` | Product detail — includes current prices, chains, and availability |
| `GET` | `/api/v1/products/{id}/price-history` | 90-day price history broken down by chain |
| `GET` | `/api/v1/products/{id}/prediction` | Current ML buy/wait prediction |

### Deals

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/deals` | Location-aware deals feed — supports `lat`, `lng`, `radius_km`, `category`, `chain`, `page` |

### Stores

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/stores` | Nearby stores — `lat`, `lng`, `radius_km` (PostGIS) |
| `GET` | `/api/v1/stores/{id}` | Store detail + current specials |

### Price Reports

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/price-reports` | Submit a community price report (geofenced) |
| `POST` | `/api/v1/price-reports/{id}/upvote` | Upvote a price report |
| `POST` | `/api/v1/price-reports/{id}/dispute` | Dispute a price report |

### Receipts

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/receipts/upload` | Upload receipt photo → extract items → create community price reports |
| `POST` | `/api/v1/receipts/verify` | Upload receipt → compare against shelf prices → return overcharge flags + SCOP remedy |

**Receipt upload / verify request:**
```http
POST /api/v1/receipts/verify
Content-Type: multipart/form-data

file: <receipt image file>
```

**Receipt verify response (excerpt):**
```json
{
  "overcharges": [
    {
      "item_name": "Full Cream Milk 2L",
      "charged_price": 4.50,
      "shelf_price": 2.85,
      "overcharge_amount": 1.65,
      "scop_remedy": "Under the Scanning Code of Practice, Coles must give you this item for free."
    }
  ],
  "total_overcharge": 1.65,
  "items_checked": 12,
  "unmatched_items": ["CHECKOUT BAG SMALL"]
}
```

### Watchlist

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/watchlist` | Get user's watchlist items |
| `POST` | `/api/v1/watchlist` | Add a product to the watchlist |
| `PUT` | `/api/v1/watchlist/{id}` | Update target price, threshold, or trigger type |
| `DELETE` | `/api/v1/watchlist/{id}` | Remove a watchlist item |
| `GET` | `/api/v1/watchlist/lists` | Get user's watchlist lists |
| `POST` | `/api/v1/watchlist/lists` | Create a new list |
| `PUT` | `/api/v1/watchlist/lists/{id}` | Rename list or update channel preferences |

### Notifications

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/notifications` | Notification history (paginated) |
| `GET` | `/api/v1/notifications/unread-count` | Count of unread in-app notifications (for bell badge) |
| `POST` | `/api/v1/notifications/{id}/read` | Mark a notification as read |
| `POST` | `/api/v1/notifications/read-all` | Mark all notifications as read |
| `GET` | `/api/v1/notifications/preferences` | Get notification channel preferences |
| `PUT` | `/api/v1/notifications/preferences` | Update preferences (push/email/quiet hours/timezone) |

### Users

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/users/me` | Get current user profile + reputation |
| `PUT` | `/api/v1/users/me` | Update profile (display name, Expo push token) |
| `GET` | `/api/v1/users/me/reports` | User's price report history |

### Admin (requires admin role)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/admin/crawls/{chain}` | Trigger catalog crawl for a chain |
| `GET` | `/api/v1/admin/crawls` | List recent crawl runs + status |
| `GET` | `/api/v1/admin/catalog-audit` | Run read-only catalog integrity audit |
| `POST` | `/api/v1/admin/catalog-remediate` | Execute an approved remediation plan |
| `GET` | `/api/v1/admin/dedup-candidates` | List pending AI dedup merge candidates |
| `POST` | `/api/v1/admin/dedup-candidates/{id}/approve` | Approve a product merge |
| `POST` | `/api/v1/admin/dedup-candidates/{id}/reject` | Reject a merge candidate |
| `GET` | `/api/v1/admin/config` | Get all app config flags |
| `PUT` | `/api/v1/admin/config/{key}` | Update a runtime config flag |

---

## Pagination

All list endpoints that can return many results support cursor-based pagination:

```http
GET /api/v1/products?page=2&page_size=20
```

Response includes:
```json
{
  "items": [...],
  "total": 15420,
  "page": 2,
  "page_size": 20,
  "pages": 772
}
```

---

## Error Responses

| Status | Meaning |
|--------|---------|
| `400` | Bad request — validation error in request body |
| `401` | Missing or invalid Firebase JWT |
| `403` | Authenticated but not authorised (e.g. non-admin accessing admin endpoints) |
| `404` | Resource not found |
| `422` | Unprocessable entity — Pydantic validation failure (field-level errors in response) |
| `429` | Rate limited — try again later |
| `500` | Internal server error — check Sentry |

All error responses follow:
```json
{
  "detail": "human-readable error message"
}
```

For `422`, `detail` is an array of field-level errors per FastAPI/Pydantic convention.

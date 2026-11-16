# API Reference

The running FastAPI OpenAPI specification at `/openapi.json` is authoritative. Local Swagger UI is at `http://localhost:8010/docs` and ReDoc at `http://localhost:8010/redoc`. Production API URLs are deployment-dependent.

Protected endpoints require `Authorization: Bearer <firebase-id-token>`. Admin endpoints additionally require admin access.

## Verified route reference

The following paths have prefix `/api/v1` unless otherwise noted. Route paths are from the backend router source; use the OpenAPI document to confirm query parameters, request/response schemas, permissions, and any additional operations.

| Group | Method | Route |
|---|---|---|
| Products | GET | `/products`, `/products/{product_id}` |
| Deals | GET | `/deals/`, `/deals/feed` |
| Stores | GET | `/stores/nearby`, `/stores/{store_id}`, `/stores/{store_id}/deals` |
| Predictions | GET | `/predictions/{product_id}` |
| Receipts | POST | `/receipts/upload` |
| Watchlist | GET, POST | `/watchlist/` |
| Watchlist | PATCH, DELETE | `/watchlist/{list_id}` |
| Watchlist | GET, POST | `/watchlist/{list_id}/items` |
| Watchlist | PATCH, DELETE | `/watchlist/{list_id}/items/{product_id}` |
| Watchlist | GET | `/watchlist/product/{product_id}/status` |
| Notifications | GET | `/notifications/`, `/notifications/unread-count` |
| Notifications | PUT | `/notifications/read-all`, `/notifications/{notification_id}/read` |
| Notifications | DELETE | `/notifications/{notification_id}` |
| Notifications | POST | `/notifications/fcm-token` (legacy route name) |
| Admin catalog | GET | `/admin/catalog/runs`, `/admin/catalog/active` |
| Admin catalog | POST | `/admin/catalog/all`, `/admin/catalog/{chain}` |
| Admin dedup | GET | `/admin/dedup/candidates` |
| Admin dedup | POST | `/admin/dedup/candidates/{candidate_id}/decide` |
| Admin config | GET | `/admin/config` |
| Admin config | PUT | `/admin/config/{key}` |
| Health | GET | `/health` (no `/api/v1` prefix) |

The original draft showed flat watchlist CRUD, `POST` mark-read operations and `/admin/crawls` paths; those do not match the registered routers. The live OpenAPI schema supersedes any examples here. Pagination is endpoint-specific and must not be described globally as cursor-based.

## Receipt upload contract

`POST /api/v1/receipts/upload` accepts a multipart `file` (JPEG, PNG or WebP; at most 5 MiB) and optional `store_id`. The parser extracts `products`, `store_name`, `receipt_date`, `total`; each product has `name`, `price`, `quantity`, and `unit_price`. If no store is provided, no price reports are created. The API returns a wrapper describing created reports and unmatched items.

**Planned (Epic #87), not implemented:** `POST /api/v1/receipts/verify` and the previously advertised overcharge/SCOP remedy response. Likewise, there are no registered `/api/v1/admin/catalog-audit` or `/api/v1/admin/catalog-remediate` endpoints in the reviewed admin router.

## Error handling

Expect `401` for missing/invalid authentication, `403` for insufficient privileges, `404` for unknown resources, and `422` for FastAPI request validation failures. `422` validation responses use structured field details. Consult each route's OpenAPI definition for exact error and success shapes.

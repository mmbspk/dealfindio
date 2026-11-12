# Notifications

DealFindio has three notification channels: **in-app bell**, **push** (mobile), and **email**.

---

## Channels

| Channel | Technology | When delivered |
|---------|-----------|----------------|
| In-app bell | PostgreSQL `notifications_log` + polling | Instantly on any platform, web or mobile |
| Push | Expo Push API (→ iOS APNs + Android FCM) | Mobile only; requires notification permission |
| Email | Resend API | Web or mobile; user must have opted in |

---

## Trigger Types

| Trigger | Description |
|---------|-------------|
| `price_match` | Product hits the user's target price |
| `percent_off` | Product reaches or exceeds the user's % off threshold |
| `flash_sale` | A new special goes live for a watched product |
| `prediction` | ML model says "buy now" for a watched product |
| `weekly_digest` | Weekly email digest of top deals across the user's watchlist |
| `recall_alert` | A watched product has a new recall notice from ACCC |

---

## In-App Notification Bell

Every notification is logged in `notifications_log` regardless of whether push or email is also sent.

The web and mobile header polls `GET /api/v1/notifications/unread-count` every 30 seconds to keep the badge count fresh. The app icon badge on mobile reflects the same unread count.

Tapping a notification:
- Marks it as read (via `POST /api/v1/notifications/{id}/read`)
- Deep-links to the relevant product, deal, or recall page

> **Screenshot placeholder:** `docs/screenshots/notification-bell-badge.png`

---

## Push Notifications

### How it works

1. On first app launch (after login), the mobile app calls `Expo.getExpoPushTokenAsync()` to get an Expo push token
2. The token is sent to `PUT /api/v1/users/me` and stored on the user's `user_profiles` row
3. When a watchlist trigger fires, the `check_watchlist_for_price` or `notify_prediction_drops` Celery task calls `services/push_sender.py`
4. `push_sender.py` calls the **Expo Push API** at `https://exp.host/--/api/v2/push/send`
5. Expo routes the push to iOS APNs or Android FCM based on the token format
6. The notification lands on the device with a title, body, and `data` payload for deep linking

### Quiet Hours

If the user has set quiet hours in their notification preferences, the push sender checks the user's timezone and defers delivery. The default quiet window is 22:00–08:00 in the user's local timezone.

### Push Payload

```json
{
  "to": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
  "title": "Coles Full Cream Milk 2L — Now $2.85",
  "body": "Your target price of $3.00 has been hit. Save 18%.",
  "data": {
    "type": "price_alert",
    "productId": "abc123",
    "watchlistId": "def456"
  },
  "badge": 3
}
```

The `badge` value is the user's current unread in-app notification count, so the iOS app icon badge stays in sync.

---

## Email Notifications

### Provider

DealFindio sends email via the **Resend API** (`https://api.resend.com/emails`). The API key is configured via `RESEND_API_KEY` in `backend/.env`.

### Email Templates

| Template | Trigger | Key content |
|----------|---------|-------------|
| Price alert | Watchlist price match / % off | Product image card, current price, price history sparkline, watchlist target met |
| Flash sale alert | New special for watched product | Product image card, original price, sale price, chain, valid_to date |
| Weekly digest | Celery Beat — Wednesday 22:00 UTC | Top 10 deals grouped by watchlist list; product image grid |
| Recall alert | ACCC recall match for watched product | Product name, recall description, action required, ACCC recall URL |

### Deliverability

- **RFC 8058 `List-Unsubscribe-Post`** header on every email — one-click unsubscribe works in Gmail, Apple Mail, and Outlook without visiting a web page
- Unsubscribe tokens are signed with `itsdangerous.URLSafeTimedSerializer` (expire after 30 days)
- SPF, DKIM, and DMARC configured on the sending domain via Resend's DNS records
- Product images are served as CDN-linked WebP thumbnails — no base64 inline images (Gmail clips emails over 102 KB)

### Weekly Digest Schedule

```python
# celery_app.py
'send-weekly-digest': {
    'task': 'send_weekly_digest',
    'schedule': crontab(day_of_week=2, hour=22, minute=0),  # Wednesday 22:00 UTC
}
```

---

## Notification Preferences

Users can configure preferences at `/api/v1/notifications/preferences`.

| Preference | Type | Default |
|------------|------|---------|
| `push_enabled` | bool | `true` |
| `email_enabled` | bool | `true` |
| `price_match_push` | bool | `true` |
| `price_match_email` | bool | `true` |
| `percent_off_push` | bool | `true` |
| `percent_off_email` | bool | `false` |
| `flash_sale_push` | bool | `true` |
| `flash_sale_email` | bool | `false` |
| `prediction_push` | bool | `true` |
| `prediction_email` | bool | `false` |
| `weekly_digest_email` | bool | `true` |
| `recall_push` | bool | `true` |
| `recall_email` | bool | `true` |
| `quiet_hours_start` | time string | `"22:00"` |
| `quiet_hours_end` | time string | `"08:00"` |
| `timezone` | IANA tz | `"Australia/Sydney"` |

### Per-List Channel Overrides

Each watchlist list can override the default channel preferences. For example, a "🎂 Baking" list can have push disabled but email enabled, regardless of the global push setting.

---

## Architecture Notes

```
PriceReport saved
      ↓
check_watchlist_for_price (Celery task)
      ↓
  ┌───────────────────────────────┐
  │ For each matching watchlist:  │
  │   1. Create notifications_log │
  │   2. If push enabled + token: │
  │      → Expo Push API          │
  │   3. If email enabled:        │
  │      → Resend API             │
  └───────────────────────────────┘

notify_prediction_drops (Beat, hourly)
      ↓
  Same flow for "prediction" trigger type

send_weekly_digest (Beat, Wednesday 22:00 UTC)
      ↓
  Per-user digest email grouped by watchlist list
```

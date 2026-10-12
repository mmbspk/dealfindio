# Notification System — Release Validation Checklist

Use this document before releasing any change that touches the notification pipeline (Epic #60 and subsequent).

> **⚠️ Scope of automated tests:** The test suite in §2 mocks all external delivery calls (`_send_push`, `send_price_alert_email`, `send_weekly_digest_email`). Tests prove the pipeline logic is correct, but they **do not prove** real Expo or Resend delivery works. Sections §3–§5 must be executed manually on a physical device with live credentials before each production release. Mark each item with the date it was last verified.

---

## 1. Automated CI gates

Run these before raising a PR. All must pass.

```bash
# Backend
cd backend
poetry run alembic upgrade head           # migration chain must apply cleanly
poetry run alembic heads                  # must show exactly one head
poetry run pytest tests/ -m integration   # all integration tests
poetry run ruff check .                   # zero errors
poetry run black --check .               # zero reformats

# TypeScript
cd ../mobile
npx tsc --noEmit                         # zero type errors
```

Expected: 0 failures on each command.

---

## 2. Backend smoke tests (automated, story #84)

The following test files cover the full notification pipeline. Run them as part of CI:

| File | Story | What it covers |
|---|---|---|
| `test_notification_e2e.py` | #66 | Happy path, quiet hours, dedup, channel override ×5, push payload, weekly digest, RFC 8058 |
| `test_notification_story61.py` | #61 | Unread count, push badge, deep-link payload, badge sync triggers |
| `test_notification_story64.py` | #64 | `_resolve_channels`, channel override API, digest suppression |
| `test_email_deliverability_story63.py` | #63 | List-Unsubscribe headers, HTML size, unsubscribe endpoints |
| `test_notification_copy.py` | #62/#65 | Subject lines, push body lengths, HTML templates |
| `test_notification_integration.py` | #84 | Lifecycle, ownership, per-list isolation, push failure, RFC 8058 subsequent-alert, digest empty, global prefs, payload completeness |

Mobile unit tests (story #93):

```bash
cd mobile
npm test
```

| File | Story | What it covers |
|---|---|---|
| `__tests__/useNotificationHandler.test.ts` | #93 | setNotificationHandler, foreground config, background tap navigation, mark-as-read, badge refresh, missing/invalid product_id, null notification_id, dedup, cold-start, subscription cleanup |

> **NOT EXECUTED in CI:** actual Expo push delivery, Resend email delivery, and mobile UI interactions. Those require §3–§5 below.

---

## 3. Manual Android checklist

**NOT EXECUTED** — run on a physical device before each production release (emulators suppress Expo push). Use `expo start --dev-client` targeting the test environment.

### 3.1 Push notification delivery

- [ ] Sign in. Confirm token registered: check server logs for `Push token registered for <email>`.
- [ ] Add a product to a watchlist with a `target_price_cents` below the current price.
- [ ] Seed a `PriceReport` below the target via the admin API (`POST /api/v1/admin/price-reports/`) or directly with `psql`.
- [ ] Trigger the Celery task: `celery -A app.workers.celery_app call app.workers.notification_tasks.check_watchlist_for_price --args='["<report-uuid>"]'`
- [ ] **Expected:** notification appears in the Android notification drawer within 30 s.
- [ ] Tap the notification while the app is in the **background**.
- [ ] **Expected:** app navigates to the matching product screen.
- [ ] Tap the notification while the app is in the **foreground**.
- [ ] **Expected:** in-app alert shown; badge updates.
- [ ] Force-quit the app. Tap the notification from the drawer (cold start).
- [ ] **Expected:** app opens and navigates to the product screen.

### 3.2 Notification preferences

- [ ] Go to Settings → Notifications. Toggle **Price match push** off.
- [ ] Trigger another price alert.
- [ ] **Expected:** no push notification delivered; in-app row still visible in the notifications list.
- [ ] Toggle it back on.

### 3.3 Per-list channel override

- [ ] On the Watchlist screen, tap the **bell icon** next to the watchlist list name.
- [ ] In the picker, select **None (suppress all)**.
- [ ] Trigger a price alert for a product in that list.
- [ ] **Expected:** no in-app, no push, no email for that list.
- [ ] Tap the bell icon again; select **Inherit** and verify notifications resume.

### 3.4 In-app notification list

- [ ] Open the notification bell/tab.
- [ ] **Expected:** unread notifications shown, unread count badge non-zero.
- [ ] Tap a notification to mark it read.
- [ ] **Expected:** unread count badge decrements.
- [ ] Tap **Mark all read**.
- [ ] **Expected:** badge clears to zero.
- [ ] Delete a notification.
- [ ] **Expected:** notification removed from list; if it was unread, badge decrements.

### 3.5 Quiet hours

- [ ] In Settings, set quiet hours to cover the current time (e.g. 00:00–23:59).
- [ ] Trigger a price alert.
- [ ] **Expected:** no push delivered; email still arrives (if email is enabled).
- [ ] Reset quiet hours.

---

## 4. Manual iOS checklist

**NOT EXECUTED** — run on a physical iPhone before each production release (simulator does not receive remote push). Steps mirror Android §3.

### 4.1 Push notification delivery

Same steps as Android §3.1. Additional iOS-specific checks:

- [ ] Confirm the Expo token starts with `ExponentPushToken[` (not FCM format).
- [ ] Test notification tap from the **lock screen**.
- [ ] **Expected:** Face/Touch ID, then navigate to product screen.

### 4.2 iOS badge count

- [ ] Verify the app icon badge increments each time an unread in-app notification is created.
- [ ] Verify the badge resets to 0 after **Mark all read**.

### 4.3 Permission prompt

- [ ] Fresh install. Open app.
- [ ] **Expected:** iOS permission prompt appears for notifications.
- [ ] Deny permission; confirm token is not registered (no POST `/api/v1/notifications/fcm-token`).
- [ ] Go to Settings → DealFindio → Notifications → enable. Re-open app.
- [ ] **Expected:** token registered, test push "Push notifications enabled!" arrives.

---

## 5. Live Resend email verification

**NOT EXECUTED** — run with live credentials before each production release.

### Prerequisites

- `RESEND_API_KEY` must be set in the backend environment.
- A verified sending domain must exist in the Resend dashboard.
- Use a real email address you can receive on (not a placeholder).

### 5.1 Price alert email

- [ ] Create a user with `notif_email_price_match=True` and a real email address.
- [ ] Trigger `check_watchlist_for_price` for a matching price_match report.
- [ ] **Expected within 60 s:** email arrives with:
  - Subject matches `"You hit your target — <Product> at $<price>"` (≤50 chars; varies by trigger type — `pct_off_match` uses `"{pct}% off: <Product>"`; `flash_sale` uses `"Flash deal: <Product> {pct}% off"`).
  - Product image (CDN URL, not base64), only when `image_url` is set.
  - Price in green bold; was-price struck through in grey (only when `was_price` is set).
  - The product card itself is the clickable link (no separate CTA button in price alerts).
  - "Unsubscribe from these alerts" link in footer.
- [ ] Check email headers in raw view:
  - `List-Unsubscribe: <https://…/api/v1/notifications/unsubscribe?token=…>, <mailto:unsubscribe@…>`
  - `List-Unsubscribe-Post: List-Unsubscribe=One-Click`
- [ ] Click the unsubscribe link from the email footer.
- [ ] **Expected:** confirmation page with a **Confirm unsubscribe** button (GET is non-mutating; no preference is changed yet).
- [ ] Click **Confirm unsubscribe**.
- [ ] **Expected:** success page shown ("You've been unsubscribed successfully.").
- [ ] Trigger another price alert for the same user.
- [ ] **Expected:** no email sent (`notif_email_price_match` is now False).

### 5.2 Weekly digest email

- [ ] Ensure at least one product in the user's watchlist has `is_on_sale=True` and `sale_pct_off > 0`.
- [ ] Trigger: `celery -A app.workers.celery_app call app.workers.notification_tasks.send_weekly_digest`
- [ ] **Expected within 60 s:** email arrives with:
  - Subject: `"Your deals this week 🛒"` (≤50 chars).
  - At least one list section with a product card.
  - Product images (CDN URLs), when available.
  - "View All Deals →" CTA button linking to `/watchlist`.
  - Unsubscribe link in footer.
- [ ] Ensure a user with `notif_email_weekly_digest=False` does **not** receive the digest.

### 5.3 Email size check

- [ ] If a user has many watchlist items, verify the digest does not exceed 90 KB (check server logs for `[WARNING] … exceeds … clip threshold`).

---

## 6. DB / migration verification

```bash
# Check Alembic heads after applying all migrations
poetry run alembic upgrade head
poetry run alembic heads
# Expected: exactly one head at revision p1k2l3m4n5o6

# Verify watchlist_lists has channel_override column
psql $DATABASE_URL -c "\d watchlist_lists" | grep channel_override
# Expected: column channel_override character varying(10)
```

---

## 7. Known gaps (pre-release blockers)

| # | Gap | Issue | Severity |
|---|---|---|---|
| 1 | ~~Mobile: no push notification tap handler~~ | [#93](https://github.com/mmbspk/dealfindio/issues/93) | ✅ Resolved — story #93 |
| 2 | Mobile: no Jest / @testing-library/react-native | — | ✅ Resolved — story #93 |
| 3 | Expo push receipts never polled | — | Medium — stale tokens accumulate silently |
| 4 | No batch push sends | — | Low — performance concern under load |
| 5 | `_fire_badge` ignores bool return from `send_expo_badge_async` | — | Low — badge sync failures not surfaced |
| 6 | `notify_prediction_drops` has zero test coverage | — | Medium — production task untested |

**Pre-release blockers resolved. App is ready for manual end-to-end validation (§3–§5).**

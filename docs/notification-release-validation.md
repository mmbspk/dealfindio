# Notification System — Release Validation Checklist

Use this document before releasing any change that touches the notification pipeline (Epic #60 and subsequent).

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

---

## 3. Manual Android checklist

Perform on a physical device (emulators suppress Expo push). Use `expo start --dev-client` with a `EXPO_TOKEN` targeting the test environment.

### 3.1 Push notification delivery

- [ ] Sign in. Confirm token registered: check server logs for `Push token registered for <email>`.
- [ ] Add a product to a watchlist with a `target_price_cents` below the current price.
- [ ] Seed a `PriceReport` below the target (use the admin API or Django shell).
- [ ] Trigger `check_watchlist_for_price` for the report ID.
- [ ] **Expected:** notification appears in the Android notification drawer within 30 s.
- [ ] Tap the notification while the app is in the **background**.
- [ ] **Expected:** app navigates to the matching product screen. ⚠️ *Currently a blocking gap — see issue #93.*
- [ ] Tap the notification while the app is in the **foreground**.
- [ ] **Expected:** in-app alert shown; badge updates. ⚠️ *Currently a blocking gap — see issue #93.*
- [ ] Force-quit the app. Tap the notification from the drawer (cold start).
- [ ] **Expected:** app opens and navigates to the product screen. ⚠️ *Currently a blocking gap — see issue #93.*

### 3.2 Notification preferences

- [ ] Go to Settings → Notifications. Toggle **Price match push** off.
- [ ] Trigger another price alert.
- [ ] **Expected:** no push notification delivered; in-app row still visible in the notifications list.
- [ ] Toggle it back on.

### 3.3 Per-list channel override

- [ ] Long-press a watchlist list → select **Notification settings** (bell icon).
- [ ] Set the override to **None (suppress all)**.
- [ ] Trigger a price alert for a product in that list.
- [ ] **Expected:** no in-app, no push, no email for that list.
- [ ] Set override back to **Inherit** and verify notifications resume.

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

Perform on a physical iPhone (simulator does not receive remote push). Steps mirror Android.

### 4.1 Push notification delivery

Same steps as Android §3.1. Additional iOS-specific checks:

- [ ] Confirm the Expo token starts with `ExponentPushToken[` (not FCM format).
- [ ] Test notification tap from the **lock screen**.
- [ ] **Expected:** Face/Touch ID, then navigate to product screen. ⚠️ *Blocked by issue #93.*

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

### Prerequisites

- `RESEND_API_KEY` must be set in the backend environment.
- A verified sending domain must exist in the Resend dashboard.
- Use a real email address you can receive on (not a placeholder).

### 5.1 Price alert email

- [ ] Create a user with `notif_email_price_match=True` and a real email address.
- [ ] Trigger `check_watchlist_for_price` for a matching report.
- [ ] **Expected within 60 s:** email arrives with:
  - Subject: `Price drop: <Product> — $<price>` (≤50 chars).
  - Product image (CDN URL, not base64).
  - Price in bold; was-price struck through (if applicable).
  - "View deal" CTA button.
  - Unsubscribe link in footer.
- [ ] Check email headers in raw view:
  - `List-Unsubscribe: <https://…/api/v1/notifications/unsubscribe?token=…>, <mailto:unsubscribe@…>`
  - `List-Unsubscribe-Post: List-Unsubscribe=One-Click`
- [ ] Click the unsubscribe link.
- [ ] **Expected:** confirmation page shown.
- [ ] Trigger another price alert for the same user.
- [ ] **Expected:** no email sent (preference now False).

### 5.2 Weekly digest email

- [ ] Ensure at least one product in the user's watchlist has `is_on_sale=True` and `sale_pct_off > 0`.
- [ ] Trigger `send_weekly_digest` (via admin panel or `celery call`).
- [ ] **Expected within 60 s:** email arrives with:
  - Subject: `Your deals this week 🛒` (≤50 chars).
  - At least one list section with a product card.
  - Product images (CDN URLs).
  - Unsubscribe link in footer.
- [ ] Ensure a user with `notif_email_weekly_digest=False` does **not** receive the digest.

### 5.3 Email size check

- [ ] If a user has many watchlist items, verify the digest does not exceed 90 KB (check server logs for `[WARNING] email HTML exceeds 90KB clip threshold`).

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
| 1 | Mobile: no push notification tap handler | [#93](https://github.com/mmbspk/dealfindio/issues/93) | **Blocking** — tapping a push does nothing |
| 2 | Mobile: no Jest / @testing-library/react-native | — | High — zero mobile test coverage |
| 3 | Expo push receipts never polled | — | Medium — stale tokens accumulate silently |
| 4 | No batch push sends | — | Low — performance concern under load |
| 5 | `_fire_badge` ignores bool return from `send_expo_badge_async` | — | Low — badge sync failures not surfaced |
| 6 | `notify_prediction_drops` has zero test coverage | — | Medium — production task untested |

**Do not ship to production until gap #1 (issue #93) is resolved.**

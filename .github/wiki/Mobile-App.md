# Mobile App

DealFindio's primary experience is the Expo / React Native app.

> **Mobile-first:** Every feature is designed for touch-first interaction, bottom navigation, thumb-zone primary actions, and offline resilience.

---

## Technology

| Layer | Technology |
|-------|-----------|
| Framework | React Native 0.81.5 + Expo SDK 54 |
| Language | TypeScript (strict) |
| Navigation | Expo Router (file-based routes) |
| State | Zustand |
| HTTP | Axios + AsyncStorage token |
| Auth | Firebase JS SDK |
| Camera | expo-camera (barcode scanning) |
| Location | expo-location (geofence validation) |
| Image picker | expo-image-picker (receipt capture) |
| Push | Expo Notifications (APNs + FCM via Expo Push API) |
| Build | EAS Build (iOS + Android) |

---

## Screens

### Tab Navigator (bottom navigation)

| Tab | Route | Purpose |
|-----|-------|---------|
| Deals | `(tabs)/deals` | Location-aware deals feed |
| Search | `(tabs)/search` | Product search with barcode scan shortcut |
| Watchlist | `(tabs)/watchlist` | Tracked products organised by list |
| Stores | `(tabs)/stores` | Nearby store map and list |
| Profile | `(tabs)/profile` | Reputation tier, notification history |

### Other Screens

| Screen | Route | Purpose |
|--------|-------|---------|
| Barcode Scanner | `scan` | Camera barcode scan → product detail |
| Product Detail | `product/[id]` | Price history, watchlist controls, prediction, availability by chain |
| Report Price | `report-price` | Community price report (geofenced ≤200m) |
| Receipt Upload | `upload-receipt` | Camera capture → receipt OCR → community price reports |
| Receipt Verify | `verify-receipt` | Camera capture → overcharge check → SCOP remedy display |
| Notifications | `notifications` | Full notification history; deep-link tap handling |
| Notification Preferences | `notification-preferences` | Per-channel opt-in: push, email; quiet hours; timezone |
| Login | `login` | Firebase sign-in (email/password or Google) |

---

## Key Features

### Barcode Scanning
The Search tab has a barcode icon that opens the camera in barcode mode. Scanning an EAN-13 or UPC-A barcode instantly navigates to the product detail page. Uses `expo-camera`.

### Receipt Capture
The `upload-receipt` screen uses `<input type="file" capture="environment">` (or `expo-image-picker`) to open the device camera. The captured image is sent to `POST /api/v1/receipts/upload` (community reports) or `POST /api/v1/receipts/verify` (overcharge check).

> **Screenshot placeholder:** `docs/screenshots/mobile-receipt-capture.png`

### Location-Aware Deals Feed
The Deals tab requests `WHEN_IN_USE` location permission on first open. The user's coordinates are sent as `lat`/`lng` query parameters to `/api/v1/deals`, enabling the PostGIS-backed nearby deals feed.

### Push Notifications
On profile setup or first notification preference visit, the app calls `Expo.getExpoPushTokenAsync()` and saves the token to `PUT /api/v1/users/me`. Notifications are sent via the Expo Push API (covers both iOS APNs and Android FCM).

> **Screenshot placeholder:** `docs/screenshots/mobile-push-notification.png`

### Price History
The product detail screen shows a 90-day price chart broken down by chain. Designed for a 375px-wide screen — uses a horizontally scrollable, touch-friendly chart.

---

## Permissions

| Permission | Platform | When requested | Why |
|------------|----------|----------------|-----|
| Camera | iOS + Android | Barcode scan or receipt capture | Barcode scanning and receipt photo |
| Location (when in use) | iOS + Android | First Deals tab open | Nearby deals feed + geofence validation for price reports |
| Push notifications | iOS (explicit), Android 13+ | First notification or preference visit | Watchlist price alerts and weekly digest |

---

## Running Locally

```bash
cd mobile
npm install

# Start Expo dev server
npm start          # Expo Go — scan QR with your phone

# Run on simulator
npm run ios        # iOS Simulator (requires Xcode)
npm run android    # Android Emulator
```

### Physical device
Update `app.config.js` → `expo.extra.apiUrl` to your machine's LAN IP:

```js
extra: {
  apiUrl: 'http://192.168.1.100:8010',
}
```

Then scan the Expo QR code with the Expo Go app (iOS/Android).

---

## Building for Production (EAS Build)

```bash
# Install EAS CLI (one-time)
npm install -g eas-cli
eas login

# Build iOS
npx eas build --platform ios --profile production

# Build Android
npx eas build --platform android --profile production
```

EAS Build configuration is in `mobile/eas.json`. Credentials (iOS distribution certificate, Android keystore) are managed by Expo's credential store.

---

## Type Checking

```bash
cd mobile
npm run type-check   # tsc --noEmit
```

---

## Deep Linking

Push notification taps include a `data.productId` field. The Expo Notifications listener in the app root navigates to `product/[id]` when a notification is tapped.

Notification deep-link format:
```json
{
  "type": "price_alert",
  "productId": "abc123",
  "watchlistId": "def456"
}
```

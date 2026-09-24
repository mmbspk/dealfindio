# dealfindio Mobile (React Native + Expo)

Cross-platform iOS/Android app for finding local grocery deals, reporting
community prices, and managing a watchlist.

## Tech Stack

- **Expo SDK 51** + **Expo Router** (file-based navigation)
- **React Native 0.74** + **TypeScript** (strict)
- **Zustand** — global state
- **Axios** — API client (shares the backend REST API with the web app)
- **expo-camera** — barcode scanning
- **expo-location** — geofence validation for price reports
- **expo-image-picker** — receipt photo capture/upload
- **firebase** — auth (JWT verified by the backend)

## Project Structure

```
mobile/
├── app/                        # Expo Router routes
│   ├── _layout.tsx             # Root stack (headers, screens)
│   ├── (tabs)/
│   │   ├── _layout.tsx         # Tab bar: Deals / Search / Watchlist / Profile
│   │   ├── deals.tsx           # Nearby deals (uses location)
│   │   ├── search.tsx          # Product search
│   │   ├── watchlist.tsx       # Tracked products + targets
│   │   └── profile.tsx         # Reputation, tier, quick actions
│   ├── scan.tsx                # Barcode scanner → product lookup
│   ├── report-price.tsx        # Community price report form (geofenced)
│   └── upload-receipt.tsx      # Receipt photo → OCR upload
├── src/
│   ├── api/client.ts           # Axios instance + AsyncStorage auth token
│   ├── store/store.ts          # Zustand global state
│   └── types/index.ts          # Shared TypeScript types
├── app.json                    # Expo config (permissions, plugins)
├── babel.config.js
├── tsconfig.json
└── package.json
```

## Setup

```bash
cd mobile
npm install
npm run start        # Expo dev server (scan QR with Expo Go)
npm run ios          # iOS simulator
npm run android      # Android emulator
npm run type-check   # tsc --noEmit
```

Set the backend URL in `app.json` under `expo.extra.apiUrl`
(defaults to `http://localhost:8000`). On a physical device, use your
machine's LAN IP instead of `localhost`.

## Screens

| Screen | Purpose | Backend endpoint |
|--------|---------|------------------|
| Deals | Nearby deals by location | `GET /api/v1/deals/feed` |
| Search | Product search | `GET /api/v1/products/search` |
| Watchlist | Tracked products | `GET /api/v1/watchlist` |
| Profile | Reputation + tier | `GET /api/v1/users/me` |
| Scan | Barcode → product | `GET /api/v1/products/search?gtin=` |
| Report Price | Community report | `POST /api/v1/price-reports/` |
| Upload Receipt | Receipt OCR | `POST /api/v1/receipts/upload` |

## Permissions

- **Camera** — barcode scanning + receipt photos
- **Location** (when in use) — nearby deals + geofence validation (must be
  within 200m of a store to report a price)

## Build (EAS)

```bash
npx eas build --platform ios
npx eas build --platform android
```

## Status

Scaffold complete (Phase 2, Task #24): navigation, all screens, API client,
state, barcode scanner, price reporting, and receipt upload are wired to the
backend. Firebase Auth login UI and push-notification token registration are
the remaining polish items for the Phase 2 testing wave.

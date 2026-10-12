# Local Mobile Development — DealFindio

This guide covers everything needed to build and run DealFindio on an iOS Simulator on macOS.

## Prerequisites

| Tool | Minimum version | Install |
|------|----------------|---------|
| macOS | 14 (Sonoma) | — |
| Xcode | 15 (full app, not CLT) | App Store |
| Node | 20 LTS | [nodejs.org](https://nodejs.org) or `nvm` |
| CocoaPods | 1.15 | `sudo gem install cocoapods` |
| npm | 9+ | bundled with Node |

> **Xcode 26 / 27 users:** the standalone `Simulator.app` has been replaced by **Device Hub**. All `make ios` targets handle this automatically — no action required.

---

## Initial Mac setup

```bash
# 1. Clone the repository
git clone https://github.com/mmbspk/dealfindio.git
cd dealfindio

# 2. Check prerequisites
make doctor

# 3. Install mobile dependencies
make setup-mobile

# 4. (First time only) Generate iOS native project + install Pods
make ios-clean

# 5. Build and launch on simulator
make ios
```

---

## Xcode 26+ / Device Hub compatibility

Xcode 26 removed the standalone `Simulator.app` and replaced it with **Device Hub**.

`xcrun simctl` still works correctly — `make ios` and all scripts use it directly. When the build step runs, it:

1. Resolves the target simulator UDID via `xcrun simctl list devices available`
2. Boots it with `xcrun simctl boot`
3. Opens Device Hub (or Simulator.app on Xcode ≤ 25)
4. Passes `--device <UDID>` to `expo run:ios` — this bypasses the `Simulator.app` path lookup that older Expo CLI versions fail on

If `expo run:ios` still fails, the script falls back to `xcodebuild` + `xcrun simctl install`.

---

## Expo SDK compatibility

This project uses **Expo SDK 54** with **React Native 0.81.5**. Do not upgrade the SDK solely to address local development problems — the Makefile and scripts resolve all known issues within SDK 54.

---

## Daily development commands

```bash
make ios                        # build + launch (auto-select simulator)
make ios DEVICE="iPhone 18 Pro" # specific device by name
make ios DEVICE_ID=<UUID>       # specific device by UDID

make mobile-test                # Jest
make mobile-typecheck           # TypeScript
make mobile-lint                # ESLint

make doctor                     # environment health check
```

---

## Choosing an iPhone simulator

```bash
# List available simulators
xcrun simctl list devices available | grep iPhone

# Find UDIDs for a specific device
xcrun simctl list devices available | grep "iPhone 18 Pro"
```

Pass the name or UDID to any `make ios` target:

```bash
make ios DEVICE="iPhone 16"
make ios DEVICE_ID=DB85A0D3-C4C9-48B2-A624-3F2F3935CAFA
make ios-device   # interactive prompt
```

---

## Connecting to the local backend

The app resolves the API URL in this order:

1. `EXPO_PUBLIC_API_URL` environment variable (from `mobile/.env.local`, gitignored)
2. Fallback: `http://localhost:8010`

**iOS Simulator** shares the host network — `localhost` reaches the Mac's backend directly with no configuration.

**Android Emulator** — localhost is the emulator guest; use `10.0.2.2:8010` to reach the host:

```bash
# mobile/.env.local (gitignored — copy from .env.example)
EXPO_PUBLIC_API_URL=http://10.0.2.2:8010
```

**Physical device on LAN** — find your Mac's LAN IP:

```bash
ipconfig getifaddr en0   # Wi-Fi
```

Then set `EXPO_PUBLIC_API_URL=http://192.168.x.y:8010` in `mobile/.env.local`.

Start the backend:

```bash
make dev          # starts API (port 8010), Celery worker, beat, Vite web
# or directly:
./dev.sh
```

Test connectivity:

```bash
curl http://localhost:8010/docs
```

---

## Running Jest and TypeScript checks

```bash
make mobile-test        # Jest (all __tests__/ files)
make mobile-typecheck   # TypeScript strict check
make mobile-lint        # ESLint with zero-warning threshold
```

---

## Testing Watchlist notifications

The `make ios-test` target runs a minimal simulator smoke test that:

1. Verifies the app is installed on the booted simulator
2. Launches it and checks the process is alive
3. Delivers a push payload via `xcrun simctl push` (valid product UUID)
4. Delivers a second payload with an invalid UUID to confirm no crash

> This tests the **on-device notification handling code**. It does NOT test live Expo Push delivery — that requires a real device registered with APNs.

To test with real product/notification records:

```bash
TEST_PRODUCT_ID=<uuid>       \
TEST_NOTIFICATION_ID=<uuid>  \
make ios-test
```

Manual push test (while app is running):

```bash
xcrun simctl push booted au.com.dealfindio.app scripts/dev/notification-valid.apns
```

---

## Troubleshooting CocoaPods deployment-target errors

**Symptom:**

```
The iOS Simulator deployment target 'IPHONEOS_DEPLOYMENT_TARGET' is set to 9.0,
but the supported range is 15.0 to 27.0.x.
Target: RNCAsyncStorage-RNCAsyncStorage_resources
```

**Cause:** `RNCAsyncStorage` v2.x in non-Fabric (Old Architecture) mode declares `s.platforms = { :ios => "9.0" }` for its resource-bundle target. CocoaPods writes 9.0 to that specific target; the global `platform :ios, '15.1'` and `react_native_post_install` do not override it. Xcode 26+ enforces a hard minimum of 15.0.

**Fix (already applied):** `mobile/app.config.js` includes a config plugin (`plugins/withFixPodsDeploymentTarget.js`) that appends a `post_install` block to the generated Podfile. That block raises every target's deployment target to at least 15.1, including resource-bundle targets.

**The fix survives `expo prebuild --clean`** because it is applied via a config plugin, not by editing generated files manually.

**To verify:**

```bash
make ios-clean          # clean rebuild + automatic audit
make doctor             # checks for low deployment targets
```

---

## Troubleshooting `Simulator.app` discovery failures

**Symptom:**

```
CommandError: Cannot find Simulator application.
```

**Cause:** Xcode 26+ removed `Simulator.app`. Older Expo CLI (< 0.24) looks for it at a hardcoded path.

**Fix:** Pass the simulator UDID explicitly — Expo CLI then skips path discovery:

```bash
make ios DEVICE="iPhone 18 Pro"
# or
make ios DEVICE_ID=DB85A0D3-C4C9-48B2-A624-3F2F3935CAFA
```

The `scripts/dev/ios.sh` script always resolves and passes the UDID, so `make ios` handles this automatically.

If `expo run:ios` still fails, the script falls back to `xcodebuild + xcrun simctl install`.

---

## Clean rebuild instructions

```bash
make ios-clean
```

This runs `expo prebuild --platform ios --clean` (deletes `ios/`) followed by `pod install`, then audits the generated Pods for unsupported deployment targets.

After a clean rebuild, verify with:

```bash
make doctor
make ios
```

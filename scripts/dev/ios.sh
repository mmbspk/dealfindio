#!/usr/bin/env bash
# scripts/dev/ios.sh — build, install and launch DealFindio on an iOS Simulator
#
# Usage:
#   ./scripts/dev/ios.sh                           # auto-select simulator
#   DEVICE="iPhone 18 Pro" ./scripts/dev/ios.sh    # match by name
#   DEVICE_ID=<UUID> ./scripts/dev/ios.sh          # exact UDID
#
# Also called by: make ios / make ios-device
#
# Handles both:
#   • Xcode ≤ 25 — opens Simulator.app
#   • Xcode 26+ (Device Hub) — boots via simctl, opens DeviceHub
#
# Strategy:
#   1. Find / validate target simulator UDID
#   2. Boot the simulator
#   3. Open the device viewer (Simulator.app or Device Hub)
#   4. Ensure native iOS files exist and are up-to-date (expo prebuild if stale)
#   5. Install Pods if needed
#   6. Try `expo run:ios --device <UDID>` only when Simulator.app is present
#      (Xcode ≤ 25). On Xcode 27+ skip it — expo run:ios requires Simulator.app
#      internally even with --device, and it no longer exists.
#   7. xcodebuild fallback: discover workspace + scheme dynamically (no hardcoded
#      names), build once, check PIPESTATUS[0], install and launch via simctl.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MOBILE_DIR="$REPO_ROOT/mobile"
BUNDLE_ID="${BUNDLE_ID:-au.com.dealfindio.app}"
BUILD_DIR="$MOBILE_DIR/ios/build/DerivedData"

# ── Colour helpers ─────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  CYAN='\033[0;36m'; RED='\033[0;31m'; RESET='\033[0m'
else
  CYAN=''; RED=''; RESET=''
fi

step() { echo -e "${CYAN}→${RESET} $1"; }
err()  { echo -e "${RED}ERROR${RESET}: $1" >&2; exit 1; }
# shellcheck source=./ios-helpers.sh
source "$(dirname "$0")/ios-helpers.sh"

# ── 1. Find simulator UDID ────────────────────────────────────────────────────
step "Resolving simulator..."

if [[ -n "${DEVICE_ID:-}" ]]; then
  UDID="$DEVICE_ID"
  step "Using supplied DEVICE_ID: $UDID"
elif [[ -n "${DEVICE:-}" ]]; then
  UDID=$(xcrun simctl list devices available 2>/dev/null \
    | grep -i "$DEVICE" \
    | grep -oE '[A-F0-9]{8}-([A-F0-9]{4}-){3}[A-F0-9]{12}' \
    | head -1 || true)
  [[ -z "$UDID" ]] && err "No available simulator matching: $DEVICE"
  step "Matched DEVICE=\"$DEVICE\" → $UDID"
else
  # Auto-select: prefer iPhone 16 or 18 Pro, otherwise first available iPhone
  UDID=$(xcrun simctl list devices available 2>/dev/null \
    | grep -E "iPhone (18|17|16) Pro" \
    | grep -oE '[A-F0-9]{8}-([A-F0-9]{4}-){3}[A-F0-9]{12}' \
    | head -1 || true)
  if [[ -z "$UDID" ]]; then
    UDID=$(xcrun simctl list devices available 2>/dev/null \
      | grep "iPhone" \
      | grep -oE '[A-F0-9]{8}-([A-F0-9]{4}-){3}[A-F0-9]{12}' \
      | head -1 || true)
  fi
  [[ -z "$UDID" ]] && err "No available iPhone simulators. Create one in Xcode → Devices & Simulators."
  SIM_NAME=$(xcrun simctl list devices available 2>/dev/null | grep "$UDID" | sed 's/ ([A-F0-9-]*).*//' | xargs)
  step "Auto-selected: $SIM_NAME ($UDID)"
fi

# ── 2. Boot simulator ─────────────────────────────────────────────────────────
STATE=$(xcrun simctl list devices 2>/dev/null \
  | grep "$UDID" | grep -oE "Booted|Shutdown" | head -1 || true)

if [[ "$STATE" != "Booted" ]]; then
  step "Booting simulator $UDID..."
  xcrun simctl boot "$UDID"
else
  step "Simulator already booted."
fi

# ── 3. Open device viewer ─────────────────────────────────────────────────────
SIMULATOR_APP="/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app"
if [[ -d "$SIMULATOR_APP" ]]; then
  step "Opening Simulator.app..."
  open -a Simulator 2>/dev/null || true
else
  step "Opening Device Hub (Xcode 26+)..."
  open -a DeviceHub 2>/dev/null || open -a Xcode 2>/dev/null || true
fi

# ── 4. Ensure iOS native project exists and is up-to-date ────────────────────
cd "$MOBILE_DIR"
NEEDS_PREBUILD=0
if [[ ! -f "ios/Podfile" ]]; then
  step "iOS native project missing — running expo prebuild..."
  NEEDS_PREBUILD=1
elif [[ "app.config.js" -nt "ios/Podfile" ]] || \
     [[ "app.json" -nt "ios/Podfile" ]] || \
     [[ "plugins/withFixPodsDeploymentTarget.js" -nt "ios/Podfile" ]] || \
     [[ "plugins/withFmtAppleClangFix.js" -nt "ios/Podfile" ]] || \
     [[ "plugins/withIosSceneLifecycle.js" -nt "ios/Podfile" ]] || \
     [[ "package.json" -nt "ios/Podfile" ]]; then
  step "Config changed since last prebuild — regenerating native project..."
  NEEDS_PREBUILD=1
elif ! grep -q "withFixPodsDeploymentTarget" "ios/Podfile" 2>/dev/null || \
     ! grep -q "withFmtAppleClangFix" "ios/Podfile" 2>/dev/null || \
     ! grep -rq "withIosSceneLifecycle" "ios/dealfindio/AppDelegate.swift" 2>/dev/null; then
  step "Config plugins missing from existing native project — re-running expo prebuild..."
  NEEDS_PREBUILD=1
fi

if [[ "$NEEDS_PREBUILD" -eq 1 ]]; then
  npx expo prebuild --platform ios --non-interactive
fi

# ── 5. Install Pods if stale / missing ───────────────────────────────────────
NEEDS_POD_INSTALL=0
if [[ ! -d "ios/Pods" ]]; then
  NEEDS_POD_INSTALL=1
elif [[ "ios/Podfile" -nt "ios/Podfile.lock" ]]; then
  NEEDS_POD_INSTALL=1
fi
if [[ "$NEEDS_POD_INSTALL" -eq 1 ]]; then
  step "Running pod install..."
  (cd ios && pod install)
fi

# ── 6. Select build path ──────────────────────────────────────────────────────
# expo run:ios opens Simulator.app internally regardless of --device.
# Xcode 27 removed Simulator.app (replaced by Device Hub); skip expo run:ios
# when it is absent and go directly to the xcodebuild path.
if [[ -d "$SIMULATOR_APP" ]]; then
  step "Trying expo run:ios --device $UDID (Simulator.app present)..."
  if npx expo run:ios --device "$UDID" 2>&1; then
    exit 0
  fi
  step "expo run:ios failed — using xcodebuild fallback..."
else
  step "Simulator.app absent (Xcode 27+) — building with xcodebuild directly..."
fi

# ── 7. xcodebuild fallback ────────────────────────────────────────────────────
# Discover workspace and scheme dynamically — Expo may generate any name.
WORKSPACE=$(ios_find_workspace "$MOBILE_DIR/ios") || exit 1
if [[ -n "${SCHEME:-}" ]]; then
  step "Using supplied SCHEME: $SCHEME"
else
  SCHEME=$(ios_find_scheme "$WORKSPACE") || exit 1
fi

ios_build "$WORKSPACE" "$SCHEME" "$UDID" "$BUILD_DIR" "${JOBS:-}" || exit 1

APP_PATH=$(ios_find_app "$BUILD_DIR") || exit 1

step "Installing on simulator $UDID..."
xcrun simctl install "$UDID" "$APP_PATH"

# Tell RCTBundleURLProvider which port Metro is on.
# It reads NSUserDefaults["RCT_jsLocation"] before probing localhost:8081, so
# writing here ensures the native debug build connects to the right Metro
# even when the default port 8081 is occupied by another process.
EFFECTIVE_METRO_PORT="${METRO_PORT:-8081}"
step "Configuring Metro port $EFFECTIVE_METRO_PORT for iOS debug build..."
xcrun simctl spawn "$UDID" defaults write "$BUNDLE_ID" \
  RCT_jsLocation "localhost:$EFFECTIVE_METRO_PORT" || true

step "Launching $BUNDLE_ID..."
xcrun simctl launch "$UDID" "$BUNDLE_ID"

step "Starting Metro bundler on port $EFFECTIVE_METRO_PORT..."
npx expo start --dev-client --port "$EFFECTIVE_METRO_PORT"

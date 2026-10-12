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
#   4. Ensure native iOS files exist (expo prebuild if absent)
#   5. Install Pods if needed
#   6. Try `expo run:ios --device <UDID>` (skips Simulator.app discovery)
#   7. On failure, fall back to xcodebuild + simctl install + launch

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MOBILE_DIR="$REPO_ROOT/mobile"
BUNDLE_ID="${BUNDLE_ID:-au.com.dealfindio.app}"
WORKSPACE="$MOBILE_DIR/ios/dealfindio.xcworkspace"
BUILD_DIR="$MOBILE_DIR/ios/build/DerivedData"

# ── Colour helpers ─────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  CYAN='\033[0;36m'; RED='\033[0;31m'; RESET='\033[0m'
else
  CYAN=''; RED=''; RESET=''
fi

step() { echo -e "${CYAN}→${RESET} $1"; }
err()  { echo -e "${RED}ERROR${RESET}: $1" >&2; exit 1; }

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
     [[ "package.json" -nt "ios/Podfile" ]]; then
  step "Config changed since last prebuild — regenerating native project..."
  NEEDS_PREBUILD=1
elif ! grep -q "withFixPodsDeploymentTarget" "ios/Podfile" 2>/dev/null; then
  step "Deployment-target plugin not present in existing Podfile — re-running expo prebuild..."
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

# ── 6. Try expo run:ios with explicit UDID (bypasses Simulator.app lookup) ───
step "Building via expo run:ios --device $UDID..."
if npx expo run:ios --device "$UDID" 2>&1; then
  exit 0
fi

# ── 7. Fallback: xcodebuild + simctl ─────────────────────────────────────────
step "expo run:ios failed — falling back to xcodebuild..."

[[ ! -f "$WORKSPACE" ]] && err "Workspace not found at $WORKSPACE. Run: make ios-clean"

XCBUILD_LOG="$(mktemp /tmp/xcodebuild.XXXXXX.log)"
step "Building with xcodebuild (log: $XCBUILD_LOG)..."

# Disable pipefail for this pipeline so we can read PIPESTATUS[0] (xcodebuild's
# exit) without bash aborting early if grep finds no matching lines.
set +o pipefail
xcodebuild \
  -workspace "$WORKSPACE" \
  -scheme dealfindio \
  -configuration Debug \
  -destination "id=$UDID" \
  -derivedDataPath "$BUILD_DIR" \
  build 2>&1 | tee "$XCBUILD_LOG" | { grep -E "^(Build|error:|warning:|CompileSwift|Linking)" || true; }
BUILD_EXIT="${PIPESTATUS[0]}"
set -o pipefail

if [[ "$BUILD_EXIT" -ne 0 ]]; then
  echo ""
  echo "Build failed. Last 40 lines of log:"
  tail -40 "$XCBUILD_LOG"
  err "xcodebuild exited with errors. See full log: $XCBUILD_LOG"
fi

APP_PATH=$(find "$BUILD_DIR" -name "dealfindio.app" \
  -path "*/Debug-iphonesimulator/*" 2>/dev/null | head -1 || true)
[[ -z "$APP_PATH" ]] && err "Build succeeded but .app bundle not found under $BUILD_DIR"

step "Installing $APP_PATH on $UDID..."
xcrun simctl install "$UDID" "$APP_PATH"

step "Launching $BUNDLE_ID..."
xcrun simctl launch "$UDID" "$BUNDLE_ID"

step "Starting Metro bundler..."
npx expo start --dev-client

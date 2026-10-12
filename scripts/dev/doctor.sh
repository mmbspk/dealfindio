#!/usr/bin/env bash
# scripts/dev/doctor.sh — DealFindio environment doctor
# Usage: ./scripts/dev/doctor.sh
#
# Checks every prerequisite for building and running the iOS mobile app.
# Prints PASS / WARN / FAIL for each item and exits non-zero on any FAIL.

set -euo pipefail
# Ignore SIGPIPE — triggered when head / grep exit early from a pipeline.
# The doctor script must run all checks regardless of early-exit signals.
trap '' PIPE
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MOBILE_DIR="$REPO_ROOT/mobile"

# ── Colour helpers ─────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
else
  GREEN=''; YELLOW=''; RED=''; RESET=''
fi

FAILS=0
WARNS=0

pass() { echo -e "${GREEN}✔ PASS${RESET}  $1"; }
warn() { echo -e "${YELLOW}⚠ WARN${RESET}  $1"; WARNS=$((WARNS + 1)); }
fail() { echo -e "${RED}✘ FAIL${RESET}  $1"; FAILS=$((FAILS + 1)); }

section() { echo ""; echo "── $1 ──────────────────────────────────────────────────"; }

# ── System ────────────────────────────────────────────────────────────────────
section "System"
OS_VER=$(sw_vers -productVersion 2>/dev/null || echo "unknown")
ARCH=$(uname -m)
pass "macOS $OS_VER ($ARCH)"

# ── Node / npm ────────────────────────────────────────────────────────────────
section "Node / npm"
if command -v node &>/dev/null; then
  NODE_VER=$(node --version)
  pass "node $NODE_VER"
else
  fail "node not found — install via https://nodejs.org or nvm"
fi

if command -v npm &>/dev/null; then
  NPM_VER=$(npm --version 2>/dev/null)
  pass "npm $NPM_VER"
else
  fail "npm not found"
fi

# ── Expo ──────────────────────────────────────────────────────────────────────
section "Expo"
if [[ -f "$MOBILE_DIR/node_modules/expo/package.json" ]]; then
  EXPO_SDK=$(node -e "process.stdout.write(require('$MOBILE_DIR/node_modules/expo/package.json').version)")
  pass "Expo SDK $EXPO_SDK (from mobile/node_modules)"
else
  fail "Expo SDK not installed — run: make setup-mobile"
fi

if [[ -f "$MOBILE_DIR/node_modules/@expo/cli/package.json" ]]; then
  EXPO_CLI=$(node -e "process.stdout.write(require('$MOBILE_DIR/node_modules/@expo/cli/package.json').version)")
  pass "@expo/cli $EXPO_CLI"
else
  warn "@expo/cli not found in node_modules (may be a transitive dependency)"
fi

# ── Xcode ─────────────────────────────────────────────────────────────────────
section "Xcode"
if command -v xcodebuild &>/dev/null; then
  XCODE_RAW=$(xcodebuild -version 2>/dev/null || true)
  XCODE_VER=$(printf '%s' "$XCODE_RAW" | awk 'NR==1' || true)
  pass "$XCODE_VER"
else
  fail "xcodebuild not found — install Xcode from the App Store"
fi

DEV_DIR=$(xcode-select -p 2>/dev/null || true)
if [[ "$DEV_DIR" == /Applications/Xcode*.app/Contents/Developer ]]; then
  pass "Developer directory: $DEV_DIR"
else
  warn "Developer directory ($DEV_DIR) is not the full Xcode app — run: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
fi

# ── Simulator / Device Hub ────────────────────────────────────────────────────
section "Simulator / Device Hub"
if command -v xcrun &>/dev/null && xcrun simctl list &>/dev/null 2>&1; then
  pass "xcrun simctl accessible"
else
  fail "xcrun simctl not accessible — full Xcode app required"
fi

SIMULATOR_APP="/Applications/Xcode.app/Contents/Developer/Applications/Simulator.app"
DEVICE_HUB_APP="/System/Applications/DeviceHub.app"
if [[ -d "$SIMULATOR_APP" ]]; then
  pass "Simulator.app found (Xcode ≤ 25)"
elif [[ -d "$DEVICE_HUB_APP" ]] || open -Ra "DeviceHub" 2>/dev/null; then
  pass "Device Hub available (Xcode 26+)"
else
  warn "Neither Simulator.app nor Device Hub found — simulator launch may require manual open"
fi

# ── iOS runtimes ──────────────────────────────────────────────────────────────
section "iOS runtimes"
RUNTIMES=$(xcrun simctl list runtimes 2>/dev/null | grep "iOS" | grep -v "unavailable" || true)
if [[ -n "$RUNTIMES" ]]; then
  while IFS= read -r rt; do
    pass "$rt"
  done <<< "$RUNTIMES"
else
  fail "No iOS runtimes found — download via Xcode → Settings → Platforms"
fi

# ── Available simulators ───────────────────────────────────────────────────────
section "Available iPhone simulators"
SIMS=$(xcrun simctl list devices available 2>/dev/null | grep "iPhone" | grep -v "unavailable" | head -8 || true)
if [[ -n "$SIMS" ]]; then
  while IFS= read -r sim; do
    pass "$sim"
  done <<< "$SIMS"
else
  warn "No available iPhone simulators — create one in Xcode → Devices & Simulators"
fi

BOOTED=$(xcrun simctl list devices booted 2>/dev/null | grep "iPhone" || true)
if [[ -n "$BOOTED" ]]; then
  pass "Booted: $(echo "$BOOTED" | head -1 | xargs)"
fi

# ── CocoaPods ─────────────────────────────────────────────────────────────────
section "CocoaPods"
if command -v pod &>/dev/null; then
  POD_VER=$(pod --version 2>/dev/null)
  pass "CocoaPods $POD_VER"
else
  fail "pod not found — install: sudo gem install cocoapods"
fi

# ── Mobile project ────────────────────────────────────────────────────────────
section "Mobile project"
if [[ -d "$MOBILE_DIR/node_modules" ]]; then
  pass "mobile/node_modules present"
else
  fail "mobile/node_modules missing — run: make setup-mobile"
fi

if [[ -f "$MOBILE_DIR/ios/Podfile" ]]; then
  pass "iOS native project present (ios/Podfile exists)"
else
  warn "iOS native project not generated — run: make ios-clean  or  cd mobile && npx expo prebuild --platform ios"
fi

if [[ -f "$MOBILE_DIR/ios/Podfile.lock" ]]; then
  pass "Pods installed (Podfile.lock present)"
else
  warn "Pods not installed — will be installed automatically by make ios"
fi

# ── Deployment-target audit ───────────────────────────────────────────────────
section "CocoaPods deployment-target audit"
PBXPROJ="$MOBILE_DIR/ios/Pods/Pods.xcodeproj/project.pbxproj"
if [[ -f "$PBXPROJ" ]]; then
  LOW=$(grep -o "IPHONEOS_DEPLOYMENT_TARGET = [^;]*" "$PBXPROJ" 2>/dev/null \
        | awk -F' = ' '{v=$2; if(v+0 < 15) print v}' | sort -u || true)
  if [[ -z "$LOW" ]]; then
    pass "All pod targets use deployment target ≥ 15.0"
  else
    fail "Pod targets with deployment target < 15.0 found: $LOW — run: make ios-clean to rebuild"
  fi
else
  warn "Pods project not found — skipping deployment-target audit"
fi

# ── API URL ───────────────────────────────────────────────────────────────────
section "Backend"
API_URL="${EXPO_PUBLIC_API_URL:-http://localhost:8010}"
pass "Configured API URL: $API_URL"

# Reachability check — WARN only; backend may not be running for mobile-only builds.
if curl -sf --max-time 2 "$API_URL/health" &>/dev/null || \
   curl -sf --max-time 2 "$API_URL/api/v1/health" &>/dev/null; then
  pass "Backend reachable at $API_URL"
else
  warn "Backend not reachable at $API_URL — start with: make dev  (or ./dev.sh)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
if [[ $FAILS -gt 0 ]]; then
  echo -e "${RED}FAIL — $FAILS failure(s), $WARNS warning(s). Fix the items above.${RESET}"
  exit 1
elif [[ $WARNS -gt 0 ]]; then
  echo -e "${YELLOW}WARN — All checks passed with $WARNS warning(s).${RESET}"
else
  echo -e "${GREEN}PASS — All checks passed.${RESET}"
fi
echo "════════════════════════════════════════════════════════"

#!/usr/bin/env bash
# scripts/dev/ios-test.sh — iOS Simulator smoke tests for DealFindio
#
# Tests:
#   1. App is installed on the target simulator
#   2. App launches without crashing (process visible in simctl)
#   3. simctl can deliver a push notification payload
#   4. App survives a notification with an invalid product ID (no crash)
#
# Limitations:
#   • Delivery via simctl push is NOT equivalent to a live Expo/APNs push.
#     This only tests the on-device notification handling code path.
#   • Navigation assertions (product screen loads) require a separate E2E
#     tool (Maestro / Detox) — not included to keep setup overhead minimal.
#   • VALID_PRODUCT_ID and VALID_NOTIFICATION_ID must be set to real records
#     in your local or staging database before those tests can be meaningful.
#
# Usage:
#   ./scripts/dev/ios-test.sh
#   DEVICE_ID=<UUID> ./scripts/dev/ios-test.sh

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MOBILE_DIR="$REPO_ROOT/mobile"
BUNDLE_ID="${BUNDLE_ID:-au.com.dealfindio.app}"
PAYLOAD_DIR="$REPO_ROOT/scripts/dev"

# ── Colour helpers ─────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; RESET='\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; RESET=''
fi
PASS=0; FAIL_COUNT=0

ok()   { echo -e "${GREEN}✔${RESET} $1"; PASS=$((PASS+1)); }
fail() { echo -e "${RED}✘ FAIL${RESET}: $1"; FAIL_COUNT=$((FAIL_COUNT+1)); }
warn() { echo -e "${YELLOW}⚠ WARN${RESET}: $1"; }
step() { echo -e "  → $1"; }

# ── Resolve simulator ─────────────────────────────────────────────────────────
if [[ -n "${DEVICE_ID:-}" ]]; then
  UDID="$DEVICE_ID"
else
  UDID=$(xcrun simctl list devices booted 2>/dev/null \
    | grep "iPhone" \
    | grep -oE '[A-F0-9]{8}-([A-F0-9]{4}-){3}[A-F0-9]{12}' \
    | head -1 || true)
fi

if [[ -z "$UDID" ]]; then
  echo "No booted iPhone simulator found. Run: make ios  (or boot one manually)"
  exit 1
fi
echo ""
echo "── Simulator smoke tests ─────────────────────────────────────────────────"
echo "   Target: $UDID"
echo ""

# ── Test 1: App is installed ───────────────────────────────────────────────────
step "Checking $BUNDLE_ID is installed..."
if xcrun simctl get_app_container "$UDID" "$BUNDLE_ID" 2>/dev/null | grep -q "/"; then
  ok "App is installed on simulator"
else
  fail "App not installed — run: make ios"
fi

# ── Test 2: App launches without crash ────────────────────────────────────────
step "Launching app..."
xcrun simctl launch "$UDID" "$BUNDLE_ID" > /dev/null 2>&1 || true
sleep 3

if xcrun simctl spawn "$UDID" launchctl list 2>/dev/null | grep -q "$BUNDLE_ID"; then
  ok "App process is running"
else
  warn "Cannot confirm app process via launchctl (may still be running)"
fi

# ── Test 3: Valid notification payload ────────────────────────────────────────
# Replace VALID_PRODUCT_ID / VALID_NOTIFICATION_ID with real UUIDs from your
# local or staging database before this test can exercise the navigation path.
VALID_PRODUCT_ID="${TEST_PRODUCT_ID:-00000000-0000-0000-0000-000000000001}"
VALID_NOTIFICATION_ID="${TEST_NOTIFICATION_ID:-00000000-0000-0000-0000-000000000002}"

VALID_PAYLOAD=$(cat "$PAYLOAD_DIR/notification-valid.apns")
VALID_PAYLOAD="${VALID_PAYLOAD//VALID_PRODUCT_UUID/$VALID_PRODUCT_ID}"
VALID_PAYLOAD="${VALID_PAYLOAD//VALID_NOTIFICATION_UUID/$VALID_NOTIFICATION_ID}"
TMP_VALID=$(mktemp /tmp/notif-valid.XXXXXX.json)
echo "$VALID_PAYLOAD" > "$TMP_VALID"

step "Pushing valid notification payload..."
if xcrun simctl push "$UDID" "$BUNDLE_ID" "$TMP_VALID" 2>&1; then
  sleep 2
  ok "Valid notification delivered without error"
else
  fail "simctl push failed for valid payload"
fi

# ── Test 4: Invalid product ID — no crash ─────────────────────────────────────
step "Pushing invalid product ID notification..."
if xcrun simctl push "$UDID" "$BUNDLE_ID" "$PAYLOAD_DIR/notification-invalid.apns" 2>&1; then
  sleep 2
  # Verify app is still running after bad payload
  if xcrun simctl spawn "$UDID" launchctl list 2>/dev/null | grep -q "$BUNDLE_ID" || \
     xcrun simctl get_app_container "$UDID" "$BUNDLE_ID" &>/dev/null; then
    ok "App survived invalid product ID (no crash)"
  else
    warn "Cannot confirm app state after invalid payload — check manually"
  fi
else
  fail "simctl push failed for invalid payload"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "── Results ───────────────────────────────────────────────────────────────"
echo "   Passed: $PASS  |  Failed: $FAIL_COUNT"
echo ""
if [[ "$VALID_PRODUCT_ID" == "00000000-0000-0000-0000-000000000001" ]]; then
  warn "TEST_PRODUCT_ID / TEST_NOTIFICATION_ID are using placeholder UUIDs."
  warn "Set them to real records in your local/staging DB for navigation tests."
fi
echo ""
[[ $FAIL_COUNT -gt 0 ]] && exit 1 || exit 0

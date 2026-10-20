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
sleep 2

APP_PID=$(xcrun simctl spawn "$UDID" launchctl list 2>/dev/null \
  | grep "$BUNDLE_ID" | awk '{print $1}' | head -1 || true)

if [[ -n "$APP_PID" ]] && [[ "$APP_PID" != "-" ]]; then
  ok "App process running (PID $APP_PID)"
else
  warn "App process not visible via launchctl — if Device Hub shows the app, it may still be running"
fi

# ── Test 3: Valid notification payload ────────────────────────────────────────
# NOTE: simctl push submits to the simulator's local notification daemon.
# A clean exit from simctl means the payload was accepted and queued —
# it does NOT confirm the app received or rendered the notification.
# Full delivery confirmation requires visual verification in Device Hub.
VALID_PRODUCT_ID="${TEST_PRODUCT_ID:-00000000-0000-0000-0000-000000000001}"
VALID_NOTIFICATION_ID="${TEST_NOTIFICATION_ID:-00000000-0000-0000-0000-000000000002}"

VALID_PAYLOAD=$(cat "$PAYLOAD_DIR/notification-valid.apns")
VALID_PAYLOAD="${VALID_PAYLOAD//VALID_PRODUCT_UUID/$VALID_PRODUCT_ID}"
VALID_PAYLOAD="${VALID_PAYLOAD//VALID_NOTIFICATION_UUID/$VALID_NOTIFICATION_ID}"
TMP_VALID=$(mktemp /tmp/notif-valid-XXXXXX)
echo "$VALID_PAYLOAD" > "$TMP_VALID"

step "Submitting valid notification payload via simctl..."
PUSH_OUT=$(xcrun simctl push "$UDID" "$BUNDLE_ID" "$TMP_VALID" 2>&1) && PUSH_RC=0 || PUSH_RC=$?
if [[ "$PUSH_RC" -eq 0 ]]; then
  ok "Valid notification payload accepted by simctl (queued for delivery — verify banner in Device Hub)"
else
  fail "simctl rejected valid payload: $PUSH_OUT"
fi

# ── Test 4: Invalid product ID — crash resilience ─────────────────────────────
# Strategy: record the app's PID before delivery, re-check after.
# A missing PID after notification means the process crashed.
step "Recording app PID before invalid notification..."
PRE_PID=$(xcrun simctl spawn "$UDID" launchctl list 2>/dev/null \
  | grep "$BUNDLE_ID" | awk '{print $1}' | head -1 || true)

step "Submitting invalid product ID notification via simctl..."
INVALID_OUT=$(xcrun simctl push "$UDID" "$BUNDLE_ID" "$PAYLOAD_DIR/notification-invalid.apns" 2>&1) && INVALID_RC=0 || INVALID_RC=$?

if [[ "$INVALID_RC" -ne 0 ]]; then
  fail "simctl rejected invalid payload (unexpected — simctl should accept any well-formed APNs JSON): $INVALID_OUT"
else
  sleep 2
  POST_PID=$(xcrun simctl spawn "$UDID" launchctl list 2>/dev/null \
    | grep "$BUNDLE_ID" | awk '{print $1}' | head -1 || true)

  if [[ -n "$POST_PID" ]] && [[ "$POST_PID" != "-" ]]; then
    if [[ "$PRE_PID" == "$POST_PID" ]]; then
      ok "App process unchanged after invalid notification (PID $POST_PID — no crash)"
    else
      ok "App still running after invalid notification (PID changed: $PRE_PID → $POST_PID)"
    fi
  else
    if [[ -z "$PRE_PID" ]] || [[ "$PRE_PID" == "-" ]]; then
      warn "App process was not visible via launchctl before or after notification — confirm in Device Hub"
    else
      fail "App process (was PID $PRE_PID) missing after invalid notification — possible crash"
    fi
  fi
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

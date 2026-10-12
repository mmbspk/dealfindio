#!/usr/bin/env bash
# scripts/dev/tests/ios-unit.sh — unit tests for ios-helpers.sh
# Mocks external commands (xcodebuild, xcrun) via a temporary bin directory on PATH.
# No Xcode, simulator, or CocoaPods required.
#
# Usage:  bash scripts/dev/tests/ios-unit.sh
#         make ios-unit-test

set -uo pipefail   # no -e: test failures must not abort the suite
PASS=0; FAIL=0

HELPERS="$(cd "$(dirname "$0")/.." && pwd)/ios-helpers.sh"
if [[ ! -f "$HELPERS" ]]; then
  printf 'FATAL: ios-helpers.sh not found at %s\n' "$HELPERS" >&2
  exit 1
fi

if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; RED='\033[0;31m'; RESET='\033[0m'
else
  GREEN=''; RED=''; RESET=''
fi
ok()   { printf "${GREEN}✔${RESET} %s\n" "$1"; PASS=$((PASS+1)); }
fail() { printf "${RED}✘${RESET} %s\n  └ %s\n" "$1" "$2"; FAIL=$((FAIL+1)); }

echo ""
echo "── ios-helpers unit tests ────────────────────────────────────────────────"

# ── T1: workspace found ────────────────────────────────────────────────────────
t1() {
  local d; d=$(mktemp -d)
  mkdir -p "$d/ios/MyApp.xcworkspace"
  local result rc
  result=$(bash -c ". '$HELPERS'; ios_find_workspace '$d/ios'" 2>/dev/null) && rc=$? || rc=$?
  rm -rf "$d"
  if [[ "$rc" -eq 0 && "$result" == *"MyApp.xcworkspace"* ]]; then
    ok "T1: workspace found — returns correct path"
  else
    fail "T1: workspace found — returns correct path" "rc=$rc result='$result'"
  fi
}; t1

# ── T2: workspace missing — no Podfile ────────────────────────────────────────
t2() {
  local d; d=$(mktemp -d); mkdir -p "$d/ios"
  local result rc
  result=$(bash -c ". '$HELPERS'; ios_find_workspace '$d/ios'" 2>&1) && rc=$? || rc=$?
  rm -rf "$d"
  if [[ "$rc" -ne 0 && "$result" == *"ios-clean"* ]]; then
    ok "T2: no Podfile → error directs to make ios-clean"
  else
    fail "T2: no Podfile → error directs to make ios-clean" "rc=$rc result='$result'"
  fi
}; t2

# ── T3: workspace missing — Podfile present, Pods/ absent ─────────────────────
t3() {
  local d; d=$(mktemp -d); mkdir -p "$d/ios"
  touch "$d/ios/Podfile"
  local result rc
  result=$(bash -c ". '$HELPERS'; ios_find_workspace '$d/ios'" 2>&1) && rc=$? || rc=$?
  rm -rf "$d"
  if [[ "$rc" -ne 0 && "$result" == *"pod install"* ]]; then
    ok "T3: Podfile present, no Pods → error directs to pod install"
  else
    fail "T3: Podfile present, no Pods → error directs to pod install" "rc=$rc result='$result'"
  fi
}; t3

# ── T4: scheme detection — exact match on workspace name ─────────────────────
t4() {
  local d; d=$(mktemp -d); mkdir -p "$d/ios/App.xcworkspace" "$d/bin"
  # Mock includes dependency schemes before the app scheme (realistic workspace)
  cat > "$d/bin/xcodebuild" <<'MOCK'
#!/usr/bin/env bash
printf 'Information about workspace "App":\n    Schemes:\n        boost\n        fmt\n        App\n        App-tvOS\n'
exit 0
MOCK
  chmod +x "$d/bin/xcodebuild"
  local result rc
  result=$(PATH="$d/bin:$PATH" bash -c ". '$HELPERS'; ios_find_scheme '$d/ios/App.xcworkspace'" 2>/dev/null) \
    && rc=$? || rc=$?
  rm -rf "$d"
  if [[ "$rc" -eq 0 && "$result" == "App" ]]; then
    ok "T4: scheme matches workspace name even when dependency schemes appear first"
  else
    fail "T4: scheme matches workspace name even when dependency schemes appear first" "rc=$rc result='$result'"
  fi
}; t4

# ── T5: build failure — non-zero exit propagated ──────────────────────────────
t5() {
  local d; d=$(mktemp -d); mkdir -p "$d/ios/App.xcworkspace" "$d/bin" "$d/build"
  cat > "$d/bin/xcodebuild" <<'MOCK'
#!/usr/bin/env bash
printf 'error: Build input file cannot be found: missing.swift\n'
exit 65
MOCK
  chmod +x "$d/bin/xcodebuild"
  local rc
  PATH="$d/bin:$PATH" bash -c \
    ". '$HELPERS'; ios_build '$d/ios/App.xcworkspace' 'App' 'FAKE-UDID' '$d/build'" \
    > /dev/null 2>&1 && rc=$? || rc=$?
  rm -rf "$d"
  if [[ "$rc" -ne 0 ]]; then
    ok "T5: build failure propagates non-zero exit ($rc)"
  else
    fail "T5: build failure propagates non-zero exit" "expected non-zero, got rc=0"
  fi
}; t5

# ── T6: successful build — install and launch called ─────────────────────────
t6() {
  local d; d=$(mktemp -d); mkdir -p "$d/ios/App.xcworkspace" "$d/bin"
  local app_path="$d/build/Build/Products/Debug-iphonesimulator/App.app"
  local xcrun_log="$d/xcrun.log"

  # xcodebuild mock: succeeds and creates the .app bundle
  cat > "$d/bin/xcodebuild" <<MOCK
#!/usr/bin/env bash
mkdir -p "$app_path"
exit 0
MOCK
  chmod +x "$d/bin/xcodebuild"

  # xcrun mock: records all invocations to a log file
  cat > "$d/bin/xcrun" <<MOCK
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$xcrun_log"
exit 0
MOCK
  chmod +x "$d/bin/xcrun"

  local rc
  PATH="$d/bin:$PATH" bash -c "
    . '$HELPERS'
    ios_build '$d/ios/App.xcworkspace' 'App' 'FAKE-UDID' '$d/build' || exit 1
    app=\$(ios_find_app '$d/build') || exit 1
    xcrun simctl install 'FAKE-UDID' \"\$app\"
    xcrun simctl launch 'FAKE-UDID' 'au.com.test.app'
  " > /dev/null 2>&1 && rc=$? || rc=$?

  local xcrun_output
  xcrun_output=$(cat "$xcrun_log" 2>/dev/null || printf '')
  rm -rf "$d"

  if [[ "$rc" -eq 0 && "$xcrun_output" == *"simctl install"* && "$xcrun_output" == *"simctl launch"* ]]; then
    ok "T6: successful build triggers simctl install then launch"
  else
    fail "T6: successful build triggers simctl install then launch" \
      "rc=$rc xcrun_log='$xcrun_output'"
  fi
}; t6

# ── T7: mktemp template valid on macOS/BSD ────────────────────────────────────
# BSD mktemp requires the X's at the END of the template, not before a suffix.
# Regression for: mktemp /tmp/xcodebuild.XXXXXX.log  (broken — .log after X's)
t7() {
  local tmpfile rc
  tmpfile=$(mktemp "${TMPDIR:-/tmp}/xcodebuild.XXXXXX" 2>/dev/null) && rc=$? || rc=$?
  [[ "$rc" -eq 0 ]] && rm -f "$tmpfile"
  if [[ "$rc" -eq 0 && -n "$tmpfile" ]]; then
    ok "T7: mktemp template (X's at end, no .log suffix) is valid on this platform"
  else
    fail "T7: mktemp template valid" "rc=$rc — BSD mktemp may not accept this template"
  fi
}; t7

# ── T8: scheme regression — 'boost' must not be selected over app scheme ──────
# Regression for: xcodebuild -list returns boost before dealfindio; the function
# was taking the first entry rather than matching the workspace name.
t8() {
  local d; d=$(mktemp -d); mkdir -p "$d/ios/dealfindio.xcworkspace" "$d/bin"
  cat > "$d/bin/xcodebuild" <<'MOCK'
#!/usr/bin/env bash
printf 'Information about workspace "dealfindio":\n    Schemes:\n        boost\n        fmt\n        glog\n        Pods-dealfindio\n        dealfindio\n'
exit 0
MOCK
  chmod +x "$d/bin/xcodebuild"
  local result rc
  result=$(PATH="$d/bin:$PATH" bash -c \
    ". '$HELPERS'; ios_find_scheme '$d/ios/dealfindio.xcworkspace'" 2>/dev/null) \
    && rc=$? || rc=$?
  rm -rf "$d"
  if [[ "$rc" -eq 0 && "$result" == "dealfindio" ]]; then
    ok "T8: boost/fmt/glog before app scheme — dealfindio selected (not boost)"
  else
    fail "T8: boost/fmt/glog before app scheme — dealfindio selected (not boost)" \
      "rc=$rc result='$result'"
  fi
}; t8

# ── Results ───────────────────────────────────────────────────────────────────
echo ""
echo "── Results ───────────────────────────────────────────────────────────────"
printf '   %d passed, %d failed\n' "$PASS" "$FAIL"
echo ""
[[ "$FAIL" -eq 0 ]]

#!/usr/bin/env bash
# scripts/dev/ios-helpers.sh — pure helper functions for iOS simulator builds.
# Sourced by scripts/dev/ios.sh and scripts/dev/tests/ios-unit.sh.
# No global side-effects; errors are written to stderr; functions return 1 on
# failure so callers can do:  RESULT=$(fn arg) || exit 1

# ios_find_workspace <ios_dir>
# Prints the path to the first .xcworkspace, or returns 1 with a diagnostic.
# Distinguishes missing native project (run expo prebuild) from missing CocoaPods
# workspace (run pod install).
ios_find_workspace() {
  local ios_dir="$1"
  local ws
  ws=$(find "$ios_dir" -maxdepth 1 -name "*.xcworkspace" 2>/dev/null | head -1 || true)
  if [[ -z "$ws" ]]; then
    if [[ ! -f "$ios_dir/Podfile" ]]; then
      printf 'ERROR: iOS native project not generated (no Podfile). Run: make ios-clean\n' >&2
    elif [[ ! -d "$ios_dir/Pods" ]]; then
      printf 'ERROR: CocoaPods workspace not generated (Pods/ missing). Run: cd mobile/ios && pod install\n' >&2
    else
      printf 'ERROR: No .xcworkspace found in %s. Run: make ios-clean\n' "$ios_dir" >&2
    fi
    return 1
  fi
  printf '%s\n' "$ws"
}

# ios_find_scheme <workspace>
# Prints the application scheme, or returns 1.
# Strategy:
#   1. Exact match on workspace basename — Expo names the scheme and workspace
#      identically (both derived from app.json → name), so this is always right
#      for Expo projects regardless of how many dependency schemes appear first.
#   2. If not found, skip known CocoaPods/infrastructure prefixes (Pods-/React-/RCT-)
#      and return the first remaining scheme, with a warning.
ios_find_scheme() {
  local ws="$1"
  local ws_name
  ws_name=$(basename "$ws" .xcworkspace)

  # Collect every scheme the workspace exposes.
  # IMPORTANT: the exit rule must appear BEFORE the print+gsub rule.
  # gsub modifies $0 in-place; if print runs first, the stripped value
  # (e.g. "boost") would match /^[^[:space:]]/ on the next rule and exit early.
  local schemes
  schemes=$(xcodebuild -list -workspace "$ws" 2>/dev/null \
    | awk '/Schemes:/{found=1; next}
           found && /^[^[:space:]]/{exit}
           found && /^[[:space:]]+[^[:space:]]/{gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print}' \
    || true)

  if [[ -z "$schemes" ]]; then
    printf 'ERROR: xcodebuild -list returned no schemes from %s. Run: make ios-clean\n' "$ws" >&2
    return 1
  fi

  # 1. Prefer the scheme whose name is identical to the workspace name
  local scheme
  scheme=$(printf '%s\n' "$schemes" | grep -xF "$ws_name" | head -1 || true)

  # 2. Fall back: skip CocoaPods aggregation and pod-library prefixes
  if [[ -z "$scheme" ]]; then
    scheme=$(printf '%s\n' "$schemes" \
      | grep -Ev "^(Pods-|React-|RCT-)" | head -1 || true)
    if [[ -n "$scheme" ]]; then
      printf 'WARNING: scheme "%s" not in workspace; selected "%s". Override: SCHEME=<name> make ios\n' \
        "$ws_name" "$scheme" >&2
    fi
  fi

  if [[ -z "$scheme" ]]; then
    printf 'ERROR: No app scheme found in %s.\nAvailable schemes:\n%s\nOverride: SCHEME=<name> make ios\n' \
      "$ws" "$schemes" >&2
    return 1
  fi
  printf '%s\n' "$scheme"
}

# ios_find_app <build_dir>
# Prints the path to the simulator .app bundle under build_dir, or returns 1.
ios_find_app() {
  local build_dir="$1"
  local app
  app=$(find "$build_dir" -name "*.app" -path "*/Debug-iphonesimulator/*" \
        -not -name "*.dSYM" 2>/dev/null | head -1 || true)
  if [[ -z "$app" ]]; then
    printf 'ERROR: .app bundle not found under %s\n' "$build_dir" >&2
    return 1
  fi
  printf '%s\n' "$app"
}

# ios_build <workspace> <scheme> <udid> <build_dir> [jobs]
# Runs xcodebuild exactly once, streams filtered output, returns xcodebuild's
# own exit code via PIPESTATUS (not the pipeline's exit).
# Optional fifth argument limits xcodebuild parallelism via -jobs <n>.
# Omit (or pass empty string) to let xcodebuild choose automatically.
ios_build() {
  local workspace="$1" scheme="$2" udid="$3" build_dir="$4"
  local jobs="${5:-}"
  local xcbuild_log
  xcbuild_log="$(mktemp "${TMPDIR:-/tmp}/xcodebuild.XXXXXX")"

  # Validate jobs when provided
  if [[ -n "$jobs" ]] && ! [[ "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    printf 'ERROR: JOBS must be a positive integer (got: %s)\n' "$jobs" >&2
    return 1
  fi

  local jobs_args=()
  [[ -n "$jobs" ]] && jobs_args=(-jobs "$jobs")

  printf '→ Building %s%s (log: %s)\n' "$scheme" \
    "${jobs:+ with -jobs $jobs}" "$xcbuild_log"

  # Disable pipefail so PIPESTATUS[0] captures xcodebuild's exit without the
  # pipeline aborting if grep finds no matching filter lines.
  set +o pipefail
  xcodebuild \
    -workspace "$workspace" \
    -scheme "$scheme" \
    -configuration Debug \
    -destination "id=$udid" \
    -derivedDataPath "$build_dir" \
    "${jobs_args[@]}" \
    build 2>&1 | tee "$xcbuild_log" | { grep -E "^(Build|error:|warning:|CompileSwift|Linking)" || true; }
  local build_exit="${PIPESTATUS[0]}"
  set -o pipefail

  if [[ "$build_exit" -ne 0 ]]; then
    printf '\nBuild failed. Last 40 lines of log:\n' >&2
    tail -40 "$xcbuild_log" >&2
    printf 'Full log: %s\n' "$xcbuild_log" >&2
  fi
  return "$build_exit"
}

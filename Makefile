# DealFindio — root Makefile
# Usage: make <target> [DEVICE="iPhone 18 Pro"] [DEVICE_ID=<UUID>]
#
# All iOS targets delegate to scripts/dev/ios.sh which handles both
# Xcode ≤ 25 (Simulator.app) and Xcode 26+ (Device Hub).

SHELL := /usr/bin/env bash -euo pipefail
.DEFAULT_GOAL := help

# Allow callers to pass a simulator by name or UUID
DEVICE    ?=
DEVICE_ID ?=

# Limit xcodebuild parallelism to reduce heat on sustained builds.
# Leave unset to let xcodebuild use all available cores.
# Example: make ios JOBS=2
JOBS ?=

# Metro bundler port.  Override when port 8081 is occupied by another process.
# The native iOS debug build is told to connect to the same port via
# xcrun simctl spawn (writes RCT_jsLocation to the app's NSUserDefaults).
# Example: make ios METRO_PORT=8082
METRO_PORT ?= 8081

# ── Phony ─────────────────────────────────────────────────────────────────────
.PHONY: help doctor setup-mobile \
        ios ios-device ios-clean ios-test ios-unit-test \
        mobile-test mobile-typecheck mobile-lint \
        dev

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "DealFindio — development targets"
	@echo ""
	@echo "  make doctor             Check Node, Xcode, CocoaPods, simulators, backend"
	@echo "  make setup-mobile       Install locked mobile dependencies"
	@echo ""
	@echo "  make ios                Build + launch on iOS Simulator (auto-select)"
	@echo "  make ios DEVICE=\"iPhone 18 Pro\""
	@echo "  make ios DEVICE_ID=<UUID>"
	@echo "  make ios JOBS=2         Limit xcodebuild to 2 parallel compile jobs"
	@echo "  make ios METRO_PORT=8082 Use Metro on port 8082 (when 8081 is occupied)"
	@echo "  make ios-device         Interactive simulator selection"
	@echo "  make ios-clean          Regenerate iOS native project and Pods from scratch"
	@echo ""
	@echo "  make ios-test           Run simulator smoke tests (requires booted simulator)"
	@echo "  make ios-unit-test      Run ios-helpers unit tests (no Xcode required)"
	@echo "  make mobile-test        Run Jest test suite"
	@echo "  make mobile-typecheck   TypeScript validation"
	@echo "  make mobile-lint        ESLint"
	@echo ""
	@echo "  make dev                Start all backend + web services (Terminal tabs)"
	@echo ""

# ── Environment doctor ────────────────────────────────────────────────────────
doctor:
	@bash scripts/dev/doctor.sh

# ── Mobile dependencies ───────────────────────────────────────────────────────
setup-mobile:
	@echo "→ Installing mobile dependencies..."
	cd mobile && npm ci
	@echo "✔ mobile/node_modules ready"

# ── iOS build + launch ────────────────────────────────────────────────────────
ios:
	@DEVICE="$(DEVICE)" DEVICE_ID="$(DEVICE_ID)" JOBS="$(JOBS)" METRO_PORT="$(METRO_PORT)" bash scripts/dev/ios.sh

# Interactive device selection (prompts user)
ios-device:
	@echo "Available iPhone simulators:"
	@xcrun simctl list devices available | grep "iPhone" | grep -v "unavailable" | \
	  awk '{printf "  %s\n", $$0}'
	@echo ""
	@read -r -p "Enter UDID or name fragment: " SEL; \
	  if echo "$$SEL" | grep -qE '[A-F0-9]{8}-'; then \
	    DEVICE_ID="$$SEL" bash scripts/dev/ios.sh; \
	  else \
	    DEVICE="$$SEL" bash scripts/dev/ios.sh; \
	  fi

# Clean + full regenerate (expo prebuild --clean + pod install)
ios-clean:
	@echo "→ Cleaning iOS native project..."
	cd mobile && npx expo prebuild --platform ios --clean --non-interactive
	@echo "→ Running pod install..."
	cd mobile/ios && pod install
	@echo "✔ iOS native project regenerated"
	@echo ""
	@echo "Auditing deployment targets..."
	@PBXPROJ="mobile/ios/Pods/Pods.xcodeproj/project.pbxproj"; \
	  LOW=$$(grep -o "IPHONEOS_DEPLOYMENT_TARGET = [^;]*" "$$PBXPROJ" 2>/dev/null \
	    | awk -F' = ' '{v=$$2; if(v+0 < 15) print v}' | sort -u || true); \
	  if [[ -z "$$LOW" ]]; then \
	    echo "✔ All pod targets use deployment target ≥ 15.0"; \
	  else \
	    echo "✘ FAIL: pod targets with deployment target < 15.0: $$LOW"; exit 1; \
	  fi

# ── iOS smoke tests ───────────────────────────────────────────────────────────
ios-test:
	@DEVICE_ID="$(DEVICE_ID)" bash scripts/dev/ios-test.sh

ios-unit-test:
	@bash scripts/dev/tests/ios-unit.sh

# ── Mobile JS/TS checks ───────────────────────────────────────────────────────
mobile-test:
	@echo "→ Running Jest..."
	cd mobile && npx jest --passWithNoTests

mobile-typecheck:
	@echo "→ Running TypeScript..."
	cd mobile && npx tsc --noEmit

mobile-lint:
	@echo "→ Running ESLint..."
	cd mobile && npx eslint . --max-warnings 0

# ── Backend + web services ────────────────────────────────────────────────────
dev:
	@./dev.sh

#!/usr/bin/env bash
# dev.sh — start all dealfindio services in separate Terminal tabs
# Usage: ./dev.sh [--no-beat] [--no-web]

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

run_tab() {
  local title="$1"
  local cmd="$2"
  osascript \
    -e 'tell application "Terminal"' \
    -e "  tell application \"System Events\" to keystroke \"t\" using command down" \
    -e "  delay 0.3" \
    -e "  do script \"printf '\\\\e]0;$title\\\\a'; $cmd\" in front window" \
    -e 'end tell'
}

echo "▶ Starting dealfindio dev environment…"

# 1. FastAPI
run_tab "api" "cd '$ROOT/backend' && poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8010"

sleep 0.5

# 2. Celery worker (scraper + default queues)
run_tab "worker" "cd '$ROOT/backend' && poetry run celery -A app.workers.celery_app worker -Q scraper,catalog,celery --loglevel=info -c 2"

sleep 0.5

# 3. Celery beat scheduler (optional, skip with --no-beat)
if [[ ! " $* " =~ " --no-beat " ]]; then
  run_tab "beat" "cd '$ROOT/backend' && poetry run celery -A app.workers.celery_app beat --loglevel=info"
  sleep 0.5
fi

# 4. Vite dev server (optional, skip with --no-web)
if [[ ! " $* " =~ " --no-web " ]]; then
  run_tab "web" "cd '$ROOT/web' && npm run dev"
fi

echo ""
echo "✓ All services starting in Terminal tabs."
echo "  API    → http://localhost:8010"
echo "  Docs   → http://localhost:8010/docs"
echo "  Web    → http://localhost:5173"
echo "  Admin  → http://localhost:5173/admin"
echo ""
echo "Stop everything: close the Terminal tabs, or kill the processes."

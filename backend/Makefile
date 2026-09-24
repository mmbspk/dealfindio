.PHONY: help install lint format type-check test test-cov dev dev-stop seed migrate-create migrate-upgrade clean

help:
	@echo "dealfindio backend - Available commands:"
	@echo "  make install          - Install dependencies with poetry"
	@echo "  make lint             - Run ruff linter"
	@echo "  make format           - Format code with black & ruff"
	@echo "  make type-check       - Run pyright type checker"
	@echo "  make test             - Run pytest"
	@echo "  make test-cov         - Run pytest with coverage report"
	@echo "  make dev              - Start development environment (PostgreSQL + Redis)"
	@echo "  make dev-stop         - Stop development environment"
	@echo "  make dev-logs         - View logs from dev containers"
	@echo "  make dev-reset        - Reset dev database (WARNING: deletes data)"
	@echo "  make seed             - Run database seeding script"
	@echo "  make migrate-create   - Create new Alembic migration"
	@echo "  make migrate-upgrade  - Run pending migrations"
	@echo "  make migrate-downgrade - Downgrade one migration"
	@echo "  make clean            - Remove __pycache__ and .pytest_cache"

install:
	poetry install

lint:
	ruff check .

format:
	black . && ruff check . --fix

type-check:
	pyright

test:
	pytest -v

test-cov:
	pytest --cov=app --cov-report=html --cov-report=term-missing

dev:
	docker-compose -f ../docker-compose.dev.yml up -d
	@echo ""
	@echo "╔════════════════════════════════════════════════════════════╗"
	@echo "║           dealfindio Development Environment Ready         ║"
	@echo "╠════════════════════════════════════════════════════════════╣"
	@echo "║  ✓ PostgreSQL:  localhost:5432 (user: dealfindio)          ║"
	@echo "║  ✓ Redis:       localhost:6379                             ║"
	@echo "║  ✓ API:         http://localhost:8000                      ║"
	@echo "║  ✓ Swagger:     http://localhost:8000/docs                 ║"
	@echo "║  ✓ Web:         http://localhost:5173                      ║"
	@echo "║  ✓ Celery:      Running in background                      ║"
	@echo "║                                                            ║"
	@echo "║  View logs:     make dev-logs                              ║"
	@echo "║  Stop all:      make dev-stop                              ║"
	@echo "║  Reset DB:      make dev-reset                             ║"
	@echo "╚════════════════════════════════════════════════════════════╝"
	@echo ""

dev-stop:
	docker-compose -f ../docker-compose.dev.yml down

dev-logs:
	docker-compose -f ../docker-compose.dev.yml logs -f

dev-logs-api:
	docker-compose -f ../docker-compose.dev.yml logs -f api

dev-logs-worker:
	docker-compose -f ../docker-compose.dev.yml logs -f celery_worker

dev-logs-beat:
	docker-compose -f ../docker-compose.dev.yml logs -f celery_beat

dev-logs-web:
	docker-compose -f ../docker-compose.dev.yml logs -f web

dev-reset:
	@echo "WARNING: This will delete all development database data!"
	@read -p "Continue? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	docker-compose -f ../docker-compose.dev.yml down -v
	docker-compose -f ../docker-compose.dev.yml up -d
	@echo "✓ Development environment reset"

seed:
	python -m app.db.seed_stores

migrate-create:
	@read -p "Migration name: " name; \
	alembic revision --autogenerate -m "$$name"

migrate-upgrade:
	alembic upgrade head

migrate-downgrade:
	alembic downgrade -1

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name .coverage -delete
	find . -name htmlcov -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ Cleaned up cache files"

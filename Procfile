api:     cd backend && poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
worker:  cd backend && poetry run celery -A app.workers.celery_app worker -Q scraper,catalog,celery --loglevel=info -c 2
beat:    cd backend && poetry run celery -A app.workers.celery_app beat --loglevel=info
web:     cd web && npm run dev

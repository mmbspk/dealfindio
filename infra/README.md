# Infra

Railway deployment configuration and cloud setup.

## Railway Deployment

`railway.toml` defines the deployment pipeline for dealfindio.

### Services

- **Backend:** FastAPI app running on Railway container
- **PostgreSQL:** Managed PostgreSQL 16 database
- **Redis:** In-memory cache and Celery broker

### Deployment

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login to Railway
railway login

# Link project
railway link

# Deploy
git push origin main  # Triggers automatic deployment via CI/CD
```

### Environment Variables

Set in Railway dashboard:
- DATABASE_URL (auto-set by Railway)
- REDIS_URL (auto-set by Railway)
- FIREBASE_PROJECT_ID
- FIREBASE_SERVICE_ACCOUNT_JSON
- ANTHROPIC_API_KEY
- SENTRY_DSN

### Database Migrations

Migrations run automatically on deploy via the `startCommand` in railway.toml.

### Monitoring

- Health checks: `/health` endpoint
- Logs: Railway dashboard
- Errors: Sentry integration (if SENTRY_DSN set)
- Metrics: Railway metrics dashboard

## Local Production-Like Testing

```bash
docker-compose -f docker-compose.prod.yml up
```

## Scaling

Railway auto-scales based on CPU/memory usage. Configure in dashboard.

# EquityKobo Coolify Deployment

This setup deploys EquityKobo as three production concerns:

- `equitykobo-api`: FastAPI backend that serves the app.
- `equitykobo-worker`: one background worker that runs the full NGX research sync.
- `equitykobo-web`: Vite frontend served by Nginx from the separate frontend repo.

The backend Compose file intentionally disables the API in-app scheduler. In production, only the worker runs scheduled jobs. That avoids duplicate NGX Pulse requests if the API restarts, reloads, or is scaled later.

## Backend

Use the backend repo with:

```text
docker-compose.coolify.yml
Dockerfile
scripts/worker-loop.sh
```

The backend image installs the Python package, exposes port `8000`, writes uploaded reports to `/app/data/uploads`, and includes a healthcheck at `/health`.

### Required Env

Set these on the Coolify backend app:

```env
DATABASE_URL=postgres://USER:PASSWORD@HOST:PORT/DB_NAME
CORS_ORIGINS=https://your-frontend-domain.com
NGXPULSE_API_KEY=your_ngxpulse_key
```

Recommended production values:

```env
APP_NAME=EquityKobo
UPLOAD_DIR=/app/data/uploads
NGXPULSE_BASE_URL=https://www.ngxpulse.ng
NGXPULSE_REQUEST_PAUSE_SECONDS=3.2
DEEPSEEK_API_KEY=your_deepseek_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
AUTOMATION_DIVIDEND_SYNC_ENABLED=true
SYNC_INTERVAL_SECONDS=86400
SYNC_STARTUP_SLEEP_SECONDS=30
```

`DATABASE_URL` can use `postgres://...`; the backend converts it to the SQLAlchemy `postgresql+psycopg://...` driver internally.

## Production Automation

The worker runs:

```bash
equitykobo-sync full-market
```

It repeats every `SYNC_INTERVAL_SECONDS`.

Current full-market pipeline:

```text
1. sync NGX stocks and latest price data
2. sync NGX fundamentals
3. sync NGX disclosures
4. sync NGX indices
5. sync NGX ETFs
6. sync NGX bonds
7. sync bond auctions
8. sync NASD OTC stocks
9. sync market news
10. sync dividend history for active companies
11. run market scan
12. generate intelligence snapshots
13. generate valuation snapshots
14. generate peer comparison snapshots
15. evaluate alerts
```

Dividend history runs before intelligence, valuation, and peer comparison, so the same production run refreshes dividend candidates, dividend view, decision cards, fair-value support, and opportunity rankings.

With NGX Pulse Starter, keep the worker at once daily unless you confirm the request count in logs. Full-market sync uses one request for each bulk endpoint plus one dividend-history request per active company.

## Manual Checks

After the first deployment, run these from the backend container terminal:

```bash
equitykobo-sync full-market
```

Then verify:

```bash
curl https://your-api-domain.com/health
curl https://your-api-domain.com/intelligence/opportunities
curl https://your-api-domain.com/decision-dashboard
```

To inspect production automation:

```bash
curl https://your-api-domain.com/automation/status
```

In this Compose setup it should show `enabled=false` for the API scheduler. That is expected because the worker container owns scheduled jobs.

## Frontend

The frontend is a separate repo. Use:

```text
deploy/frontend.Dockerfile
deploy/nginx.conf
deploy/frontend-compose.coolify.yml
```

Set the frontend build variable:

```env
VITE_API_BASE_URL=https://your-api-domain.com
```

Also make sure backend `CORS_ORIGINS` contains the final frontend domain:

```env
CORS_ORIGINS=https://your-frontend-domain.com
```

The frontend container serves the built Vite app with SPA fallback, so routes such as `/company/ZENITHBANK`, `/watchlists`, and `/portfolio` work after browser refresh.

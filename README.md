# Vendor Compliance Monitoring System

Onboards UK companies as vendors, screens them (Companies House, UK Sanctions
List, financial filings), scores their risk, and keeps watching them: new
alerts, review decisions and an immutable audit trail surface in a dashboard,
while n8n handles scheduling and e-mail notifications.

> Automated screening is an aid, not a verdict. Potential sanctions or
> adverse-media matches must be reviewed by a human before any adverse action.

## Architecture

| Component | Path | Role |
|---|---|---|
| **API** | `python-service/` | FastAPI backend: evaluation, scoring, alerts, reports, audit log, dashboard session API. Postgres via SQLAlchemy + Alembic. |
| **Sanctions service** | `sanctions-service/` | Caches the official UK Sanctions List XML and serves fuzzy-match screening to the API. |
| **Dashboard** | `vendor-dashboard/` | React + Vite single-page app. Talks only to the API (shared-password session). |
| **Automation** | `n8n/workflows/` | Intake form, daily monitoring, alert delivery, digests, bulk upload, job worker, failure alerts. |

```
 n8n ──x-internal-api-key──▶ API (:8000) ◀──session cookie── Dashboard
                              │   │
                              │   └──▶ sanctions-service (:8001, internal)
                              └──▶ Postgres · Companies House · Gemini
```

## Repository layout

```
python-service/      API, scoring, adapters, Alembic migrations, tests
sanctions-service/   UK Sanctions List microservice
vendor-dashboard/    React/Vite dashboard (+ its own README, tests)
n8n/workflows/       Sanitised n8n workflow exports  (see n8n/README.md)
scripts/             Helper scripts (n8n export sanitiser)
docker-compose.yml   n8n + API + sanctions service
```

## Quick start

Requires Docker with Compose, and Node.js 22.12+ for the dashboard.

```bash
# 1. Configure the backend (never commit the real .env)
cp python-service/.env.example python-service/.env
#    fill in the required values and generate real secrets (see SECURITY.md)

# 2. Start n8n, the API and the sanctions service
docker compose up -d --build

# 3. Apply database migrations (not run automatically)
docker compose exec python-service alembic upgrade head

# 4. Run the dashboard
cd vendor-dashboard
npm ci
npm run dev        # http://127.0.0.1:5173 — sign in with DASHBOARD_PASSWORD
```

- API health: <http://localhost:8000/>
- n8n editor: <http://localhost:5679/> — then follow [`n8n/README.md`](n8n/README.md) to import the workflows.

## Tests

```bash
# Backend (unittest)
docker compose exec python-service python -m unittest discover -s tests -v

# Dashboard
cd vendor-dashboard && npm test
```

CI (`.github/workflows/ci.yml`) runs both plus a build, on every push and pull request.

## Configuration

All backend settings are environment variables documented in
[`python-service/.env.example`](python-service/.env.example). The dashboard's
optional, **public** settings are in
[`vendor-dashboard/.env.example`](vendor-dashboard/.env.example). Never put
secrets in `VITE_*` variables: they are embedded in the browser bundle.

## Data sources and terms

The system calls the Companies House API, the UK Sanctions List, Google News RSS
and the Gemini API. Check each provider's terms of use (including rate limits
and permitted use) before commercial deployment.

## Security

See [`SECURITY.md`](SECURITY.md) for vulnerability reporting, the list of files
that must never be committed, and a deployment hardening checklist.

## License

All rights reserved. See [`LICENSE`](LICENSE).

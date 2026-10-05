# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private reporting: **Security → Report a vulnerability** on this
repository. Include the affected component, steps to reproduce, and the impact
you observed. You will get an acknowledgement as soon as the maintainer can
respond; please allow reasonable time for a fix before any public disclosure.

## Scope

This repository contains a compliance-monitoring system that handles
supplier/company data and calls third-party APIs. In scope: the FastAPI backend
(`python-service`), the sanctions service, the dashboard, and the n8n workflows
in `n8n/workflows`.

## Secrets: what must never be committed

- `python-service/.env`, `vendor-dashboard/.env*` (other than `.env.example`)
- `n8n_data/` — contains n8n's **encryption key** and its database of stored
  credentials. Anyone holding both can decrypt your SMTP password and API keys.
- Raw n8n exports (`n8n/raw/`) — sanitise with
  `python scripts/sanitize_n8n_export.py` before committing.
- Anything under `evidence/`, `sanctions-data/`, or `python-service/data/`.

`.gitignore` and `.dockerignore` enforce this, but check `git status` before
every push. If a secret is ever committed, **rotate it first**, then clean up;
deleting the file does not remove it from git history.

## Deployment hardening checklist

- [ ] `INTERNAL_API_KEY` is set (if empty, internal endpoints are unauthenticated).
- [ ] `DASHBOARD_PASSWORD` and a long random `DASHBOARD_SESSION_SECRET` are set.
- [ ] `DASHBOARD_COOKIE_SECURE=true` and the dashboard is served over HTTPS.
- [ ] `ALLOWED_ORIGINS` lists only your real dashboard origin(s).
- [ ] n8n uses credentials from its credential store (never keys pasted into nodes).
- [ ] Database role used by the app cannot `UPDATE`/`DELETE` audit-log rows.
- [ ] Never put secrets in `VITE_*` variables: Vite embeds them in the public JS bundle.

## Automated screening disclaimer

Sanctions, adverse-media, and financial signals are automated screening aids.
Potential matches require human review before any adverse business decision.

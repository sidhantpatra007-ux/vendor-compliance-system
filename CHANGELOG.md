# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Alembic-managed schema with six migrations: event foundation, risk
  intelligence and reporting, alert operations, final backend operations, bulk
  onboarding and automation jobs, dashboard orchestration.
- Source adapters (`python-service/source_adapters/`) feeding a normalised event store.
- New React/Vite dashboard (`vendor-dashboard/`) using the password-protected
  backend routes; no direct database access from the browser.
- n8n workflows (`n8n/workflows/`): onboarding intake and daily monitoring,
  dashboard interactives, dashboard action worker, failure/error handling.
- Repository hygiene: `.gitignore`, `.gitattributes`, `.editorconfig`,
  `.dockerignore`, `.env.example`, CI workflow, security policy.

### Changed
- Legacy TypeScript dashboard replaced by the new JSX dashboard.
- Postgres (Supabase) replaces the earlier SQLite persistence layer.

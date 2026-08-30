# Vendor Compliance & Risk Monitoring System — Production Upgrade Spec

You are being handed an existing, working vendor compliance/onboarding/monitoring system. Your job is to extend it to production-grade without breaking what already works. Read this entire spec before writing any code. Do not restyle, rename, or restructure existing files beyond what's explicitly requested here.

## 1. Existing system — read this before touching anything

**Frontend**: React + Vite + TypeScript + Tailwind + Recharts + lucide-react + @tanstack/react-query + react-router-dom. Key files:
- `src/api.ts` — single `dashboard` object wrapping all fetch calls
- `src/types.ts` — shared TS interfaces
- `src/hooks.ts` — react-query hooks, 90-second poll interval
- `src/pages.tsx` — page components (Overview, Vendors, VendorDetail, Alerts, Audit, Login)
- `src/components.tsx` — shared UI primitives (Grade, Severity, IconMetric, Empty, Loading, ErrorCard)
- `src/Layout.tsx` — sidebar nav + header shell

**Backend**: FastAPI service at `python-service:8000` (exact repo not provided — infer route signatures from the frontend contract below and confirm against the actual backend code before implementing).

**Current API contract** (do not break these):
```
GET  /dashboard/auth/status
POST /dashboard/auth/login          { password }
POST /dashboard/auth/logout
GET  /dashboard/vendors             -> Vendor[]
GET  /dashboard/vendors/{id}        -> Detail
GET  /dashboard/alerts              -> Alert[]
GET  /dashboard/audit-logs?limit=100 -> AuditEvent[]
POST /dashboard/vendors/{id}/refresh
POST /dashboard/alerts/{id}/status  body: Record<string, unknown>
POST /dashboard/vendors/{id}/review-decisions  body: Record<string, unknown>
```

**Current types already in place** (extend, don't duplicate):
```ts
Vendor { vendor_id, name, company_number, latest_score, latest_grade, last_checked, recommend_manual_review, data_confidence }
Alert { alert_id, vendor_id, severity, status, title, reason, evidence?, assigned_to, acknowledged_at?, resolved_at?, sla_due_at, created_at }
AuditEvent { id, action, actor, vendor_id?, alert_id?, details, created_at }
Evidence { evidence_id, concept, state, extraction_method, page, captured_at, image_url, companies_house_filing_history_url }
Decision { decision_id, decision, reviewer, note, next_review_at, created_at }
Detail { vendor_id, vendor_name, company_number, intake, latest{...}, score_history[], financial_evidence[], review_decisions[], alerts[], audit_events[] }
```
Note: `AuditEvent`, `Alert.sla_due_at/acknowledged_at/resolved_at`, and `Decision.next_review_at` already exist. Build on top of these — do not create parallel/duplicate concepts.

**n8n workflow** (exported as `vendor_compliance_checker.json`): a nightly `Schedule Trigger` calls `/refresh-all`, splits out vendors with new alerts, fetches vendor detail, builds a Google News RSS query per vendor, and emails a "Vendor Change Review" report. Separately, a `New Vendor Due-Diligence Intake` form feeds a `Classify Onboarding Risk` code node that routes to Blocked / Enhanced Review / Standard, each branch ending in an email with no persistence back to the backend.

## 2. Objective

Close the gap between "computes good signals" and "persists, tiers, and tracks them as auditable, actionable data." Every addition below should either (a) make data durable that currently only exists transiently in an email or a single computation, (b) add risk-tiering so monitoring/alerting isn't one-size-fits-all, or (c) close the loop on human review so decisions feed back into the system instead of dead-ending.

## 3. Database changes

Confirm the actual DB engine (Postgres/Supabase based on context) before writing migrations. Required additions:

- **`risk_tier` column** on the vendor table: enum `critical | high | medium | low`, distinct from the existing `latest_grade` (A–E). Grade reflects computed risk score; tier reflects business criticality (data access, subcontractor use, regulatory exposure, contract value) and drives monitoring frequency. Backfill logic: derive an initial tier from existing `intake` fields (`processes_personal_data`, `access_to_client_systems_or_data`, `uses_subcontractors`) if present.
- **`next_review_due` column** on the vendor table (date). Set on onboarding, recalculated on each formal reassessment. Suggested default cadence: critical = 12 months + continuous monitoring, high = 18 months, medium/low = 24 months or event-driven — confirm exact cadence with the project owner before hardcoding.
- **`evidence` table** (new — separate from the existing `financial_evidence`/`Evidence` concept): `id, vendor_id, source (news|sanctions|companies_house|other), type, payload_json, retrieved_at`. Purpose: persist the Google News RSS results and any sanctions-match output that n8n currently only emails, so they're queryable per vendor instead of disappearing after the email sends.
- **`remediation_tasks` table** (new): `id, alert_id, vendor_id, owner, status (open|in_progress|resolved), due_date, resolution_notes, created_at, updated_at`. Purpose: alerts should become tracked work items with an owner and due date, not a flat list.
- **`review_requests` table** (new, or extend `review_decisions` if the schema allows a `status` of `pending`): `id, vendor_id, reasons_json, assigned_reviewer, status (pending|approved|rejected), created_at, decided_at`. Purpose: the "Enhanced Review Required" onboarding branch needs a durable record the moment it fires, not just at the point a decision is made.
- **Audit log integrity**: confirm the existing `audit_events`/`AuditEvent` table has no `UPDATE`/`DELETE` grants for the application's runtime DB role — timestamps must be system-assigned at insert time, and no role (including admin) should be able to retroactively edit a row. If this isn't already true, add a migration that revokes those grants and, if using Postgres, add a trigger that rejects `UPDATE`/`DELETE` on that table outright.
- **`offboarding` support**: add a `status` value (`active|offboarding|terminated`) on the vendor table plus an `offboarded_at`/`offboarding_reason` pair. There is currently no way to formally end a vendor relationship in the system.

## 4. Backend changes (FastAPI)

- `POST /dashboard/vendors/{id}/evidence` — accepts `{source, type, payload}`, used by n8n to persist news/adverse-media results instead of only emailing them.
- `POST /dashboard/vendors/{id}/review-requests` — creates a pending review request with reasons; called by n8n the moment the "Needs Enhanced Review?" branch fires.
- Modify `POST /dashboard/vendors/{id}/review-decisions` — after saving the decision, also fire an outbound HTTP POST to an n8n webhook URL (configurable via env var) with `{vendor_id, decision, reasons}`, so n8n can resume and send the vendor-facing approval/rejection email. This is the connective piece that closes the loop between "reviewer clicks Approve/Reject on the dashboard" and "vendor gets notified."
- `POST /dashboard/vendors/{id}/offboard` — sets status to `offboarding`/`terminated`, records reason, timestamps it.
- `PATCH /dashboard/alerts/{id}/remediation` — sets owner/status/due_date/resolution_notes on a remediation task tied to an alert. Extend rather than replace the existing `/dashboard/alerts/{id}/status` endpoint if that already covers part of this.
- `GET /dashboard/vendors?tier=critical` — allow filtering the existing vendors list by `risk_tier`.
- Alert deduplication: before inserting a new alert row, check for an existing open alert on the same `vendor_id` + same underlying issue signature (e.g. same `title`/category) and update it instead of creating a duplicate. Apply this at the endpoint the nightly refresh calls into, not in n8n.
- Tier-aware refresh scheduling: if `/refresh-all` currently checks every vendor uniformly, add a parameter or separate endpoint (`/refresh-all?tier=critical`) so n8n can call it at different frequencies per tier.

## 5. Frontend changes

All new UI should reuse the existing `.panel`, `.metric-card`, `.data-num`, `Grade`, `Severity`, `Empty`, `Loading`, `ErrorCard` primitives already in `components.tsx` and `index.css` — do not introduce a second design system.

- **Vendor list/register**: add filter controls (by `risk_tier`, `latest_grade`, review status) and sort. Add a risk-tier badge component analogous to the existing `Grade` component but using tier semantics, not grade colors.
- **Vendor detail page**: add an "Evidence" tab surfacing the new `evidence` table contents (news hits, sanctions matches) chronologically. Add a "Next review due" field near the existing grade/score display.
- **Remediation task board**: new page or section under Alerts — list `remediation_tasks` grouped by status (open/in progress/resolved) with owner and due date visible, not just a flat alert list.
- **Onboarding/review queue page**: new page listing `review_requests` with status `pending`, showing the reasons array, with Approve/Reject actions that call the existing `dashboard.decision()` — add a free-text field for the reviewer to edit/soften the rejection reason before it's sent to the vendor, rather than forwarding raw internal reason strings verbatim.
- **Audit trail viewer**: ensure the existing Audit page is filterable by vendor/actor/date range, and make clear in the UI (e.g. a small badge or note) that entries are immutable, to build trust with a client reviewing the product.
- **Reassessment calendar/queue**: a simple list or calendar view of vendors sorted by `next_review_due`, surfaced on the Overview page as a widget (upcoming due items) as well as its own page.
- Update `src/types.ts` and `src/api.ts` to add the corresponding TS interfaces and `dashboard.*` methods for every new endpoint above, following the existing single-line style already used in those files.
- Update `Layout.tsx` nav array to add entries for any new top-level pages (Review Queue, Remediation Tasks) if they don't fit naturally as tabs on existing pages.

## 6. n8n workflow changes

- Add an `HTTP Request` node right after "Enhanced Review Request" email, POSTing to the new `/vendors/{id}/review-requests` endpoint.
- Add a `Webhook` trigger node (new workflow or new trigger in the existing one) that fires when the backend's `/review-decisions` endpoint calls back. Branch on `decision == rejected` vs `approved`:
  - Rejected → `Email Send` to the vendor's own contact email (confirm the intake form actually captures this field) with the reviewer-edited reason and the client's contact info for follow-up.
  - Approved → `Email Send` welcome/next-steps to the vendor + `HTTP Request` to activate monitoring.
- Add an `HTTP Request` node after the existing RSS/news correlation step, POSTing results to the new `/vendors/{id}/evidence` endpoint, in addition to (not instead of) the existing email.
- Split the single nightly `Schedule Trigger` into tiered schedules (e.g. critical vendors every few hours, standard vendors nightly), each calling `/refresh-all?tier=...`.
- Add a new scheduled workflow (e.g. daily) that queries vendors where `next_review_due` is within N days and emails a reassessment reminder to the internal team.
- Add an offboarding workflow: manual trigger or form → `HTTP Request` to `/vendors/{id}/offboard` → final evidence/audit snapshot → vendor notification email.
- **Security fix required regardless of the above**: the existing workflow has an internal API key hardcoded in plaintext across multiple `HTTP Request` node parameters. Move it into n8n's credential store (HTTP Header Auth credential type) and rotate the key on the backend, since it has already been exposed in an exported JSON file outside n8n.

## 7. Non-functional requirements

- Every new backend endpoint must require the same authentication already enforced on `/dashboard/*` routes.
- Do not remove or rename any existing field on `Vendor`, `Alert`, `AuditEvent`, `Evidence`, `Decision`, or `Detail` — only add.
- Every new table needs `created_at` at minimum; audit-relevant tables (`evidence`, `remediation_tasks`, `review_requests`) should also track who/what triggered the row (`actor` or `source` field).
- The audit log's append-only guarantee must be enforced at the database permission level, not only in application code — a bug in the backend should not be able to silently violate it.
- Confirm before hardcoding: exact reassessment cadence per tier, and which intake fields determine initial `risk_tier` — these should come from the project owner's actual policy, not be invented.

## 8. Suggested implementation order

1. Database migrations (Section 3) — everything else depends on these existing first.
2. Backend endpoints (Section 4).
3. n8n `HTTP Request` nodes that persist data to the new endpoints (Section 6, evidence + review-requests first — these are additive and low-risk).
4. Frontend read-only views of the new data (evidence tab, review queue list, remediation board) — ship visibility before ship interactivity.
5. Frontend write actions (approve/reject, remediation status updates) wired to the backend endpoints.
6. n8n webhook loop-closing (review decision → vendor email) and tiered scheduling — these depend on both backend and frontend being in place.
7. Offboarding flow last — it's the least urgent and touches every layer.

Confirm each layer builds/type-checks/deploys cleanly before moving to the next. Do not attempt all seven steps in a single pass.

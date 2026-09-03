# Vendor Compliance & Risk Monitoring System — Production Upgrade Spec

Existing, working vendor compliance/onboarding/monitoring system. This document
is the single source of truth for what's planned vs already built. It replaces
all earlier versions of this file — don't reconstruct priorities from an older
copy sitting in git history or a chat transcript. Read it fully before writing
code. Do not restyle, rename, or restructure existing files beyond what's
explicitly requested here.

## 0. Scope question to answer before building Section 5

**Is any actual client FCA-regulated, doing "material outsourcing" under
SYSC 8?** This determines whether the governance layer in Section 5
(review_requests, offboarding workflow, reassessment cadence) is a near-term
requirement or genuinely deferrable. If unregulated SMB — defer it. If
FCA-regulated — it's closer to a regulatory expectation and should move up.
Don't guess this. Ask.

## 1. Existing system — read this before touching anything

**Frontend**: React + Vite + TypeScript + Tailwind + Recharts + lucide-react +
@tanstack/react-query + react-router-dom. Key files:
- `src/api.ts` — single `dashboard` object wrapping all fetch calls
- `src/types.ts` — shared TS interfaces
- `src/hooks.ts` — react-query hooks, 90-second poll interval
- `src/pages.tsx` — page components (Overview, Vendors, VendorDetail, Alerts, Audit, Login)
- `src/components.tsx` — shared UI primitives (Grade, Severity, IconMetric, Empty, Loading, ErrorCard)
- `src/Layout.tsx` — sidebar nav + header shell

**Backend**: FastAPI service at `python-service:8000` (`main.py`, SQLAlchemy
models in `models.py`, Postgres/Supabase via `DATABASE_URL`).

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

**Backend financial extraction pipeline** (`companies_house/xbrl.py` →
`pdf_extract.py` → `ocr_extract.py`, in that fallback order): XBRL first,
PDF-text second, scanned-image OCR (pytesseract) last. `amount_parse.py` is
shared by the PDF and OCR tiers for currency/locale-aware number parsing.

## 2. Objective

Close the gap between "computes good signals" and "persists, tiers, tracks,
and surfaces them as auditable, client-facing value." Sequence work toward
what a client actually notices and would pay for, not toward enterprise TPRM
governance ceremony that doesn't match this product's current stage.

## 3. Decided — do not re-litigate

These were evaluated and closed. Re-opening them without new evidence wastes
a session.

- **LLM provider for `compliance/summarize.py` stays Gemini.** The idea of
  switching to ChatGPT/OpenAI "for cost" was based on a false premise:
  ChatGPT's free/unlimited tier applies to the consumer chat app, not the
  OpenAI API — which is what `summarize.py` would call, and which bills
  per-token exactly like Gemini's API does. Gemini also already has a
  comparable free tier (Google AI Studio, Flash models, no card required).
  This specific call (~300 input / ~150 output tokens, one summary per score
  change) costs a fraction of a cent per call on Gemini 2.5 Flash's paid
  rate regardless — not a meaningful cost lever either direction at this
  project's volume. `GEMINI_API_KEY` and `summarize.py` are unchanged.
  Only reopen this if there's an actual observed bill, a real model-quality
  complaint about the summaries, or a concrete non-cost reason.

## 4. In progress / needs validation

- **`amount_parse.py::parse_amounts_in_line` — bare-year fix applied, not yet
  validated against a real filing.** The bug: a concept line like "Net
  assets 2024 2023" (a duplicated table header) or one with a nearby "for
  the year ended 31 December 2024" would return the year as the extracted
  value, because the regex had no concept of "this looks like a year, not
  money." Fix: real amount-shaped tokens (currency symbol, or a
  comma/decimal thousands separator) now sort ahead of bare 4-digit
  1900–2099 tokens on the same line, so `amounts[0]` — which
  `pdf_extract.py` and `ocr_extract.py` both consume as the primary
  candidate — is the real figure when both appear on one line. The bare
  year is never *discarded*, only deprioritized, so it's still returned
  (and still flagged low-confidence via the existing `needs_manual_review`
  path) if it's genuinely the only number on the line. **Not yet tested
  against a real Companies House scan** — validate before trusting
  extracted values in production, and don't treat this as "fixed and
  closed" until it's been run against actual messy filings.

- **OCR engine replacement — researched, not decided, not built.** Current
  `pytesseract`-based `ocr_extract.py` has no table/layout awareness at
  all — it treats a page as flat lines of text, which is the structural
  reason it can confuse a header cell for a data cell in the first place
  (the bare-year fix above is a mitigation, not a cure). Two candidates
  worth prototyping against real filings before picking one:
  - **PaddleOCR (PP-OCRv6 / PP-StructureV2)** — mature, table-structure-aware
    (`PP-Structure` identifies actual table cells, not just lines of text),
    runs on CPU, meaningfully heavier install than pytesseract
    (`paddleocr` + `paddlepaddle`, Dockerfile changes). This is the safer
    default recommendation.
  - **GLM-OCR (0.9B, GGUF)** — new in 2026, specifically claimed strong on
    financial-document tables and small enough to run on a laptop CPU
    (2–4GB RAM). Less battle-tested; claims are from third-party 2026
    benchmarks, not independently verified against this project's actual
    scanned filings.
  Do not build a new extraction tier around either without first running
  both against a handful of real scanned accounts and comparing actual
  output, not benchmark claims.

## 5. Backend/DB — reprioritized toward client-visible value

Original ordering here (tiering and governance first) was enterprise-TPRM
sequencing that doesn't match an early-stage product's actual sales pitch.
Reordered based on what SMB/startup vendor-compliance buyers actually cite
as value versus what's structural scaffolding underneath it.

### Priority 0 — Security fixes (do regardless of anything else)
- `main.py::require_api_key` compares `x_internal_api_key != INTERNAL_API_KEY`
  with a plain `!=` — timing side-channel on the internal API key. Replace
  with `hmac.compare_digest(x_internal_api_key or "", INTERNAL_API_KEY)`,
  matching what `dashboard_login` already does correctly two functions away.
- `config.py`'s `DASHBOARD_SESSION_SECRET` fallback chain ends in the
  hardcoded string `"local-dashboard-change-me"`. If the env var is unset,
  the app should refuse to start with sessions enabled, not silently sign
  cookies with a public string.
- n8n has the internal API key hardcoded in plaintext across multiple HTTP
  Request node parameters, already exposed via an exported JSON file. Move
  to an n8n credential (HTTP Header Auth type), rotate the key.
- Confirm `audit_logs` has no `UPDATE`/`DELETE` grant for the app's runtime
  DB role at the database permission level — not just "nobody calls it" in
  application code. Add the REVOKE + a rejecting trigger before describing
  the audit trail as "immutable" to any client.

### Priority 1 — Real instant notifications
Currently an `Alert` row is written on refresh/stream event, and nothing
pushes it anywhere until n8n's *nightly batch* email. That's not "instant"
by any reasonable definition and is the largest gap between what this
product would be sold as and what it does today.
- Backend: in `_open_alert()` (main.py), fire an outbound webhook/email
  immediately for `severity in {"critical", "high"}` — separate from the
  nightly digest, which stays for medium/low severity.
- n8n: new `Webhook` trigger workflow for immediate delivery, distinct from
  the existing nightly digest workflow.
- DB: needs delivery tracking — a `notified_at`/`notification_channel` pair
  on `Alert`, or a small `alert_notifications` table if per-channel delivery
  tracking is wanted. Confirm with the client which channel(s) they
  actually check, and whether preference is per-vendor or global, before
  building either shape.

### Priority 2 — Exportable compliance reports
Nothing currently produces a file a client can hand to an auditor, insurer,
or their own downstream client — the dashboard is read-only web UI only.
- `GET /dashboard/vendors/{id}/report.pdf` — single-vendor report: score/
  grade, factor breakdown, financial evidence summary, alert history, audit
  excerpt. Build via the PDF skill, not a hand-rolled PDF library.
- `GET /dashboard/audit-logs/export.csv` — filterable audit log export.
- This is the artifact a compliance officer actually forwards upward. Ships
  before tiering, not after.

### Priority 3 — Lightweight risk tiering
Validated as real, not vanity: it's the standard mechanism for preventing
alert fatigue at scale once Priority 1 exists, not a feature that competes
with notifications for engineering time. Ship the minimal version:
- DB: `risk_tier` enum (`critical | high | medium | low`) on `vendors`,
  nullable, no automatic backfill logic without a confirmed policy for
  which intake fields determine it.
- Backend: `GET /dashboard/vendors?tier=critical` filter.
- Defer `next_review_due` + reassessment-cadence automation until a cadence
  policy is actually approved — don't build a calendar engine around
  numbers nobody signed off on.

### Priority 4 — Remediation tracking
Closes the loop on alerts — "we got an alert" becomes "we can prove we did
something about it," which is the audit-trail value clients actually want.
- DB: `remediation_tasks` table — `id, alert_id, vendor_id, owner, status
  (open|in_progress|resolved), due_date, resolution_notes, created_at,
  updated_at`.
- Backend: `PATCH /dashboard/alerts/{id}/remediation`.

### Priority 5 — Defer until a specific client's workflow requires them
Governance-department features for a product that doesn't have a governance
department yet. Build when required, not speculatively:
- `review_requests` table + onboarding review queue UI
- Offboarding workflow (`status`, `offboarded_at`, `offboarding_reason` on `vendors`)
- Separate `evidence` table for news/sanctions RSS persistence (currently
  only emailed by n8n, not persisted anywhere queryable)
- Webhook loop-closing from review-decision back to n8n vendor-facing emails
- Tiered refresh scheduling (`/refresh-all?tier=critical`) — depends on
  Priority 3 existing first

## 6. Frontend — dashboard blueprint

Reuse existing primitives (`Grade`, `Severity`, `IconMetric`, `Empty`,
`Loading`, `ErrorCard`, `.panel`/`.metric-card`/`.data-num` classes) — no
second design system.

**Overview**
- Existing: 4 metric cards, grade distribution bar chart, top-6 open-alert list
- Add: "due for reassessment" widget (needs `next_review_due`, if tiering cadence ships)
- Add: "awaiting remediation" count card (Priority 4)
- Add: notification delivery status strip — proves Priority 1 actually works
- Add: portfolio-average score trend line (aggregate existing per-vendor `score_history`)

**Vendors (register)**
- Existing: search + flat table
- Add: tier badge column + filter row (tier / grade / review status)
- Add: bulk action bar (select → refresh, export)
- Add: proper empty state for a first-time client with zero vendors

**Vendor Detail**
- Existing: Overview / Onboarding / Companies House / Sanctions / Alerts /
  Financial evidence / Review & audit tabs
- Add: tier badge + "next review due" near the score header
- Add: **Reports tab** — download-PDF button (Priority 2)
- Add: **Remediation tab** — open/in-progress/resolved tasks for this
  vendor's alerts, owner + due date visible
- Add: notification delivery log inside the Alerts tab (delivered at X,
  viewed at Y — not just an acknowledge button)
- Financial evidence tab: add a UI form for `POST /vendors/{id}/override` —
  the backend already supports client corrections to extracted financial
  values, there's currently no UI for it, only direct API access

**Alerts**
- Existing: flat list, single page
- Split into open queue vs remediation board (Kanban: open → in progress →
  resolved), per Priority 4
- Add severity + tier combined filter/sort

**Audit**
- Existing: flat chronological list
- Add filter bar: vendor / actor / date range
- Add an "immutable" badge only once the DB-level trigger from Priority 0
  is actually in place — don't claim it before it's real
- Add CSV export button (Priority 2)

**New: Reports page**
- Central list of generated PDFs per vendor, regenerate/download — the
  screenshot-able proof of value for a client showing their own boss

## 7. Non-functional requirements

- Every new backend endpoint requires the same auth already enforced on
  `/dashboard/*` routes.
- Do not remove or rename any existing field on `Vendor`, `Alert`,
  `AuditEvent`, `Evidence`, `Decision`, or `Detail` — only add.
- Every new table needs `created_at` at minimum; audit-relevant tables
  (`remediation_tasks`, any future `evidence`/`review_requests`) should also
  track who/what triggered the row.
- The audit log's append-only guarantee must be enforced at the database
  permission level, not only in application code.
- Confirm before hardcoding: exact reassessment cadence per tier, which
  intake fields determine initial `risk_tier`, and which notification
  channel(s) a client actually wants — these come from the client's actual
  policy/preference, not an invented default.

## 8. Suggested implementation order

1. Priority 0 security fixes — no dependencies, do first regardless of anything else.
2. Validate the `amount_parse.py` fix against real filings; run the PaddleOCR/GLM-OCR prototype comparison.
3. Priority 1 (instant notifications) — biggest client-visible gap.
4. Priority 2 (exportable reports).
5. Priority 3 (lightweight tiering) — only the DB column + filter, not the cadence engine.
6. Priority 4 (remediation tracking).
7. Frontend blueprint items, in the same order as their backing features land.
8. Priority 5 items — only if/when a specific client's workflow requires them.

Confirm each layer builds/type-checks/deploys cleanly before moving to the
next. Do not attempt everything in a single pass.

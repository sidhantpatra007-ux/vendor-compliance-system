# Changelog

Session history for this project. Each account/session appends an entry
here before switching, in this format:

## YYYY-MM-DD — short title
Changed: file(s) touched
What: what was done
Why: why
Status: working / partial / untested
Next: specific next step

## 2026-09-01 — Spec consolidation, OCR year-value bug fix, LLM provider decision closed
Changed: PRODUCTION_UPGRADE_SPEC.md (rewritten, replaces all prior versions of this file), python-service/companies_house/amount_parse.py, CHANGELOG.md
What: Rewrote PRODUCTION_UPGRADE_SPEC.md as the single authoritative spec. Reordered priorities toward client-visible value (instant alert notifications, exportable PDF/CSV compliance reports) ahead of enterprise-TPRM governance scaffolding (risk tiering, review queues, offboarding), which is now explicitly marked deferred pending confirmation of whether any actual client is FCA-regulated. Patched amount_parse.py::parse_amounts_in_line so amount-shaped tokens (currency symbol, or comma/decimal separators) sort ahead of bare 4-digit 1900-2099 "year-shaped" tokens on the same line, fixing OCR/PDF-text extraction returning a filing year instead of the real financial value.
Why: Original spec sequenced governance features first based on general enterprise-TPRM convention, not on what SMB/startup buyers actually pay for — reordered after checking 2026 TPRM market research. amount_parse.py had no concept of "this number is a year, not money," so a concept-matched line containing a nearby year (filing date reference, duplicated table header) could return the year as the extracted value ahead of the real figure.
Status: Spec — planning document only, nothing in Sections 5/6 built yet. amount_parse.py fix — written and logically sound, NOT yet validated against a real Companies House scan. Do not treat the OCR bug as fully closed until it's been run against actual messy filings, not just reasoned about.
Decided against (do not re-open without new evidence): switching the compliance-summary LLM provider (compliance/summarize.py) from Gemini to ChatGPT/OpenAI. The premise — that ChatGPT's free/unlimited tier avoids a bill "at scale" — is false: that free tier applies to the consumer chat app, not the OpenAI API, which is what summarize.py would actually call and which bills per-token exactly like Gemini's API. Gemini also already has a comparable free tier (Google AI Studio, Flash models, no card). Estimated cost for this call (~300 input / ~150 output tokens per score-change summary) is a fraction of a cent regardless of provider at this project's realistic volume. GEMINI_API_KEY and summarize.py are unchanged — do not swap providers based on a cost argument without an actual observed bill.
Next: (1) Ask whoever owns the client relationship whether they're FCA-regulated before building Section 5/Priority-5 governance features. (2) Validate the amount_parse.py fix against real scanned filings. (3) Prototype PaddleOCR (PP-Structure) and/or GLM-OCR (0.9B GGUF) against real Companies House scans before committing to either as a pytesseract replacement — not decided, not built, no new dependency added yet. (4) Start Priority 0 security fixes (main.py::require_api_key timing-unsafe comparison, config.py hardcoded session-secret fallback, n8n plaintext API key) — these have no dependencies and should go first.

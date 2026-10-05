# n8n workflows

Sanitised exports of the four production workflows. They are committed with
`active: false`, no instance IDs, and placeholder e-mail addresses.

| File | Trigger | What it does |
|---|---|---|
| `1-company-onboarding-intake-and-daily-monitoring.json` | Intake form · daily schedule | Form → `POST /vendors/onboard` → routes to *blocked / enhanced review / standard* e-mails. Daily: `POST /refresh-all`, run summary, failure notices. |
| `dashboard-interactives.json` | Two schedules · bulk-upload form | Delivers pending alert notifications, builds the digest e-mail, and handles CSV bulk onboarding. |
| `dashboard-action-worker.json` | Every minute | Polls `/automation-jobs?status=pending` and runs `bulk_onboard`, `send_questionnaire` and `email_report` jobs, then reports complete/fail. |
| `failure-retry-and-error-handling.json` | Error Trigger | Fetches `/operations/health` and e-mails a failure report. |

## Setup

1. **Import** each file: *Workflows → ⋯ → Import from file*.
2. **Create two credentials** with exactly these names (the nodes reference them by name):
   - `Header Auth account` — type *Header Auth*. Name: `x-internal-api-key`, Value: the `INTERNAL_API_KEY` from `python-service/.env`.
   - `SMTP account` — type *SMTP*, your mail server details.
3. **Replace the placeholders** in the *Email Send* nodes:
   `REPLACE_WITH_APPROVED_SENDER_EMAIL` (from) and `REPLACE_WITH_ALERT_RECIPIENT_EMAIL` (to).
4. **Wire the error handler.** An *Error Trigger* workflow only runs for workflows
   that name it. In each of the other three workflows open *Settings → Error
   workflow* and select **Failure, retry and error handling**.
5. **Activate** the workflows.

The HTTP nodes call `http://python-service:8000`, which resolves only inside the
Docker Compose network, so run n8n from this repository's `docker-compose.yml`.

## Updating a workflow safely

Never commit a raw export. Save it to `n8n/raw/` (git-ignored), then:

```bash
python scripts/sanitize_n8n_export.py n8n/raw/*.json --out n8n/workflows
```

The script strips instance IDs and e-mail addresses and refuses to write a file
that still looks like it contains a hard-coded key.

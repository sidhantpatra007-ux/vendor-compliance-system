from fastapi import Body, Depends, HTTPException

from datetime import datetime, timedelta
import hashlib
import os
import re
from uuid import uuid4

from sqlalchemy import and_, or_

from automation_models import AutomationJob, IntakeRequest
from config import AUDIT_RETENTION_DAYS
from external_events import ingest_external_event
from models import Alert, AlertAction, AuditLog, AuditReportSnapshot, ComplianceSnapshot, EventReviewDecision, RiskEvent, RiskRule, SourceReliabilityConfig, Vendor, VendorRefreshLock
from operations import (
    escalate_overdue, financial_review_summary, freshness_summary, mark_notification_failed,
    mark_notified, notification_feed, notification_payload, save_financial_review,
    serialize_alert, update_alert,
)
from reporting import build_current_report, save_report_snapshot, serialize_report_snapshot
from risk_intelligence import get_risk_summary, save_event_review


def register_risk_routes(app, get_db, require_api_key, require_dashboard_session):
    AUTOMATION_JOB_TYPES = {"bulk_refresh", "generate_report", "bulk_onboard", "send_questionnaire", "email_report"}
    MAX_AUTOMATION_ATTEMPTS = 3
    AUTOMATION_LEASE_MINUTES = 10
    MAX_BULK_COMPANIES = 100
    REQUEST_EXPIRY_DAYS = 30
    EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    INTAKE_FIELDS = {
        "trading_name", "address_street", "address_city", "address_postcode",
        "contact_name", "contact_email", "contact_phone", "vendor_category",
        "goods_or_services", "supplier_criticality", "annual_spend_band",
        "access_to_client_systems_or_data", "processes_personal_data",
        "delivery_countries", "uses_subcontractors", "supplier_declaration_accepted",
    }

    def _job_dict(job):
        payload = job.payload if isinstance(job.payload, dict) else {}
        return {
            "job_id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "attempts": job.attempts,
            "claim_token": job.claim_token,
            "lease_expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
            "error": job.error,
            "result": job.result,
            "created_by": job.created_by,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            **payload,
        }

    def _audit_job(db, job, action, actor, details=None):
        db.add(AuditLog(
            action=action,
            actor=(actor or "system")[:200],
            details={"job_id": job.id, "job_type": job.job_type, **(details or {})},
        ))

    def _request_dict(row):
        return {
            "request_id": row.id,
            "vendor_id": row.vendor_id,
            "status": row.status,
            "requested_fields": row.requested_fields or [],
            "recipient_name": row.recipient_name,
            "recipient_email": row.recipient_email,
            "message": row.message,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat(),
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "expires_at": row.expires_at.isoformat(),
        }

    def _safe_actor(value, fallback="dashboard_user"):
        return str(value or fallback).strip()[:200] or fallback

    def _valid_email(value):
        return isinstance(value, str) and bool(EMAIL_RE.match(value.strip()))

    def _validate_job_request(body):
        if not isinstance(body, dict):
            raise HTTPException(400, "job body must be a JSON object")
        job_type = str(body.get("job_type") or "").strip().lower()
        if job_type not in AUTOMATION_JOB_TYPES:
            raise HTTPException(400, "Unsupported automation job_type")
        payload = body.get("payload") or {}
        if not isinstance(payload, dict):
            raise HTTPException(400, "payload must be a JSON object")
        if job_type == "generate_report":
            vendor_id = payload.get("vendor_id")
            if not isinstance(vendor_id, int) or vendor_id < 1:
                raise HTTPException(400, "generate_report requires payload.vendor_id")
        if job_type == "bulk_onboard":
            companies = payload.get("companies")
            if not isinstance(companies, list) or not 1 <= len(companies) <= MAX_BULK_COMPANIES:
                raise HTTPException(400, f"bulk_onboard requires 1 to {MAX_BULK_COMPANIES} companies")
        if job_type == "send_questionnaire":
            if not isinstance(payload.get("intake_request_id"), int):
                raise HTTPException(400, "send_questionnaire requires payload.intake_request_id")
        if job_type == "email_report":
            if not isinstance(payload.get("report_id"), int) or not EMAIL_RE.match(str(payload.get("recipient_email") or "")):
                raise HTTPException(400, "email_report requires a report_id and valid recipient_email")
        return job_type, payload

    def _create_job(db, body, actor):
        job_type, payload = _validate_job_request(body)
        row = AutomationJob(
            job_type=job_type,
            status="pending",
            payload=payload,
            created_by=(actor or "dashboard")[:200],
        )
        db.add(row)
        db.flush()
        _audit_job(db, row, "automation_job_created", actor)
        return row

    def _claim_jobs(db, limit, actor):
        if not 1 <= limit <= 100:
            raise HTTPException(400, "limit must be between 1 and 100")
        now = datetime.utcnow()
        rows = (
            db.query(AutomationJob)
            .filter(or_(
                AutomationJob.status == "pending",
                and_(
                    AutomationJob.status == "processing",
                    AutomationJob.lease_expires_at.isnot(None),
                    AutomationJob.lease_expires_at < now,
                ),
            ))
            .filter(AutomationJob.attempts < MAX_AUTOMATION_ATTEMPTS)
            .order_by(AutomationJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        for row in rows:
            reclaimed = row.status == "processing"
            row.status = "processing"
            row.attempts += 1
            row.claim_token = uuid4().hex
            row.started_at = row.started_at or now
            row.lease_expires_at = now + timedelta(minutes=AUTOMATION_LEASE_MINUTES)
            row.error = None
            _audit_job(
                db,
                row,
                "automation_job_reclaimed" if reclaimed else "automation_job_claimed",
                actor,
                {"attempt": row.attempts},
            )
        db.commit()
        return [_job_dict(row) for row in rows]

    def _complete_job(db, job_id, body, actor):
        row = db.query(AutomationJob).filter(AutomationJob.id == job_id).with_for_update().first()
        if not row:
            raise HTTPException(404, "Automation job not found")
        if row.status == "completed":
            return _job_dict(row)
        if row.status != "processing":
            raise HTTPException(409, "Only a processing automation job can be completed")
        claim_token = body.get("claim_token") if isinstance(body, dict) else None
        if claim_token and claim_token != row.claim_token:
            raise HTTPException(409, "Automation job claim token does not match")
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, (dict, list, str, int, float, bool)) and result is not None:
            result = {"value": str(result)}
        row.status = "completed"
        row.result = result
        row.error = None
        row.completed_at = datetime.utcnow()
        row.lease_expires_at = None
        if row.job_type == "send_questionnaire":
            request_id = (row.payload or {}).get("intake_request_id")
            request = db.query(IntakeRequest).filter(IntakeRequest.id == request_id).first()
            if request and request.status == "pending":
                request.status = "sent"
                request.sent_at = row.completed_at
        _audit_job(db, row, "automation_job_completed", actor)
        db.commit()
        return _job_dict(row)

    def _fail_job(db, job_id, body, actor):
        row = db.query(AutomationJob).filter(AutomationJob.id == job_id).with_for_update().first()
        if not row:
            raise HTTPException(404, "Automation job not found")
        if row.status == "completed":
            raise HTTPException(409, "Completed automation jobs cannot fail")
        claim_token = body.get("claim_token") if isinstance(body, dict) else None
        if claim_token and claim_token != row.claim_token:
            raise HTTPException(409, "Automation job claim token does not match")
        error = str((body or {}).get("error") or "Automation worker failed")[:2000]
        row.error = error
        row.lease_expires_at = None
        row.claim_token = None
        if row.attempts >= MAX_AUTOMATION_ATTEMPTS:
            row.status = "failed"
            row.completed_at = datetime.utcnow()
            action = "automation_job_failed"
        else:
            row.status = "pending"
            action = "automation_job_retry_queued"
        _audit_job(db, row, action, actor, {"attempt": row.attempts, "error": error})
        db.commit()
        return _job_dict(row)

    def vendor_or_404(db, vendor_id):
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            raise HTTPException(404, "Vendor not found")
        return vendor

    def review(db, event_id, body):
        event = db.query(RiskEvent).filter(RiskEvent.id == event_id).first()
        if not event:
            raise HTTPException(404, "Risk event not found")
        try:
            row = save_event_review(db, event, body.get("decision"), body.get("reviewer"), body.get("note"))
            db.commit()
            db.refresh(row)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc
        return {"review_id": row.id, "event_id": row.event_id, "decision": row.decision, "reviewer": row.reviewer, "note": row.note, "created_at": row.created_at.isoformat()}

    def alert_or_404(db, alert_id):
        row = db.query(Alert).filter(Alert.id == alert_id).first()
        if not row:
            raise HTTPException(404, "Alert not found")
        return row

    def latest_snapshot_or_404(db, vendor):
        row = db.query(ComplianceSnapshot).filter(ComplianceSnapshot.vendor_id == vendor.id).order_by(ComplianceSnapshot.checked_at.desc()).first()
        if not row:
            raise HTTPException(409, "Vendor has no compliance snapshot")
        return row

    def change_alert(db, alert_id, body):
        try:
            row = update_alert(db, alert_or_404(db, alert_id), body.get("action"), body.get("actor"), body.get("assigned_to"), body.get("note"))
            db.commit()
            db.refresh(row)
            return serialize_alert(row)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc

    @app.post("/automation-jobs")
    def create_automation_job(body: dict = Body(...), db=Depends(get_db), auth=Depends(require_api_key)):
        row = _create_job(db, body, body.get("actor", "internal_api"))
        db.commit()
        db.refresh(row)
        return _job_dict(row)

    @app.get("/automation-jobs")
    def claim_automation_jobs(status: str = "pending", limit: int = 25, db=Depends(get_db), auth=Depends(require_api_key)):
        if status != "pending":
            raise HTTPException(400, "Only status=pending may be claimed by n8n")
        return _claim_jobs(db, limit, "n8n")

    @app.post("/automation-jobs/{job_id}/complete")
    def complete_automation_job(job_id: int, body: dict = Body(default={}), db=Depends(get_db), auth=Depends(require_api_key)):
        return _complete_job(db, job_id, body, body.get("actor", "n8n"))

    @app.post("/automation-jobs/{job_id}/fail")
    def fail_automation_job(job_id: int, body: dict = Body(default={}), db=Depends(get_db), auth=Depends(require_api_key)):
        return _fail_job(db, job_id, body, body.get("actor", "n8n"))

    @app.post("/dashboard/automation-jobs")
    def dashboard_create_automation_job(body: dict = Body(...), db=Depends(get_db), auth=Depends(require_dashboard_session)):
        row = _create_job(db, body, body.get("actor", "dashboard_user"))
        db.commit()
        db.refresh(row)
        return _job_dict(row)

    @app.get("/dashboard/automation-jobs")
    def dashboard_automation_jobs(limit: int = 50, job_type: str | None = None, db=Depends(get_db), auth=Depends(require_dashboard_session)):
        if not 1 <= limit <= 100:
            raise HTTPException(400, "limit must be between 1 and 100")
        query = db.query(AutomationJob)
        if job_type:
            query = query.filter(AutomationJob.job_type == job_type.strip().lower())
        rows = query.order_by(AutomationJob.created_at.desc()).limit(limit).all()
        return [_job_dict(row) for row in rows]

    @app.get("/dashboard/automation-jobs/{job_id}")
    def dashboard_automation_job(job_id: int, db=Depends(get_db), auth=Depends(require_dashboard_session)):
        row = db.query(AutomationJob).filter(AutomationJob.id == job_id).first()
        if not row:
            raise HTTPException(404, "Automation job not found")
        return _job_dict(row)

    @app.post("/dashboard/bulk-onboarding-jobs")
    def dashboard_bulk_onboarding_job(body: dict = Body(...), db=Depends(get_db), auth=Depends(require_dashboard_session)):
        companies = body.get("companies") if isinstance(body, dict) else None
        if not isinstance(companies, list) or not 1 <= len(companies) <= MAX_BULK_COMPANIES:
            raise HTTPException(400, f"Provide 1 to {MAX_BULK_COMPANIES} companies")
        normalized = []
        for position, company in enumerate(companies, start=1):
            if not isinstance(company, dict):
                raise HTTPException(400, f"Company at row {position} must be an object")
            number = str(company.get("company_number") or "").strip().upper()
            if not number:
                raise HTTPException(400, f"Company number is required at row {position}")
            normalized.append({
                **{key: value for key, value in company.items() if key in INTAKE_FIELDS or key in {"company_number", "display_name", "csv_row"}},
                "company_number": number,
                "csv_row": int(company.get("csv_row") or position),
            })
        row = _create_job(db, {
            "job_type": "bulk_onboard",
            "payload": {"companies": normalized, "submitted": len(normalized)},
        }, _safe_actor(body.get("actor")))
        db.commit()
        db.refresh(row)
        return _job_dict(row)

    @app.patch("/dashboard/vendors/{vendor_id}/contact")
    def dashboard_update_vendor_contact(vendor_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_dashboard_session)):
        vendor = vendor_or_404(db, vendor_id)
        email = str(body.get("contact_email") or "").strip().lower()
        if not _valid_email(email):
            raise HTTPException(400, "A valid contact_email is required")
        vendor.contact_email = email
        if isinstance(body.get("contact_name"), str):
            vendor.contact_name = body["contact_name"].strip()[:200] or None
        db.add(AuditLog(vendor_id=vendor.id, action="vendor_contact_updated", actor=_safe_actor(body.get("actor")), details={}))
        db.commit()
        return {"vendor_id": vendor.id, "contact_name": vendor.contact_name, "contact_email": vendor.contact_email}

    @app.post("/dashboard/vendors/{vendor_id}/intake-requests")
    def dashboard_create_intake_request(vendor_id: int, body: dict = Body(default={}), db=Depends(get_db), auth=Depends(require_dashboard_session)):
        vendor = vendor_or_404(db, vendor_id)
        email = str(body.get("contact_email") or vendor.contact_email or "").strip().lower()
        if not _valid_email(email):
            raise HTTPException(400, "Add a valid company contact email before sending a questionnaire")
        fields = body.get("requested_fields") or sorted(INTAKE_FIELDS - {"contact_email", "contact_name"})
        if not isinstance(fields, list) or not fields or any(field not in INTAKE_FIELDS for field in fields):
            raise HTTPException(400, "requested_fields contains an unsupported field")
        token = uuid4().hex + uuid4().hex
        request = IntakeRequest(
            vendor_id=vendor.id,
            status="pending",
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            requested_fields=list(dict.fromkeys(fields)),
            recipient_name=str(body.get("contact_name") or vendor.contact_name or "").strip()[:200] or None,
            recipient_email=email,
            message=str(body.get("message") or "").strip()[:2000] or None,
            created_by=_safe_actor(body.get("actor")),
            expires_at=datetime.utcnow() + timedelta(days=REQUEST_EXPIRY_DAYS),
        )
        vendor.contact_email = email
        db.add(request)
        db.flush()
        public_base = os.getenv("QUESTIONNAIRE_BASE_URL", "").strip().rstrip("/")
        if not public_base:
            db.rollback()
            raise HTTPException(409, "QUESTIONNAIRE_BASE_URL must be configured before sending questionnaires")
        job = _create_job(db, {
            "job_type": "send_questionnaire",
            "payload": {
                "intake_request_id": request.id,
                "vendor_id": vendor.id,
                "vendor_name": vendor.display_name,
                "recipient_name": request.recipient_name,
                "recipient_email": request.recipient_email,
                "requested_fields": request.requested_fields,
                "message": request.message,
                "expires_at": request.expires_at.isoformat(),
                "questionnaire_url": f"{public_base}?token={token}",
            },
        }, request.created_by)
        _audit_job(db, job, "intake_request_created", request.created_by, {"vendor_id": vendor.id, "intake_request_id": request.id})
        db.commit()
        db.refresh(request)
        return {"request": _request_dict(request), "job": _job_dict(job)}

    @app.get("/dashboard/vendors/{vendor_id}/intake-requests")
    def dashboard_intake_requests(vendor_id: int, limit: int = 25, db=Depends(get_db), auth=Depends(require_dashboard_session)):
        vendor_or_404(db, vendor_id)
        if not 1 <= limit <= 100:
            raise HTTPException(400, "limit must be between 1 and 100")
        rows = db.query(IntakeRequest).filter(IntakeRequest.vendor_id == vendor_id).order_by(IntakeRequest.created_at.desc()).limit(limit).all()
        return [_request_dict(row) for row in rows]

    @app.post("/intake-requests/{token}/submit")
    def submit_intake_request(token: str, body: dict = Body(...), db=Depends(get_db)):
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        request = db.query(IntakeRequest).filter(IntakeRequest.token_hash == token_hash).first()
        now = datetime.utcnow()
        if not request or request.status in {"cancelled", "completed"} or request.expires_at <= now:
            raise HTTPException(404, "This questionnaire link is invalid or expired")
        answers = body.get("answers") if isinstance(body, dict) else None
        if not isinstance(answers, dict):
            raise HTTPException(400, "answers must be a JSON object")
        allowed_answers = {key: value for key, value in answers.items() if key in request.requested_fields and key in INTAKE_FIELDS}
        if not allowed_answers:
            raise HTTPException(400, "No requested questionnaire fields were supplied")
        vendor = vendor_or_404(db, request.vendor_id)
        for key, value in allowed_answers.items():
            if key == "contact_email" and value is not None and not _valid_email(str(value).strip().lower()):
                raise HTTPException(400, "contact_email is invalid")
            setattr(vendor, key, value)
        request.response = allowed_answers
        request.status = "completed"
        request.completed_at = now
        db.add(AuditLog(vendor_id=vendor.id, action="intake_questionnaire_completed", actor="questionnaire", details={"intake_request_id": request.id, "fields": sorted(allowed_answers)}))
        db.commit()
        return {"request_id": request.id, "status": request.status, "updated_fields": sorted(allowed_answers)}

    @app.post("/dashboard/vendors/{vendor_id}/reports/email")
    def dashboard_email_report(vendor_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_dashboard_session)):
        vendor = vendor_or_404(db, vendor_id)
        recipient_email = str(body.get("recipient_email") or "").strip().lower()
        if not _valid_email(recipient_email):
            raise HTTPException(400, "A valid recipient_email is required")
        actor = _safe_actor(body.get("actor"))
        report = save_report_snapshot(db, vendor, actor)
        db.flush()
        job = _create_job(db, {
            "job_type": "email_report",
            "payload": {"vendor_id": vendor.id, "vendor_name": vendor.display_name, "report_id": report.id, "recipient_email": recipient_email},
        }, actor)
        db.commit()
        db.refresh(report)
        return {"report": serialize_report_snapshot(report, include_payload=True), "job": _job_dict(job)}

    @app.get("/vendors/{vendor_id}/risk")
    def vendor_risk(vendor_id: int, db=Depends(get_db), auth=Depends(require_api_key)):
        return get_risk_summary(db, vendor_or_404(db, vendor_id))

    @app.get("/vendors/{vendor_id}/reports/current")
    def current_report(vendor_id: int, db=Depends(get_db), auth=Depends(require_api_key)):
        return build_current_report(db, vendor_or_404(db, vendor_id))

    @app.post("/vendors/{vendor_id}/reports")
    def create_report(vendor_id: int, body: dict = Body(default={}), db=Depends(get_db), auth=Depends(require_api_key)):
        row = save_report_snapshot(db, vendor_or_404(db, vendor_id), body.get("generated_by", "internal_api"))
        db.commit()
        db.refresh(row)
        return serialize_report_snapshot(row, include_payload=True)

    @app.get("/vendors/{vendor_id}/reports")
    def list_reports(vendor_id: int, db=Depends(get_db), auth=Depends(require_api_key)):
        vendor_or_404(db, vendor_id)
        rows = db.query(AuditReportSnapshot).filter(AuditReportSnapshot.vendor_id == vendor_id).order_by(AuditReportSnapshot.created_at.desc()).all()
        return [serialize_report_snapshot(row) for row in rows]

    @app.get("/vendors/{vendor_id}/reports/{report_id}")
    def get_report(vendor_id: int, report_id: int, db=Depends(get_db), auth=Depends(require_api_key)):
        row = db.query(AuditReportSnapshot).filter(AuditReportSnapshot.vendor_id == vendor_id, AuditReportSnapshot.id == report_id).first()
        if not row:
            raise HTTPException(404, "Report not found")
        return serialize_report_snapshot(row, include_payload=True)

    @app.post("/risk-events/{event_id}/review")
    def review_event(event_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_api_key)):
        return review(db, event_id, body)

    @app.get("/risk-events/{event_id}/reviews")
    def event_reviews(event_id: int, db=Depends(get_db), auth=Depends(require_api_key)):
        if not db.query(RiskEvent).filter(RiskEvent.id == event_id).first():
            raise HTTPException(404, "Risk event not found")
        rows = db.query(EventReviewDecision).filter(EventReviewDecision.event_id == event_id).order_by(EventReviewDecision.created_at.desc()).all()
        return [{"review_id": row.id, "decision": row.decision, "reviewer": row.reviewer, "note": row.note, "created_at": row.created_at.isoformat()} for row in rows]

    @app.get("/risk-rules")
    def risk_rules(db=Depends(get_db), auth=Depends(require_api_key)):
        rows = db.query(RiskRule).order_by(RiskRule.category, RiskRule.rule_key).all()
        return [{"rule_key": row.rule_key, "name": row.name, "category": row.category, "event_type": row.event_type, "severity": row.severity, "conditions": row.conditions, "base_delta": row.base_delta, "affects_score": row.affects_score, "affects_alert": row.affects_alert, "enabled": row.enabled, "version": row.version} for row in rows]

    @app.get("/sources/reliability")
    def source_reliability(db=Depends(get_db), auth=Depends(require_api_key)):
        rows = db.query(SourceReliabilityConfig).order_by(SourceReliabilityConfig.source).all()
        return [{"source": row.source, "reliability": row.reliability, "enabled": row.enabled, "description": row.description, "metadata": row.metadata_json} for row in rows]

    @app.get("/vendors/{vendor_id}/coverage")
    def vendor_coverage(vendor_id: int, db=Depends(get_db), auth=Depends(require_api_key)):
        vendor = vendor_or_404(db, vendor_id)
        snapshot = latest_snapshot_or_404(db, vendor)
        return {"vendor_id": vendor.id, "snapshot_id": snapshot.id, "data_freshness": freshness_summary(db, vendor.id), "financial_review": financial_review_summary(db, vendor.id, snapshot.id)}

    @app.post("/vendors/{vendor_id}/financial-review")
    def financial_review(vendor_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_api_key)):
        vendor = vendor_or_404(db, vendor_id)
        snapshot = latest_snapshot_or_404(db, vendor)
        try:
            row = save_financial_review(db, vendor, snapshot, body.get("status"), body.get("reviewer"), body.get("note"))
            db.commit()
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc
        return financial_review_summary(db, vendor.id, snapshot.id)

    @app.post("/vendors/{vendor_id}/external-events")
    def add_external_event(vendor_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_api_key)):
        try:
            result = ingest_external_event(db, vendor_or_404(db, vendor_id), body)
            db.commit()
            return result
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc

    @app.get("/alerts/operations")
    def operational_alerts(status: str | None = None, db=Depends(get_db), auth=Depends(require_api_key)):
        query = db.query(Alert)
        if status:
            query = query.filter(Alert.status == status)
        return [serialize_alert(row) for row in query.order_by(Alert.created_at.desc()).all()]

    @app.get("/alerts/{alert_id}/operations")
    def operational_alert(alert_id: int, db=Depends(get_db), auth=Depends(require_api_key)):
        row = alert_or_404(db, alert_id)
        actions = db.query(AlertAction).filter(AlertAction.alert_id == row.id).order_by(AlertAction.created_at.asc()).all()
        return {"alert": serialize_alert(row), "actions": [{"action_id": item.id, "action": item.action, "actor": item.actor, "note": item.note, "details": item.details, "created_at": item.created_at.isoformat()} for item in actions]}

    @app.post("/alerts/{alert_id}/actions")
    def alert_action(alert_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_api_key)):
        return change_alert(db, alert_id, body)

    @app.post("/operations/alerts/escalate-overdue")
    def operational_escalate(db=Depends(get_db), auth=Depends(require_api_key)):
        rows = escalate_overdue(db)
        db.commit()
        return {"escalated_count": len(rows), "alerts": [serialize_alert(row) for row in rows]}

    @app.get("/notifications/pending")
    def pending_notifications(limit: int = 100, db=Depends(get_db), auth=Depends(require_api_key)):
        if limit < 1 or limit > 500:
            raise HTTPException(400, "limit must be between 1 and 500")
        return [notification_payload(row) for row in notification_feed(db, limit)]

    @app.post("/alerts/{alert_id}/notifications/delivered")
    def notification_delivered(alert_id: int, body: dict = Body(default={}), db=Depends(get_db), auth=Depends(require_api_key)):
        try:
            row = mark_notified(db, alert_or_404(db, alert_id), body.get("channel", "email"), body.get("actor", "n8n"))
            db.commit()
            return serialize_alert(row)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc

    @app.post("/alerts/{alert_id}/notifications/failed")
    def notification_failed(alert_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_api_key)):
        try:
            row = mark_notification_failed(db, alert_or_404(db, alert_id), body.get("error"), body.get("actor", "n8n"))
            db.commit()
            return serialize_alert(row)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc

    @app.get("/operations/health")
    def operations_health(db=Depends(get_db), auth=Depends(require_api_key)):
        now = datetime.utcnow()
        active_locks = db.query(VendorRefreshLock).filter(VendorRefreshLock.expires_at > now).all()
        failed_notifications = db.query(Alert).filter(Alert.notification_state == "failed").count()
        recent_failures = db.query(AuditLog).filter(AuditLog.action == "refresh_all_vendor_failed").order_by(AuditLog.created_at.desc()).limit(20).all()
        return {
            "checked_at": now.isoformat(),
            "active_refresh_locks": [{"vendor_id": row.vendor_id, "expires_at": row.expires_at.isoformat()} for row in active_locks],
            "failed_notification_count": failed_notifications,
            "recent_refresh_failures": [{"vendor_id": row.vendor_id, "details": row.details, "created_at": row.created_at.isoformat()} for row in recent_failures],
        }

    @app.post("/vendors/{vendor_id}/archive")
    def archive_vendor(vendor_id: int, body: dict = Body(default={}), db=Depends(get_db), auth=Depends(require_api_key)):
        vendor = vendor_or_404(db, vendor_id)
        vendor.archived_at = datetime.utcnow()
        db.add(AuditLog(vendor_id=vendor.id, action="vendor_archived", actor=(body.get("actor") or "internal_api")[:200], details={"retention_days": AUDIT_RETENTION_DAYS}))
        db.commit()
        return {"vendor_id": vendor.id, "archived_at": vendor.archived_at.isoformat(), "evidence_retention_eligible_at": (vendor.archived_at + timedelta(days=AUDIT_RETENTION_DAYS)).isoformat()}

    @app.get("/operations/retention/candidates")
    def retention_candidates(db=Depends(get_db), auth=Depends(require_api_key)):
        cutoff = datetime.utcnow() - timedelta(days=AUDIT_RETENTION_DAYS)
        rows = db.query(Vendor).filter(Vendor.archived_at.isnot(None), Vendor.archived_at <= cutoff).order_by(Vendor.archived_at.asc()).all()
        return {"retention_days": AUDIT_RETENTION_DAYS, "candidates": [{"vendor_id": row.id, "display_name": row.display_name, "archived_at": row.archived_at.isoformat()} for row in rows], "note": "Candidates are listed only. Delete evidence manually after client approval; this endpoint never deletes records or files."}

    @app.get("/dashboard/vendors/{vendor_id}/risk")
    def dashboard_risk(vendor_id: int, db=Depends(get_db), auth=Depends(require_dashboard_session)):
        return get_risk_summary(db, vendor_or_404(db, vendor_id))

    @app.get("/dashboard/vendors/{vendor_id}/reports/current")
    def dashboard_current_report(vendor_id: int, db=Depends(get_db), auth=Depends(require_dashboard_session)):
        return build_current_report(db, vendor_or_404(db, vendor_id))

    @app.post("/dashboard/vendors/{vendor_id}/reports")
    def dashboard_create_report(vendor_id: int, body: dict = Body(default={}), db=Depends(get_db), auth=Depends(require_dashboard_session)):
        row = save_report_snapshot(db, vendor_or_404(db, vendor_id), body.get("generated_by", "dashboard_reviewer"))
        db.commit()
        db.refresh(row)
        return serialize_report_snapshot(row, include_payload=True)

    @app.post("/dashboard/risk-events/{event_id}/review")
    def dashboard_review_event(event_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_dashboard_session)):
        return review(db, event_id, body)

    @app.get("/dashboard/vendors/{vendor_id}/coverage")
    def dashboard_vendor_coverage(vendor_id: int, db=Depends(get_db), auth=Depends(require_dashboard_session)):
        vendor = vendor_or_404(db, vendor_id)
        snapshot = latest_snapshot_or_404(db, vendor)
        return {"vendor_id": vendor.id, "snapshot_id": snapshot.id, "data_freshness": freshness_summary(db, vendor.id), "financial_review": financial_review_summary(db, vendor.id, snapshot.id)}

    @app.post("/dashboard/vendors/{vendor_id}/financial-review")
    def dashboard_financial_review(vendor_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_dashboard_session)):
        vendor = vendor_or_404(db, vendor_id)
        snapshot = latest_snapshot_or_404(db, vendor)
        try:
            save_financial_review(db, vendor, snapshot, body.get("status"), body.get("reviewer"), body.get("note"))
            db.commit()
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc
        return financial_review_summary(db, vendor.id, snapshot.id)

    @app.get("/dashboard/alerts/operations")
    def dashboard_operational_alerts(status: str | None = None, db=Depends(get_db), auth=Depends(require_dashboard_session)):
        query = db.query(Alert)
        if status:
            query = query.filter(Alert.status == status)
        return [serialize_alert(row) for row in query.order_by(Alert.created_at.desc()).all()]

    @app.post("/dashboard/alerts/{alert_id}/actions")
    def dashboard_alert_action(alert_id: int, body: dict = Body(...), db=Depends(get_db), auth=Depends(require_dashboard_session)):
        return change_alert(db, alert_id, body)

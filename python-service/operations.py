from datetime import datetime, timedelta

from models import (
    Alert, AlertAction, AuditLog, ComplianceSnapshot, FinancialEvidenceReview,
    FilingEvidencePage, RiskEvent, SourceSyncState,
)
from risk_intelligence import alert_policy

OPEN_ALERT_STATUSES = {"open", "acknowledged", "escalated"}
CLOSED_ALERT_STATUSES = {"resolved", "false_positive"}
ALERT_STATUSES = OPEN_ALERT_STATUSES | CLOSED_ALERT_STATUSES
FINANCIAL_REVIEW_STATUSES = {"pending", "reviewed", "reprocess_required", "not_required", "unavailable"}


def _audit(db, action, vendor_id=None, alert_id=None, actor=None, details=None):
    db.add(AuditLog(vendor_id=vendor_id, alert_id=alert_id, action=action, actor=actor, details=details or {}))


def _action(db, alert, action, actor=None, note=None, details=None):
    db.add(AlertAction(alert_id=alert.id, action=action, actor=actor, note=note, details=details or {}))


def _latest_snapshot(db, vendor_id):
    return db.query(ComplianceSnapshot).filter(ComplianceSnapshot.vendor_id == vendor_id).order_by(ComplianceSnapshot.checked_at.desc()).first()


def _open_or_update_alert(db, vendor, snapshot, dedup_key, severity, title, reason, evidence):
    alert = db.query(Alert).filter(Alert.vendor_id == vendor.id, Alert.dedup_key == dedup_key, Alert.status.in_(OPEN_ALERT_STATUSES)).first()
    if alert:
        alert.severity = severity
        alert.reason = reason
        alert.evidence = evidence
        alert.updated_at = datetime.utcnow()
        return alert, False
    _, hours = alert_policy(vendor.supplier_criticality, severity)
    alert = Alert(
        vendor_id=vendor.id, snapshot_id=snapshot.id, dedup_key=dedup_key,
        severity=severity, title=title, reason=reason, evidence=evidence,
        sla_due_at=datetime.utcnow() + timedelta(hours=hours),
        notification_state="pending",
    )
    db.add(alert)
    db.flush()
    _action(db, alert, "opened", details={"dedup_key": dedup_key, "severity": severity, "sla_hours": hours})
    _audit(db, "alert_opened", vendor.id, alert.id, details={"dedup_key": dedup_key, "severity": severity})
    return alert, True


def sync_operational_state(db, vendor, snapshot):
    signals = snapshot.signals or {}
    if signals.get("sanctions_match_state") not in {"POSSIBLE_MATCH", "REVIEW_REQUIRED"}:
        return []
    event = db.query(RiskEvent).filter(
        RiskEvent.vendor_id == vendor.id,
        RiskEvent.event_type.in_(("SANCTIONS_POSSIBLE_MATCH", "SANCTIONS_REVIEW_REQUIRED")),
    ).order_by(RiskEvent.detected_at.desc()).first()
    if not event:
        return []
    severity, _ = alert_policy(vendor.supplier_criticality, "medium")
    alert, created = _open_or_update_alert(
        db, vendor, snapshot, f"sanctions-review:{event.id}", severity,
        f"Sanctions screening review: {vendor.display_name}",
        "A sanctions name-screening candidate requires human review. This is not a confirmed sanctions finding.",
        [{"type": "risk_event", "risk_event_id": event.id, "event_type": event.event_type, "source_url": event.source_url}],
    )
    return [alert] if created else []


def ensure_financial_review(db, vendor, snapshot):
    pages = db.query(FilingEvidencePage).filter(
        FilingEvidencePage.vendor_id == vendor.id,
        FilingEvidencePage.snapshot_id == snapshot.id,
    ).count()
    row = db.query(FinancialEvidenceReview).filter(
        FinancialEvidenceReview.vendor_id == vendor.id,
        FinancialEvidenceReview.snapshot_id == snapshot.id,
    ).first()
    desired = "pending" if pages else "unavailable"
    if not row:
        row = FinancialEvidenceReview(vendor_id=vendor.id, snapshot_id=snapshot.id, status=desired)
        db.add(row)
        db.flush()
    return row


def financial_review_summary(db, vendor_id, snapshot_id):
    row = db.query(FinancialEvidenceReview).filter(
        FinancialEvidenceReview.vendor_id == vendor_id,
        FinancialEvidenceReview.snapshot_id == snapshot_id,
    ).first()
    if not row:
        return {"status": "unavailable", "label": "Financial data unavailable", "page_count": 0}
    pages = db.query(FilingEvidencePage).filter(
        FilingEvidencePage.vendor_id == vendor_id,
        FilingEvidencePage.snapshot_id == snapshot_id,
    ).count()
    labels = {
        "pending": "Financial review pending",
        "reviewed": "Financial evidence reviewed",
        "reprocess_required": "Financial evidence needs reprocessing",
        "not_required": "Financial review not required",
        "unavailable": "Financial data unavailable",
    }
    return {"review_id": row.id, "status": row.status, "label": labels[row.status], "page_count": pages, "reviewer": row.reviewer, "note": row.note, "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None}


def save_financial_review(db, vendor, snapshot, status, reviewer, note):
    status, reviewer = (status or "").strip().lower(), (reviewer or "").strip()
    if status not in FINANCIAL_REVIEW_STATUSES:
        raise ValueError("status must be pending, reviewed, reprocess_required, not_required, or unavailable")
    if status in {"reviewed", "reprocess_required", "not_required"} and not reviewer:
        raise ValueError("reviewer is required for this status")
    row = ensure_financial_review(db, vendor, snapshot)
    row.status, row.reviewer, row.note = status, reviewer[:200] or None, (note or "").strip()[:2000] or None
    row.reviewed_at = datetime.utcnow() if status in {"reviewed", "reprocess_required", "not_required"} else None
    _audit(db, "financial_evidence_reviewed", vendor.id, actor=row.reviewer, details={"snapshot_id": snapshot.id, "status": status})
    return row


def update_alert(db, alert, action, actor, assigned_to=None, note=None):
    action, actor = (action or "").strip().lower(), (actor or "").strip()
    if not actor:
        raise ValueError("actor is required")
    if action not in {"acknowledge", "assign", "resolve", "false_positive", "escalate"}:
        raise ValueError("action must be acknowledge, assign, resolve, false_positive, or escalate")
    if alert.status in CLOSED_ALERT_STATUSES:
        raise ValueError("closed alerts cannot be changed")
    if action in {"resolve", "false_positive"} and not (note or "").strip():
        raise ValueError("note is required when closing an alert")
    now = datetime.utcnow()
    if assigned_to is not None:
        alert.assigned_to = assigned_to.strip()[:200] or None
    if action == "acknowledge":
        alert.status, alert.acknowledged_at, alert.acknowledged_by = "acknowledged", now, actor[:200]
    elif action == "assign":
        if not alert.assigned_to:
            raise ValueError("assigned_to is required")
    elif action in {"resolve", "false_positive"}:
        alert.status, alert.resolved_at, alert.resolved_by, alert.resolution_note = action, now, actor[:200], note.strip()[:2000]
    elif action == "escalate":
        alert.status, alert.escalated_at, alert.notification_state = "escalated", now, "pending"
    _action(db, alert, action, actor[:200], (note or "").strip()[:2000] or None, {"assigned_to": alert.assigned_to, "status": alert.status})
    _audit(db, "alert_" + action, alert.vendor_id, alert.id, actor[:200], {"status": alert.status, "assigned_to": alert.assigned_to})
    return alert


def escalate_overdue(db):
    now = datetime.utcnow()
    rows = db.query(Alert).filter(Alert.status.in_(("open", "acknowledged")), Alert.sla_due_at <= now).all()
    for alert in rows:
        alert.status, alert.escalated_at, alert.notification_state = "escalated", now, "pending"
        _action(db, alert, "sla_escalated", details={"sla_due_at": alert.sla_due_at.isoformat()})
        _audit(db, "alert_escalated", alert.vendor_id, alert.id, details={"reason": "sla_overdue"})
    return rows


def notification_feed(db, limit=100):
    rows = db.query(Alert).filter(Alert.status.in_(OPEN_ALERT_STATUSES), Alert.notification_state == "pending").order_by(Alert.created_at.asc()).limit(limit).all()
    return rows


def mark_notified(db, alert, channel, actor="n8n"):
    if alert.status in CLOSED_ALERT_STATUSES:
        raise ValueError("cannot notify a closed alert")
    channel = (channel or "email").strip().lower()
    if channel not in {"email", "slack", "other"}:
        raise ValueError("channel must be email, slack, or other")
    alert.notification_state, alert.notification_count, alert.last_notified_at, alert.notification_last_error = "delivered", (alert.notification_count or 0) + 1, datetime.utcnow(), None
    _action(db, alert, "notification_delivered", actor, details={"channel": channel, "notification_count": alert.notification_count})
    _audit(db, "alert_notification_delivered", alert.vendor_id, alert.id, actor, {"channel": channel})
    return alert


def mark_notification_failed(db, alert, error, actor="n8n"):
    if alert.status in CLOSED_ALERT_STATUSES:
        raise ValueError("cannot update a closed alert")
    alert.notification_count = (alert.notification_count or 0) + 1
    alert.notification_last_error = (error or "Notification failed")[:2000]
    alert.last_notified_at = datetime.utcnow()
    alert.notification_state = "failed" if alert.notification_count >= 3 else "pending"
    _action(db, alert, "notification_failed", actor, details={"attempt": alert.notification_count, "will_retry": alert.notification_state == "pending"})
    _audit(db, "alert_notification_failed", alert.vendor_id, alert.id, actor, {"attempt": alert.notification_count})
    return alert


def notification_payload(alert):
    """Stable, channel-neutral data for n8n delivery nodes."""
    vendor = alert.vendor
    return {
        "alert": serialize_alert(alert),
        "vendor": {
            "vendor_id": vendor.id,
            "display_name": vendor.display_name,
            "company_number": vendor.company_number,
            "supplier_criticality": vendor.supplier_criticality,
        },
        "delivery": {
            "subject": f"[{alert.severity.upper()}] {alert.title}",
            "plain_text": f"{alert.reason}\n\nVendor: {vendor.display_name} ({vendor.company_number})\nSLA due: {alert.sla_due_at.isoformat()}\nAlert ID: {alert.id}",
        },
    }


def freshness_summary(db, vendor_id):
    now = datetime.utcnow()
    result = []
    for row in db.query(SourceSyncState).filter(SourceSyncState.vendor_id == vendor_id).order_by(SourceSyncState.source).all():
        age = (now - row.last_successful_sync).total_seconds() if row.last_successful_sync else None
        state = "healthy" if row.status == "healthy" and age is not None and age <= 172800 else "stale" if row.last_successful_sync else "unavailable"
        result.append({"source": row.source, "state": state, "last_successful_sync": row.last_successful_sync.isoformat() if row.last_successful_sync else None, "age_seconds": int(age) if age is not None else None, "last_error": row.last_error})
    return result


def serialize_alert(alert):
    return {"alert_id": alert.id, "vendor_id": alert.vendor_id, "snapshot_id": alert.snapshot_id, "severity": alert.severity, "status": alert.status, "title": alert.title, "reason": alert.reason, "evidence": alert.evidence, "assigned_to": alert.assigned_to, "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None, "acknowledged_by": alert.acknowledged_by, "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None, "resolved_by": alert.resolved_by, "resolution_note": alert.resolution_note, "sla_due_at": alert.sla_due_at.isoformat(), "escalated_at": alert.escalated_at.isoformat() if alert.escalated_at else None, "notification_state": alert.notification_state, "notification_count": alert.notification_count, "notification_last_error": alert.notification_last_error, "last_notified_at": alert.last_notified_at.isoformat() if alert.last_notified_at else None, "created_at": alert.created_at.isoformat(), "updated_at": alert.updated_at.isoformat()}

from datetime import datetime

from models import (
    Alert, AuditReportSnapshot, ComplianceSnapshot, EventEvidence,
    EventReviewDecision, FilingEvidencePage, RiskEvent, SourceSyncState,
)
from risk_intelligence import get_risk_summary
from operations import financial_review_summary, freshness_summary, serialize_alert

REPORT_VERSION = "1.2.0"


def build_current_report(db, vendor):
    latest = db.query(ComplianceSnapshot).filter(ComplianceSnapshot.vendor_id == vendor.id).order_by(ComplianceSnapshot.checked_at.desc()).first()
    risk = get_risk_summary(db, vendor)
    events = db.query(RiskEvent).filter(RiskEvent.vendor_id == vendor.id).order_by(RiskEvent.detected_at.desc()).limit(100).all()
    event_ids = [event.id for event in events]
    evidence = db.query(EventEvidence).filter(EventEvidence.event_id.in_(event_ids)).all() if event_ids else []
    evidence_by_event = {}
    for row in evidence:
        evidence_by_event.setdefault(row.event_id, []).append({"source": row.source, "source_url": row.source_url, "source_record_id": row.source_record_id, "source_version": row.source_version, "retrieved_at": row.retrieved_at.isoformat()})
    reviews = db.query(EventReviewDecision).join(RiskEvent).filter(RiskEvent.vendor_id == vendor.id).order_by(EventReviewDecision.created_at.desc()).all()
    review_by_event = {}
    for row in reviews:
        review_by_event.setdefault(row.event_id, {"decision": row.decision, "reviewer": row.reviewer, "note": row.note, "created_at": row.created_at.isoformat()})
    alerts = db.query(Alert).filter(Alert.vendor_id == vendor.id, Alert.status.in_(("open", "acknowledged", "escalated"))).order_by(Alert.created_at.desc()).all()
    sources = db.query(SourceSyncState).filter(SourceSyncState.vendor_id == vendor.id).order_by(SourceSyncState.source).all()
    filing_pages = db.query(FilingEvidencePage).filter(FilingEvidencePage.vendor_id == vendor.id, FilingEvidencePage.snapshot_id == latest.id).order_by(FilingEvidencePage.page_number).all() if latest else []
    blocked = risk.get("overall_status") == "BLOCKED"
    review_required = risk.get("overall_status") in {"BLOCKED", "REVIEW_REQUIRED"}
    return {
        "report_version": REPORT_VERSION,
        "report_generated_at": datetime.utcnow().isoformat(),
        "vendor": {
            "vendor_id": vendor.id, "display_name": vendor.display_name, "company_number": vendor.company_number,
            "trading_name": vendor.trading_name, "supplier_category": vendor.vendor_category,
            "goods_or_services": vendor.goods_or_services, "supplier_criticality": vendor.supplier_criticality,
            "annual_spend_band": vendor.annual_spend_band,
            "access_to_client_systems_or_data": vendor.access_to_client_systems_or_data,
            "processes_personal_data": vendor.processes_personal_data,
            "delivery_countries": vendor.delivery_countries, "uses_subcontractors": vendor.uses_subcontractors,
            "supplier_declaration_accepted": vendor.supplier_declaration_accepted,
        },
        "current_risk": risk,
        "assessment_summary": {
            "label": risk.get("assessment_label"),
            "is_provisional": risk.get("is_provisional", True),
            "data_confidence": risk.get("data_confidence"),
            "human_review_required": review_required,
            "blocked_by_status": blocked,
        },
        "executive_summary": {
            "overall_status": risk.get("overall_status"),
            "score": risk.get("overall_score"),
            "grade": risk.get("overall_grade"),
            "trend": risk.get("trend"),
            "open_alert_count": len(alerts),
            "earliest_sla_due_at": min((alert.sla_due_at for alert in alerts), default=None).isoformat() if alerts else None,
            "data_freshness_state": "review" if any(row.status != "healthy" for row in sources) else "healthy",
        },
        "material_events": [{
            "event_id": event.id, "source": event.source, "event_type": event.event_type,
            "category": event.category, "title": event.title, "description": event.description,
            "severity": event.severity, "confidence": event.confidence,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "detected_at": event.detected_at.isoformat(), "source_url": event.source_url,
            "requires_caution": event.source == "news",
            "caution": "External news/RSS content may be incomplete, inaccurate, or unverified. Human review is required before any business decision." if event.source == "news" else None,
            "review": review_by_event.get(event.id), "evidence": evidence_by_event.get(event.id, []),
        } for event in events],
        "open_alerts": [serialize_alert(alert) for alert in alerts],
        "source_health": [{
            "source": row.source, "status": row.status,
            "last_successful_sync": row.last_successful_sync.isoformat() if row.last_successful_sync else None,
            "last_attempted_sync": row.last_attempted_sync.isoformat() if row.last_attempted_sync else None,
            "source_timestamp": row.source_timestamp.isoformat() if row.source_timestamp else None,
            "last_error": row.last_error, "metadata": row.metadata_json,
        } for row in sources],
        "financial_filing_evidence": [{
            "evidence_id": page.id, "page_number": page.page_number,
            "captured_at": page.captured_at.isoformat(),
            "dashboard_image_url": f"/dashboard/evidence/{page.id}/image",
        } for page in filing_pages],
        "financial_review": financial_review_summary(db, vendor.id, latest.id) if latest else {"status": "unavailable", "label": "Financial data unavailable", "page_count": 0},
        "data_freshness": freshness_summary(db, vendor.id),
        "recommended_review_areas": _recommendations(risk),
        "disclaimer": "This report is decision-support information based on available source data. It is not a legal, sanctions, creditworthiness, or financial-failure determination. Human review is required before adverse action.",
    }


def save_report_snapshot(db, vendor, generated_by: str):
    latest = db.query(ComplianceSnapshot).filter(ComplianceSnapshot.vendor_id == vendor.id).order_by(ComplianceSnapshot.checked_at.desc()).first()
    row = AuditReportSnapshot(vendor_id=vendor.id, snapshot_id=latest.id if latest else None, report_version=REPORT_VERSION, generated_by=(generated_by or "system")[:200], payload=build_current_report(db, vendor))
    db.add(row)
    db.flush()
    return row


def serialize_report_snapshot(row, include_payload=False):
    result = {"report_id": row.id, "vendor_id": row.vendor_id, "snapshot_id": row.snapshot_id, "report_version": row.report_version, "generated_by": row.generated_by, "created_at": row.created_at.isoformat()}
    if include_payload:
        result["payload"] = row.payload
    return result


def _recommendations(risk):
    result = []
    for name, dimension in risk.get("dimensions", {}).items():
        if dimension["state"] == "UNKNOWN":
            result.append(f"Obtain or validate additional {name} evidence.")
        elif dimension["state"] == "REVIEW_REQUIRED":
            result.append(f"Review the {name} risk evidence and record a decision.")
        elif dimension["state"] == "WATCH":
            result.append(f"Monitor {name} signals at the next scheduled review.")
    return list(dict.fromkeys(result))

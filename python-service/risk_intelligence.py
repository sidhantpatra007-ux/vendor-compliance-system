from datetime import datetime, timedelta

from models import (
    Alert, AuditLog, ComplianceSnapshot, EventReviewDecision,
    RiskDimensionSnapshot, RiskEvent, ScoreChangeRecord,
)

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
VALID_REVIEW_DECISIONS = {"confirmed_match", "false_positive", "needs_information", "dismissed"}
CRITICALITY_POLICY = {
    "low": (None, 72),
    "medium": (None, 48),
    "high": ("high", 24),
    "critical": ("critical", 4),
}


def alert_policy(criticality: str | None, base_severity: str) -> tuple[str, int]:
    minimum, hours = CRITICALITY_POLICY.get((criticality or "medium").strip().lower(), CRITICALITY_POLICY["medium"])
    severity = (base_severity or "medium").lower()
    if minimum and SEVERITY_ORDER[minimum] > SEVERITY_ORDER.get(severity, 2):
        severity = minimum
    return severity, hours


def calculate_trend(snapshots) -> str:
    if len(snapshots) < 3:
        return "INSUFFICIENT_DATA"
    ordered = sorted(snapshots, key=lambda row: row.checked_at)
    delta = ordered[-1].composite_score - ordered[0].composite_score
    if delta <= -5:
        return "DETERIORATING"
    if delta >= 5:
        return "IMPROVING"
    return "STABLE"


def _latest_decisions(db, vendor_id: int):
    rows = db.query(EventReviewDecision).join(RiskEvent).filter(
        RiskEvent.vendor_id == vendor_id
    ).order_by(EventReviewDecision.created_at.desc(), EventReviewDecision.id.desc()).all()
    latest = {}
    for row in rows:
        latest.setdefault(row.event_id, row)
    return latest


def _from_categories(categories, names, label):
    rows = [categories[name] for name in names if name in categories]
    if not rows:
        return {"state": "UNKNOWN", "score": None, "confidence": "LOW", "summary": f"{label} risk rules are not configured yet.", "details": {}}
    unscored = all(row.get("unscored", False) for row in rows)
    scores = [row.get("score") for row in rows if row.get("score") is not None]
    factors = [factor for row in rows for factor in row.get("factors", [])]
    severities = {factor.get("severity", "low").lower() for factor in factors}
    score = min(scores) if scores else None
    if unscored:
        state, summary, confidence = "UNKNOWN", f"{label} could not be assessed from validated data.", "LOW"
    elif "critical" in severities or "high" in severities:
        state, summary, confidence = "REVIEW_REQUIRED", f"Material {label.lower()} signals require review.", "MEDIUM"
    elif factors or (score is not None and score < 90):
        state, summary, confidence = "WATCH", f"{label} signals should be monitored.", "MEDIUM"
    else:
        state, summary, confidence = "CLEAR", f"No material {label.lower()} risk signal was detected.", "MEDIUM"
    return {"state": state, "score": score, "confidence": confidence, "summary": summary, "details": {"categories": names, "factors": factors, "unscored": unscored}}


def build_dimension_data(db, vendor, snapshot):
    categories = snapshot.categories or {}
    signals = snapshot.signals or {}
    decisions = _latest_decisions(db, vendor.id)
    confirmed = (
        any(row.decision == "confirmed_match" for row in decisions.values())
        or signals.get("sanctions_match_state") == "CONFIRMED_MATCH"
        or signals.get("director_or_psc_sanctions_match_state") == "CONFIRMED_MATCH"
    )
    result = {
        "corporate": _from_categories(categories, ["registration_validity", "filing_compliance", "governance_stability"], "Corporate"),
        "financial": _from_categories(categories, ["financial_health", "insolvency_risk"], "Financial"),
        "ownership": _ownership_dimension(signals),
        "reputation": {"state": "UNKNOWN", "score": None, "confidence": "LOW", "summary": "No reputation/news provider is configured.", "details": {}},
        "cyber": {"state": "UNKNOWN", "score": None, "confidence": "LOW", "summary": "No cyber-risk provider is configured.", "details": {}},
    }
    sanctions_state = signals.get("sanctions_match_state", "NO_MATCH")
    if confirmed:
        compliance = {"state": "BLOCKED", "score": 100, "confidence": "HIGH", "summary": "A reviewer confirmed a compliance screening match. Supplier status is blocked pending the client's escalation process; the numeric score is intentionally unchanged.", "details": {"manual_confirmation": True}}
    elif not signals.get("sanctions_screening_available", True):
        compliance = {"state": "UNKNOWN", "score": None, "confidence": "LOW", "summary": "Sanctions screening was unavailable.", "details": {}}
    elif sanctions_state in {"POSSIBLE_MATCH", "REVIEW_REQUIRED"}:
        compliance = {"state": "REVIEW_REQUIRED", "score": 100, "confidence": "MEDIUM", "summary": "A sanctions name-screening candidate requires human review. This is not a confirmed sanctions finding.", "details": {"screening_state": sanctions_state, "candidate_name": signals.get("sanctions_candidate_name"), "match_score": signals.get("sanctions_match_score")}}
    else:
        compliance = {"state": "CLEAR", "score": 100, "confidence": "HIGH", "summary": "No sanctions name-screening candidate was identified.", "details": {"screening_state": sanctions_state}}
    result["compliance"] = compliance
    return result


def _ownership_dimension(signals):
    details = {
        "active_psc_count": signals.get("active_psc_count"),
        "active_pscs": signals.get("active_pscs", []),
        "psc_data_available": signals.get("psc_data_available", True),
        "psc_changed_recently": signals.get("psc_changed_recently", False),
        "psc_details_unclear": signals.get("psc_details_unclear", False),
    }
    if not details["psc_data_available"]:
        return {"state": "UNKNOWN", "score": None, "confidence": "LOW", "summary": "PSC data could not be retrieved from Companies House.", "details": details}
    if details["psc_changed_recently"] or details["psc_details_unclear"]:
        return {"state": "WATCH", "score": 94, "confidence": "MEDIUM", "summary": "Ownership data needs a routine review; this is not, by itself, an adverse finding.", "details": details}
    return {"state": "CLEAR", "score": 100, "confidence": "MEDIUM", "summary": "No ownership review signal was detected from available PSC data.", "details": details}


def materialize_risk_analysis(db, vendor, snapshot) -> None:
    if db.query(ScoreChangeRecord).filter(ScoreChangeRecord.snapshot_id == snapshot.id).first():
        return
    history = db.query(ComplianceSnapshot).filter(
        ComplianceSnapshot.vendor_id == vendor.id,
        ComplianceSnapshot.checked_at >= datetime.utcnow() - timedelta(days=90),
    ).order_by(ComplianceSnapshot.checked_at.asc()).all()
    previous = db.query(ComplianceSnapshot).filter(
        ComplianceSnapshot.vendor_id == vendor.id,
        ComplianceSnapshot.id != snapshot.id,
    ).order_by(ComplianceSnapshot.checked_at.desc()).first()
    dimensions = build_dimension_data(db, vendor, snapshot)
    for dimension, data in dimensions.items():
        db.add(RiskDimensionSnapshot(vendor_id=vendor.id, snapshot_id=snapshot.id, dimension=dimension, **data))
    old_codes = {factor.get("code") for factor in (previous.factors or [])} if previous else set()
    new_factors = [factor for factor in (snapshot.factors or []) if factor.get("code") not in old_codes]
    delta = snapshot.composite_score - previous.composite_score if previous else None
    db.add(ScoreChangeRecord(
        vendor_id=vendor.id, snapshot_id=snapshot.id,
        previous_snapshot_id=previous.id if previous else None,
        previous_score=previous.composite_score if previous else None,
        current_score=snapshot.composite_score, score_delta=delta,
        trend=calculate_trend(history),
        explanation={"previous_score": previous.composite_score if previous else None, "current_score": snapshot.composite_score, "score_delta": delta, "new_factors": new_factors, "note": "First recorded assessment." if not previous else "Explanation is generated deterministically from stored factors."},
    ))
    from operations import ensure_financial_review, sync_operational_state
    ensure_financial_review(db, vendor, snapshot)
    sync_operational_state(db, vendor, snapshot)


def get_risk_summary(db, vendor):
    latest = db.query(ComplianceSnapshot).filter(ComplianceSnapshot.vendor_id == vendor.id).order_by(ComplianceSnapshot.checked_at.desc()).first()
    if not latest:
        return {"vendor_id": vendor.id, "status": "NO_ASSESSMENT", "dimensions": {}}
    rows = db.query(RiskDimensionSnapshot).filter(RiskDimensionSnapshot.snapshot_id == latest.id).all()
    dimensions = {row.dimension: {"state": row.state, "score": row.score, "confidence": row.confidence, "summary": row.summary, "details": row.details} for row in rows}
    if not dimensions:
        dimensions = build_dimension_data(db, vendor, latest)
    change = db.query(ScoreChangeRecord).filter(ScoreChangeRecord.snapshot_id == latest.id).first()
    blocked = any(item["state"] == "BLOCKED" for item in dimensions.values())
    review_required = any(item["state"] == "REVIEW_REQUIRED" for item in dimensions.values())
    confidence = (latest.signals or {}).get("data_quality", {}).get("confidence", "unknown")
    provisional = blocked or review_required or confidence.lower() in {"low", "unknown"} or any(item["state"] == "UNKNOWN" for item in dimensions.values())
    status = "BLOCKED" if blocked else "REVIEW_REQUIRED" if review_required else "ASSESSED"
    label = "Blocked by compliance status" if blocked else f"Provisional {latest.risk_grade} / {latest.composite_score}" if provisional else f"Assessed {latest.risk_grade} / {latest.composite_score}"
    return {"vendor_id": vendor.id, "snapshot_id": latest.id, "checked_at": latest.checked_at.isoformat(), "overall_score": latest.composite_score, "overall_grade": latest.risk_grade, "overall_status": status, "assessment_label": label, "is_provisional": provisional, "data_confidence": confidence, "trend": change.trend if change else "INSUFFICIENT_DATA", "score_explanation": change.explanation if change else {}, "dimensions": dimensions}


def save_event_review(db, event, decision: str, reviewer: str, note: str | None):
    decision, reviewer = (decision or "").strip().lower(), (reviewer or "").strip()
    if decision not in VALID_REVIEW_DECISIONS:
        raise ValueError("Unsupported review decision")
    if not reviewer:
        raise ValueError("reviewer is required")
    row = EventReviewDecision(event_id=event.id, decision=decision, reviewer=reviewer[:200], note=(note or "").strip()[:2000] or None)
    db.add(row)
    db.flush()
    db.add(AuditLog(vendor_id=event.vendor_id, action="risk_event_reviewed", actor=row.reviewer, details={"risk_event_id": event.id, "event_type": event.event_type, "decision": row.decision}))
    if decision == "confirmed_match" and event.event_type.startswith("SANCTIONS_"):
        _create_confirmed_match_alert(db, event, row.reviewer)
    return row


def _create_confirmed_match_alert(db, event, reviewer: str) -> None:
    if db.query(Alert).filter(Alert.vendor_id == event.vendor_id, Alert.dedup_key == f"confirmed-sanctions:{event.id}", Alert.status.in_(("open", "acknowledged", "escalated"))).first():
        return
    latest = db.query(ComplianceSnapshot).filter(ComplianceSnapshot.vendor_id == event.vendor_id).order_by(ComplianceSnapshot.checked_at.desc()).first()
    if not latest:
        return
    severity, hours = alert_policy(event.vendor.supplier_criticality, "critical")
    db.add(Alert(vendor_id=event.vendor_id, snapshot_id=latest.id, dedup_key=f"confirmed-sanctions:{event.id}", severity=severity, title=f"Confirmed compliance review: {event.vendor.display_name}", reason="A reviewer confirmed a sanctions screening match. Review evidence and follow the client's escalation process.", evidence=[{"type": "risk_event", "risk_event_id": event.id, "event_type": event.event_type, "source_url": event.source_url, "reviewer": reviewer}], sla_due_at=datetime.utcnow() + timedelta(hours=hours)))

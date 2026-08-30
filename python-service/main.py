from fastapi import FastAPI, Body, Depends, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hmac
import os
import re
import httpx

from database import init_db, SessionLocal
from models import (
    Vendor,
    ComplianceSnapshot,
    FinancialRecord,
    FilingEvidencePage,
    StreamCheckpoint,
    CompanyEvent,
    Alert,
    AuditLog,
    JobRun,
    ReviewerDecision,
)
from config import (
    ALERT_SCORE_DROP_THRESHOLD, ALERT_SLA_HOURS, ALLOWED_ORIGINS,
    COMPANIES_HOUSE_STREAM_API_KEY, COMPANIES_HOUSE_STREAM_ENABLED,
    INTERNAL_API_KEY, SANCTIONS_SERVICE_URL, AUDIT_RETENTION_DAYS,
    DASHBOARD_PASSWORD, DASHBOARD_SESSION_SECRET, DASHBOARD_COOKIE_SECURE,
)
from companies_house.streaming import CompaniesHouseStreamSupervisor
from compliance.evaluate import evaluate_company
from compliance.scoring import score_from_signals
from compliance.diff import get_last_two_snapshots, diff_snapshots
from compliance.summarize import summarize_diff
# NOTE: db.py (the old raw-sqlite layer) is gone entirely — its two jobs
# (evaluations, client_overrides) are now covered by ComplianceSnapshot
# and FinancialRecord's own client_confirmed_* columns respectively.

app = FastAPI(title="Vendor Compliance Checker")
app.add_middleware(
    SessionMiddleware,
    secret_key=DASHBOARD_SESSION_SECRET or INTERNAL_API_KEY or "local-dashboard-change-me",
    https_only=DASHBOARD_COOKIE_SECURE,
    same_site="lax",
)
_stream_supervisor: CompaniesHouseStreamSupervisor | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["http://localhost:3000"],
    allow_credentials=bool(ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

ALERT_THRESHOLD = ALERT_SCORE_DROP_THRESHOLD

_COMPANY_NUMBER_RE = re.compile(r"^[A-Za-z]{0,2}[0-9]{6,8}$")


def _validate_company_number(company_number: str) -> str:
    if not company_number or not _COMPANY_NUMBER_RE.match(company_number):
        raise HTTPException(
            400,
            f"'{company_number}' is not a valid UK company number "
            "(expected 8 characters: digits, optionally with a 2-letter prefix)",
        )
    return company_number.upper()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(x_internal_api_key: str | None = Header(default=None)):
    if INTERNAL_API_KEY and x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(401, "Invalid internal API key")


def require_dashboard_session(request: Request):
    if not DASHBOARD_PASSWORD or not DASHBOARD_SESSION_SECRET:
        raise HTTPException(503, "Dashboard login is not configured")
    if not request.session.get("dashboard_authenticated"):
        raise HTTPException(401, "Dashboard login required")


@app.on_event("startup")
def on_startup():
    init_db()
    _start_companies_house_streams()


@app.on_event("shutdown")
def on_shutdown():
    global _stream_supervisor
    if _stream_supervisor:
        _stream_supervisor.stop()
        _stream_supervisor = None


@app.get("/")
def health_check():
    try:
        response = httpx.get(f"{SANCTIONS_SERVICE_URL.rstrip('/')}/", timeout=3.0)
        response.raise_for_status()
        sanctions = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        sanctions = {"status": "unavailable", "error": str(exc)}
    return {
        "status": "ok" if sanctions.get("status") != "unavailable" else "degraded",
        "service": "vendor-compliance-checker",
        "companies_house_streaming": {
            "enabled": COMPANIES_HOUSE_STREAM_ENABLED,
            "streams": _stream_supervisor.status() if _stream_supervisor else {},
        },
        "sanctions_service": sanctions,
    }


# ---------- Internal helpers ----------

_STREAM_COMPANY_NUMBER_RE = re.compile(r"/company/([A-Za-z0-9]+)(?:/|$)")


def _company_number_from_stream_event(event: dict) -> str | None:
    """Extract the Companies House company number from any supported stream."""
    data = event.get("data") or {}
    for key in ("company_number", "company_id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value.upper()

    resource_uri = event.get("resource_uri") or ""
    match = _STREAM_COMPANY_NUMBER_RE.search(resource_uri)
    return match.group(1).upper() if match else None


def _parse_stream_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _get_stream_checkpoint(stream_name: str) -> int | None:
    db = SessionLocal()
    try:
        row = db.query(StreamCheckpoint).filter(StreamCheckpoint.stream_name == stream_name).first()
        return row.timepoint if row else None
    finally:
        db.close()


def _save_stream_checkpoint(stream_name: str, timepoint: int) -> None:
    db = SessionLocal()
    try:
        row = db.query(StreamCheckpoint).filter(StreamCheckpoint.stream_name == stream_name).first()
        if row:
            row.timepoint = timepoint
            row.updated_at = datetime.utcnow()
        else:
            db.add(StreamCheckpoint(stream_name=stream_name, timepoint=timepoint))
        db.commit()
    finally:
        db.close()


def _reset_stream_checkpoint(stream_name: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(StreamCheckpoint).filter(StreamCheckpoint.stream_name == stream_name).first()
        if row:
            row.timepoint = None
            row.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def _process_companies_house_event(stream_name: str, event: dict) -> None:
    """Persist a relevant stream event and refresh every matching vendor.

    Events for companies the client is not monitoring are discarded before a
    database write.  Replayed events are harmless because the event's
    stream/timepoint/vendor tuple is unique.
    """
    timepoint = event.get("event", {}).get("timepoint")
    if not isinstance(timepoint, int):
        raise ValueError("Companies House stream event has no integer timepoint")

    company_number = _company_number_from_stream_event(event)
    if not company_number:
        return

    db = SessionLocal()
    try:
        vendors = db.query(Vendor).filter(Vendor.company_number == company_number).all()
        if not vendors:
            return

        metadata = event.get("event") or {}
        for vendor in vendors:
            company_event = (
                db.query(CompanyEvent)
                .filter(
                    CompanyEvent.stream_name == stream_name,
                    CompanyEvent.timepoint == timepoint,
                    CompanyEvent.vendor_id == vendor.id,
                )
                .first()
            )
            if company_event and company_event.processed_at:
                continue

            if not company_event:
                company_event = CompanyEvent(
                    vendor_id=vendor.id,
                    company_number=company_number,
                    stream_name=stream_name,
                    timepoint=timepoint,
                    event_type=metadata.get("type", "changed"),
                    resource_uri=event.get("resource_uri"),
                    published_at=_parse_stream_datetime(metadata.get("published_at")),
                    payload=event,
                )
                db.add(company_event)
                db.flush()

            # The existing evaluator remains the single source of truth for
            # scores. A stream event simply makes its refresh immediate rather
            # than waiting for n8n's scheduled batch run.
            snapshot = _run_and_save_snapshot(db, vendor)
            pair = get_last_two_snapshots(db, vendor.id)
            if pair and pair[1].id == snapshot.id:
                _open_alert(db, vendor, snapshot, diff_snapshots(*pair))
            company_event.snapshot_id = snapshot.id
            company_event.processed_at = datetime.utcnow()
            company_event.processing_error = None
            db.commit()
    except Exception as exc:
        db.rollback()
        raise RuntimeError(
            f"Could not process Companies House {stream_name} event {timepoint} "
            f"for company {company_number}: {exc}"
        ) from exc
    finally:
        db.close()


def _start_companies_house_streams() -> None:
    global _stream_supervisor
    if not COMPANIES_HOUSE_STREAM_ENABLED or _stream_supervisor:
        return
    _stream_supervisor = CompaniesHouseStreamSupervisor(
        api_key=COMPANIES_HOUSE_STREAM_API_KEY,
        get_checkpoint=_get_stream_checkpoint,
        save_checkpoint=_save_stream_checkpoint,
        reset_checkpoint=_reset_stream_checkpoint,
        handle_event=_process_companies_house_event,
    )
    _stream_supervisor.start()

def _carry_forward_overrides(db: Session, vendor_id: int) -> dict:
    """
    Finds the most recent client_confirmed=True FinancialRecord per concept
    for this vendor, across ALL past snapshots — this is what makes a
    client's correction persist into future refreshes instead of resetting
    every time evaluate_company() re-runs. Returns {concept: FinancialRecord}.
    """
    confirmed = (
        db.query(FinancialRecord)
        .filter(FinancialRecord.vendor_id == vendor_id, FinancialRecord.client_confirmed == True)  # noqa: E712
        .order_by(FinancialRecord.extracted_at.desc())
        .all()
    )
    latest_per_concept = {}
    for record in confirmed:
        if record.concept not in latest_per_concept:
            latest_per_concept[record.concept] = record
    return latest_per_concept


def _add_financial_trends(db: Session, vendor_id: int, result: dict) -> None:
    previous = (
        db.query(ComplianceSnapshot)
        .filter(ComplianceSnapshot.vendor_id == vendor_id)
        .order_by(ComplianceSnapshot.checked_at.desc())
        .first()
    )
    if not previous:
        return
    current = result["signals"]
    prior = previous.signals or {}
    try:
        old_assets = float(prior.get("net_assets_value"))
        new_assets = float(current.get("net_assets_value"))
        if old_assets > 0 and new_assets >= 0:
            current["net_assets_drop_percent"] = max(0.0, (old_assets - new_assets) / old_assets)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    current["consecutive_losses"] = bool(
        current.get("profit_loss_state") == "PRESENT"
        and prior.get("profit_loss_state") == "PRESENT"
        and (current.get("profit_loss_value") or 0) < 0
        and (prior.get("profit_loss_value") or 0) < 0
    )
    result.update(score_from_signals(current))


def _run_and_save_snapshot(db: Session, vendor: Vendor) -> ComplianceSnapshot:
    """
    Runs evaluate_company(), saves the computed score as a ComplianceSnapshot
    row, and saves each extracted financial line item as its own
    FinancialRecord row (not one JSON blob) — carrying forward any past
    client-confirmed override for the same concept. Shared by onboarding,
    single-vendor refresh, and bulk refresh-all.
    """
    result = evaluate_company(vendor.company_number)
    _add_financial_trends(db, vendor.id, result)
    overrides = _carry_forward_overrides(db, vendor.id)

    extracted = result.get("extracted_financial_summary") or {}
    candidates = extracted.get("candidates", {})

    # An override can flip a NIL/AMBIGUOUS concept to effectively resolved —
    # recompute recommend_manual_review accounting for that, same logic the
    # old JSON-blob version used, just against the new per-row shape.
    recommend_manual_review = result.get("recommend_manual_review", False)
    if candidates:
        recommend_manual_review = any(
            candidates[c].get("state") != "PRESENT" and c not in overrides
            for c in candidates
        )

    snapshot = ComplianceSnapshot(
        vendor_id=vendor.id,
        composite_score=result["composite_score"],
        risk_grade=result["risk_grade"],
        scoring_version=result["scoring_version"],
        signals=result["signals"],
        categories=result["categories"],
        factors=result["factors"],
        recommend_manual_review=recommend_manual_review,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    # One FinancialRecord row per extracted concept.
    for concept, data in candidates.items():
        override = overrides.get(concept)
        record = FinancialRecord(
            vendor_id=vendor.id,
            snapshot_id=snapshot.id,
            concept=concept,
            value=override.client_confirmed_value if override else data.get("value"),
            currency=data.get("currency"),
            extraction_method=data.get("source", "unknown"),
            state=data.get("state", "NIL"),
            evidence_image_path=data.get("evidence_saved_path"),
            evidence_page=data.get("page"),
            client_confirmed=bool(override),
            client_confirmed_value=override.client_confirmed_value if override else None,
            client_confirmed_by=override.client_confirmed_by if override else None,
            client_confirmed_at=override.client_confirmed_at if override else None,
            client_note=override.client_note if override else None,
        )
        db.add(record)

    for page in result.get("filing_evidence_pages") or []:
        path = page.get("saved_path")
        number = page.get("page")
        if path and isinstance(number, int):
            db.add(FilingEvidencePage(
                vendor_id=vendor.id,
                snapshot_id=snapshot.id,
                page_number=number,
                image_path=path,
            ))

    # The whole-page reference image (captured even when XBRL succeeded
    # cleanly, so there's always something to visually cross-check) —
    # stored as its own FinancialRecord row rather than a separate column,
    # so it shows up alongside the concept rows in one query.
    reference = result.get("reference_filing_snapshot")
    if reference:
        db.add(FinancialRecord(
            vendor_id=vendor.id,
            snapshot_id=snapshot.id,
            concept="reference_page",
            extraction_method="reference",
            state="PRESENT",
            evidence_image_path=reference.get("saved_path"),
            evidence_page=reference.get("page"),
        ))

    db.commit()
    return snapshot


def _snapshot_to_dict(db: Session, snapshot: ComplianceSnapshot) -> dict:
    records = (
        db.query(FinancialRecord)
        .filter(FinancialRecord.snapshot_id == snapshot.id)
        .all()
    )
    pair = get_last_two_snapshots(db, snapshot.vendor_id)
    changes = diff_snapshots(*pair) if pair and pair[1].id == snapshot.id else None
    data_quality = (snapshot.signals or {}).get("data_quality", {})
    return {
        "snapshot_id": snapshot.id,
        "checked_at": snapshot.checked_at.isoformat(),
        "composite_score": snapshot.composite_score,
        "risk_grade": snapshot.risk_grade,
        "scoring_version": snapshot.scoring_version,
        "signals": snapshot.signals,
        "categories": snapshot.categories,
        "factors": snapshot.factors,
        "recommend_manual_review": snapshot.recommend_manual_review,
        "data_quality": data_quality,
        "changes_since_previous": changes,
        "financial_records": [
            {
                "concept": r.concept,
                "value": float(r.value) if r.value is not None else None,
                "currency": r.currency,
                "extraction_method": r.extraction_method,
                "state": r.state,
                "evidence_image_path": r.evidence_image_path,
                "evidence_page": r.evidence_page,
                "client_confirmed": r.client_confirmed,
                "client_confirmed_value": float(r.client_confirmed_value) if r.client_confirmed_value is not None else None,
                "client_note": r.client_note,
            }
            for r in records
        ],
    }


def _evidence_rows(db: Session, vendor_id: int, snapshot_id: int) -> list[FilingEvidencePage]:
    query = db.query(FilingEvidencePage).filter(FilingEvidencePage.vendor_id == vendor_id)
    if snapshot_id is not None:
        query = query.filter(FilingEvidencePage.snapshot_id == snapshot_id)
    return query.order_by(FilingEvidencePage.page_number.asc()).all()


def _evidence_dict(vendor: Vendor, record: FilingEvidencePage) -> dict:
    return {
        "evidence_id": record.id,
        "concept": "Accounts filing page",
        "state": "AVAILABLE",
        "extraction_method": "source_pdf",
        "page": record.page_number,
        "captured_at": record.captured_at.isoformat(),
        "image_url": f"/dashboard/evidence/{record.id}/image",
        "companies_house_filing_history_url": (
            "https://find-and-update.company-information.service.gov.uk/"
            f"company/{vendor.company_number}/filing-history"
        ),
    }


def _dashboard_snapshot_dict(snapshot: ComplianceSnapshot) -> dict:
    hidden_financial_values = {
        "net_assets_value", "profit_loss_value", "current_ratio_value",
        "net_assets_drop_percent",
    }
    safe_signals = {
        key: value for key, value in (snapshot.signals or {}).items()
        if key not in hidden_financial_values
    }
    return {
        "snapshot_id": snapshot.id,
        "checked_at": snapshot.checked_at.isoformat(),
        "composite_score": snapshot.composite_score,
        "risk_grade": snapshot.risk_grade,
        "scoring_version": snapshot.scoring_version,
        "signals": safe_signals,
        "categories": snapshot.categories,
        "factors": snapshot.factors,
        "recommend_manual_review": snapshot.recommend_manual_review,
        "data_quality": (snapshot.signals or {}).get("data_quality", {}),
    }


def _review_decision_dict(decision: ReviewerDecision) -> dict:
    return {
        "decision_id": decision.id,
        "decision": decision.decision,
        "reviewer": decision.reviewer,
        "note": decision.note,
        "next_review_at": decision.next_review_at.isoformat() if decision.next_review_at else None,
        "created_at": decision.created_at.isoformat(),
    }


def _intake_dict(vendor: Vendor) -> dict:
    return {
        "trading_name": vendor.trading_name,
        "address_street": vendor.address_street,
        "address_city": vendor.address_city,
        "address_postcode": vendor.address_postcode,
        "contact_name": vendor.contact_name,
        "contact_email": vendor.contact_email,
        "contact_phone": vendor.contact_phone,
        "vendor_category": vendor.vendor_category,
        "goods_or_services": vendor.goods_or_services,
        "supplier_criticality": vendor.supplier_criticality,
        "annual_spend_band": vendor.annual_spend_band,
        "access_to_client_systems_or_data": vendor.access_to_client_systems_or_data,
        "processes_personal_data": vendor.processes_personal_data,
        "delivery_countries": vendor.delivery_countries,
        "uses_subcontractors": vendor.uses_subcontractors,
        "supplier_declaration_accepted": vendor.supplier_declaration_accepted,
    }


def _audit(db: Session, action: str, vendor_id: int = None, alert_id: int = None, actor: str = None, details: dict = None):
    db.add(AuditLog(
        action=action,
        vendor_id=vendor_id,
        alert_id=alert_id,
        actor=actor,
        details=details or {},
    ))


def _severity(diff: dict) -> str:
    levels = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    found = [f.get("severity", "low") for f in diff["new_factors"]]
    return max(found, key=lambda value: levels.get(value, 0)) if found else "medium"


def _open_alert(db: Session, vendor: Vendor, snapshot: ComplianceSnapshot, diff: dict) -> tuple[Alert | None, bool]:
    serious = [f for f in diff["new_factors"] if f.get("severity") in {"critical", "high"}]
    alert_signals = [
        change for change in diff.get("changed_signals", [])
        if change["key"] in {"outstanding_charge_count", "has_floating_charge", "recent_resignations"}
        and bool(change.get("after"))
    ]
    if diff["score_delta"] > ALERT_THRESHOLD and not serious and not alert_signals:
        return None, False
    factor_codes = sorted(f.get("code") for f in serious)
    signal_codes = sorted(f"{change['key']}:{change['after']}" for change in alert_signals)
    dedup_key = "|".join(factor_codes + signal_codes) or f"score-drop:{diff['current_score']}"
    existing = (
        db.query(Alert)
        .filter(Alert.vendor_id == vendor.id, Alert.dedup_key == dedup_key, Alert.status.in_(("open", "acknowledged", "escalated")))
        .first()
    )
    if existing:
        return existing, False
    evidence = [{"type": "factor", "code": f.get("code"), "description": f.get("description")} for f in serious]
    evidence.extend({"type": "signal", **change} for change in diff.get("changed_signals", []))
    alert = Alert(
        vendor_id=vendor.id,
        snapshot_id=snapshot.id,
        dedup_key=dedup_key,
        severity=_severity(diff) if serious else "medium",
        title=f"Vendor risk change: {vendor.display_name}",
        reason=summarize_diff(vendor.display_name, diff),
        evidence=evidence,
        sla_due_at=datetime.utcnow() + timedelta(hours=ALERT_SLA_HOURS),
    )
    db.add(alert)
    db.flush()
    _audit(db, "alert_created", vendor.id, alert.id, details={"dedup_key": dedup_key, "severity": alert.severity})
    return alert, True


def _alert_dict(alert: Alert) -> dict:
    return {
        "alert_id": alert.id,
        "vendor_id": alert.vendor_id,
        "snapshot_id": alert.snapshot_id,
        "severity": alert.severity,
        "status": alert.status,
        "title": alert.title,
        "reason": alert.reason,
        "evidence": alert.evidence,
        "assigned_to": alert.assigned_to,
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "sla_due_at": alert.sla_due_at.isoformat(),
        "escalated_at": alert.escalated_at.isoformat() if alert.escalated_at else None,
        "created_at": alert.created_at.isoformat(),
    }


# ---------- Vendor management ----------

@app.post("/vendors")
def add_vendor(
    company_number: str,
    display_name: str,
    trading_name: str = None,
    address_street: str = None,
    address_city: str = None,
    address_postcode: str = None,
    contact_name: str = None,
    contact_email: str = None,
    contact_phone: str = None,
    vendor_category: str = None,
    goods_or_services: str = None,
    supplier_criticality: str = None,
    annual_spend_band: str = None,
    access_to_client_systems_or_data: str = None,
    processes_personal_data: str = None,
    delivery_countries: str = None,
    uses_subcontractors: str = None,
    supplier_declaration_accepted: bool = None,
    db: Session = Depends(get_db),
    auth=Depends(require_api_key),
):
    company_number = _validate_company_number(company_number)

    vendor = Vendor(
        company_number=company_number,
        display_name=display_name,
        trading_name=trading_name,
        address_street=address_street,
        address_city=address_city,
        address_postcode=address_postcode,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        vendor_category=vendor_category,
        goods_or_services=goods_or_services,
        supplier_criticality=supplier_criticality,
        annual_spend_band=annual_spend_band,
        access_to_client_systems_or_data=access_to_client_systems_or_data,
        processes_personal_data=processes_personal_data,
        delivery_countries=delivery_countries,
        uses_subcontractors=uses_subcontractors,
        supplier_declaration_accepted=supplier_declaration_accepted,
    )
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    _audit(db, "vendor_created", vendor_id=vendor.id)
    db.commit()
    return {"vendor_id": vendor.id, "name": vendor.display_name}


@app.get("/vendors")
def list_vendors(db: Session = Depends(get_db), auth=Depends(require_api_key)):
    vendors = db.query(Vendor).all()
    result = []
    for vendor in vendors:
        latest = (
            db.query(ComplianceSnapshot)
            .filter(ComplianceSnapshot.vendor_id == vendor.id)
            .order_by(ComplianceSnapshot.checked_at.desc())
            .first()
        )
        result.append({
            "vendor_id": vendor.id,
            "name": vendor.display_name,
            "company_number": vendor.company_number,
            "latest_score": latest.composite_score if latest else None,
            "latest_grade": latest.risk_grade if latest else None,
            "last_checked": latest.checked_at.isoformat() if latest else None,
            "recommend_manual_review": latest.recommend_manual_review if latest else False,
            "data_confidence": (latest.signals or {}).get("data_quality", {}).get("confidence") if latest else None,
        })
    return result


# ---------- Full vendor dossier (detail view) ----------

@app.get("/vendors/{vendor_id}")
def get_vendor(vendor_id: int, db: Session = Depends(get_db), auth=Depends(require_api_key)):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(404, "Vendor not found")

    snapshots = (
        db.query(ComplianceSnapshot)
        .filter(ComplianceSnapshot.vendor_id == vendor_id)
        .order_by(ComplianceSnapshot.checked_at.asc())
        .all()
    )
    if not snapshots:
        raise HTTPException(400, "No snapshots yet — call /refresh or /onboard first")

    latest = snapshots[-1]

    return {
        "vendor_id": vendor.id,
        "vendor_name": vendor.display_name,
        "company_number": vendor.company_number,
        "intake": _intake_dict(vendor),
        "latest": _snapshot_to_dict(db, latest),
        "score_history": [
            {
                "snapshot_id": s.id,
                "checked_at": s.checked_at.isoformat(),
                "score": s.composite_score,
                "grade": s.risk_grade,
                "confidence": (s.signals or {}).get("data_quality", {}).get("confidence"),
            }
            for s in snapshots
        ],
    }


# ---------- Local dashboard API ----------

@app.get("/dashboard/auth/status")
def dashboard_auth_status(request: Request):
    return {"authenticated": bool(request.session.get("dashboard_authenticated"))}


@app.post("/dashboard/auth/login")
def dashboard_login(request: Request, password: str = Body(embed=True)):
    if not DASHBOARD_PASSWORD or not DASHBOARD_SESSION_SECRET:
        raise HTTPException(503, "Set DASHBOARD_PASSWORD and DASHBOARD_SESSION_SECRET in .env")
    if not hmac.compare_digest(password, DASHBOARD_PASSWORD):
        raise HTTPException(401, "Invalid dashboard password")
    request.session["dashboard_authenticated"] = True
    return {"status": "authenticated"}


@app.post("/dashboard/auth/logout")
def dashboard_logout(request: Request):
    request.session.clear()
    return {"status": "signed_out"}


@app.get("/dashboard/vendors")
def dashboard_vendors(db: Session = Depends(get_db), auth=Depends(require_dashboard_session)):
    return list_vendors(db=db)


@app.get("/dashboard/vendors/{vendor_id}")
def dashboard_vendor_detail(
    vendor_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_dashboard_session),
):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(404, "Vendor not found")
    snapshots = (
        db.query(ComplianceSnapshot)
        .filter(ComplianceSnapshot.vendor_id == vendor_id)
        .order_by(ComplianceSnapshot.checked_at.asc())
        .all()
    )
    if not snapshots:
        raise HTTPException(400, "No snapshots yet — refresh this vendor first")
    latest = snapshots[-1]
    decisions = (
        db.query(ReviewerDecision)
        .filter(ReviewerDecision.vendor_id == vendor_id)
        .order_by(ReviewerDecision.created_at.desc())
        .all()
    )
    return {
        "vendor_id": vendor.id,
        "vendor_name": vendor.display_name,
        "company_number": vendor.company_number,
        "intake": _intake_dict(vendor),
        "latest": _dashboard_snapshot_dict(latest),
        "score_history": [
            {"snapshot_id": s.id, "checked_at": s.checked_at.isoformat(), "score": s.composite_score, "grade": s.risk_grade}
            for s in snapshots[-5:]
        ],
        "financial_evidence": [_evidence_dict(vendor, row) for row in _evidence_rows(db, vendor_id, latest.id)],
        "review_decisions": [_review_decision_dict(item) for item in decisions],
        "alerts": [_alert_dict(item) for item in db.query(Alert).filter(Alert.vendor_id == vendor_id).order_by(Alert.created_at.desc()).all()],
        "audit_events": [
            {"id": item.id, "action": item.action, "actor": item.actor, "alert_id": item.alert_id, "details": item.details, "created_at": item.created_at.isoformat()}
            for item in db.query(AuditLog).filter(AuditLog.vendor_id == vendor_id).order_by(AuditLog.created_at.desc()).limit(100).all()
        ],
    }


@app.get("/dashboard/evidence/{evidence_id}/image")
def dashboard_evidence_image(
    evidence_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_dashboard_session),
):
    record = db.query(FilingEvidencePage).filter(FilingEvidencePage.id == evidence_id).first()
    image_location = record.image_path if record else None
    if not image_location:
        raise HTTPException(404, "Evidence image not found")
    root = Path(os.getenv("EVIDENCE_DIR", "/app/evidence")).resolve()
    image_path = Path(image_location).resolve()
    if root not in image_path.parents or not image_path.is_file():
        raise HTTPException(404, "Evidence image is unavailable")
    return FileResponse(image_path, media_type="image/png", filename=image_path.name)


@app.get("/dashboard/vendors/{vendor_id}/review-decisions")
def dashboard_review_decisions(vendor_id: int, db: Session = Depends(get_db), auth=Depends(require_dashboard_session)):
    if not db.query(Vendor.id).filter(Vendor.id == vendor_id).first():
        raise HTTPException(404, "Vendor not found")
    rows = db.query(ReviewerDecision).filter(ReviewerDecision.vendor_id == vendor_id).order_by(ReviewerDecision.created_at.desc()).all()
    return [_review_decision_dict(item) for item in rows]


@app.post("/dashboard/vendors/{vendor_id}/review-decisions")
def save_dashboard_review_decision(
    vendor_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    auth=Depends(require_dashboard_session),
):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(404, "Vendor not found")
    allowed = {"pending_review", "approved", "approved_with_conditions", "needs_information", "rejected"}
    decision = str(payload.get("decision") or "").strip()
    reviewer = str(payload.get("reviewer") or "").strip()
    if decision not in allowed or not reviewer:
        raise HTTPException(400, "decision and reviewer are required")
    next_review_at = None
    if payload.get("next_review_at"):
        try:
            next_review_at = datetime.fromisoformat(str(payload["next_review_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError as exc:
            raise HTTPException(400, "next_review_at must be ISO-8601") from exc
    row = ReviewerDecision(
        vendor_id=vendor_id,
        decision=decision,
        reviewer=reviewer,
        note=str(payload.get("note") or "").strip() or None,
        next_review_at=next_review_at,
    )
    db.add(row)
    _audit(db, "reviewer_decision_saved", vendor_id=vendor_id, actor=reviewer, details={"decision": decision})
    db.commit()
    db.refresh(row)
    return _review_decision_dict(row)


@app.get("/dashboard/alerts")
def dashboard_alerts(status: str = None, db: Session = Depends(get_db), auth=Depends(require_dashboard_session)):
    query = db.query(Alert)
    if status:
        query = query.filter(Alert.status == status)
    return [_alert_dict(alert) for alert in query.order_by(Alert.created_at.desc()).all()]


@app.post("/dashboard/vendors/{vendor_id}/refresh")
def dashboard_refresh_vendor(vendor_id: int, db: Session = Depends(get_db), auth=Depends(require_dashboard_session)):
    return refresh_vendor(vendor_id=vendor_id, db=db)


@app.post("/dashboard/alerts/{alert_id}/status")
def dashboard_update_alert_status(
    alert_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    auth=Depends(require_dashboard_session),
):
    return update_alert_status(
        alert_id=alert_id,
        status=str(payload.get("status") or ""),
        actor=str(payload.get("actor") or "Local reviewer"),
        assigned_to=str(payload.get("assigned_to") or "").strip() or None,
        resolution_note=str(payload.get("resolution_note") or "").strip() or None,
        db=db,
    )


@app.get("/dashboard/audit-logs")
def dashboard_audit_logs(limit: int = 100, db: Session = Depends(get_db), auth=Depends(require_dashboard_session)):
    return list_audit_logs(limit=limit, db=db)


# ---------- Onboarding (Workflow A) ----------

@app.post("/vendors/onboard")
def onboard_vendor(
    company_number: str,
    display_name: str,
    trading_name: str = None,
    address_street: str = None,
    address_city: str = None,
    address_postcode: str = None,
    contact_name: str = None,
    contact_email: str = None,
    contact_phone: str = None,
    vendor_category: str = None,
    goods_or_services: str = None,
    supplier_criticality: str = None,
    annual_spend_band: str = None,
    access_to_client_systems_or_data: str = None,
    processes_personal_data: str = None,
    delivery_countries: str = None,
    uses_subcontractors: str = None,
    supplier_declaration_accepted: bool = None,
    db: Session = Depends(get_db),
    auth=Depends(require_api_key),
):
    company_number = _validate_company_number(company_number)

    existing_vendor = db.query(Vendor).filter(Vendor.company_number == company_number).first()
    if existing_vendor:
        updates = {
            "display_name": display_name, "trading_name": trading_name,
            "address_street": address_street, "address_city": address_city,
            "address_postcode": address_postcode, "contact_name": contact_name,
            "contact_email": contact_email, "contact_phone": contact_phone,
            "vendor_category": vendor_category, "goods_or_services": goods_or_services,
            "supplier_criticality": supplier_criticality, "annual_spend_band": annual_spend_band,
            "access_to_client_systems_or_data": access_to_client_systems_or_data,
            "processes_personal_data": processes_personal_data,
            "delivery_countries": delivery_countries, "uses_subcontractors": uses_subcontractors,
            "supplier_declaration_accepted": supplier_declaration_accepted,
        }
        for field, value in updates.items():
            if value is not None:
                setattr(existing_vendor, field, value)
        snapshot = _run_and_save_snapshot(db, existing_vendor)
        _audit(db, "vendor_rechecked_on_onboarding", existing_vendor.id)
        db.commit()
        return {
            "vendor_id": existing_vendor.id,
            "vendor_name": existing_vendor.display_name,
            "company_number": existing_vendor.company_number,
            "duplicate": True,
            **_snapshot_to_dict(db, snapshot),
        }

    vendor = Vendor(
        company_number=company_number,
        display_name=display_name,
        trading_name=trading_name,
        address_street=address_street,
        address_city=address_city,
        address_postcode=address_postcode,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        vendor_category=vendor_category,
        goods_or_services=goods_or_services,
        supplier_criticality=supplier_criticality,
        annual_spend_band=annual_spend_band,
        access_to_client_systems_or_data=access_to_client_systems_or_data,
        processes_personal_data=processes_personal_data,
        delivery_countries=delivery_countries,
        uses_subcontractors=uses_subcontractors,
        supplier_declaration_accepted=supplier_declaration_accepted,
    )
    db.add(vendor)
    db.commit()
    db.refresh(vendor)

    snapshot = _run_and_save_snapshot(db, vendor)
    _audit(db, "vendor_onboarded", vendor.id)
    db.commit()

    return {
        "vendor_id": vendor.id,
        "vendor_name": vendor.display_name,
        "company_number": vendor.company_number,
        **_snapshot_to_dict(db, snapshot),
    }


# ---------- Scoring & summaries (single vendor) ----------

@app.post("/vendors/{vendor_id}/refresh")
def refresh_vendor(vendor_id: int, db: Session = Depends(get_db), auth=Depends(require_api_key)):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(404, "Vendor not found")

    snapshot = _run_and_save_snapshot(db, vendor)
    pair = get_last_two_snapshots(db, vendor.id)
    alert = None
    if pair and pair[1].id == snapshot.id:
        alert, _ = _open_alert(db, vendor, snapshot, diff_snapshots(*pair))
    _audit(db, "vendor_refreshed", vendor.id)
    db.commit()
    return {"status": "refreshed", "alert": _alert_dict(alert) if alert else None, **_snapshot_to_dict(db, snapshot)}


@app.get("/vendors/{vendor_id}/summary")
def vendor_summary(vendor_id: int, db: Session = Depends(get_db), auth=Depends(require_api_key)):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(404, "Vendor not found")

    latest = (
        db.query(ComplianceSnapshot)
        .filter(ComplianceSnapshot.vendor_id == vendor_id)
        .order_by(ComplianceSnapshot.checked_at.desc())
        .first()
    )
    if not latest:
        raise HTTPException(400, "No snapshots yet — call /refresh first")

    pair = get_last_two_snapshots(db, vendor_id)

    if pair is None:
        return {
            "vendor_name": vendor.display_name,
            "score": latest.composite_score,
            "grade": latest.risk_grade,
            "summary": "This is the first check on record — no prior score to compare against.",
        }

    previous, current = pair
    diff = diff_snapshots(previous, current)
    summary = summarize_diff(vendor.display_name, diff)

    return {
        "vendor_name": vendor.display_name,
        "score": current.composite_score,
        "grade": current.risk_grade,
        "diff": diff,
        "summary": summary,
    }


# ---------- Client overrides (manual review corrections) ----------

@app.post("/vendors/{vendor_id}/override")
def save_override(
    vendor_id: int,
    concept: str,
    value: float,
    confirmed_by: str = None,
    note: str = None,
    db: Session = Depends(get_db),
    auth=Depends(require_api_key),
):
    """
    Saves a client's confirmed/corrected value for one financial concept,
    applied to the LATEST FinancialRecord row for this vendor+concept, and
    carried forward into every future refresh via _carry_forward_overrides()
    above. Never affects composite_score/risk_grade, which are computed
    from real signals only, upstream of any override.
    """
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(404, "Vendor not found")

    latest_record = (
        db.query(FinancialRecord)
        .filter(FinancialRecord.vendor_id == vendor_id, FinancialRecord.concept == concept)
        .order_by(FinancialRecord.extracted_at.desc())
        .first()
    )
    if not latest_record:
        raise HTTPException(404, f"No financial record found for concept '{concept}' on this vendor")

    latest_record.client_confirmed = True
    latest_record.client_confirmed_value = value
    latest_record.client_confirmed_by = confirmed_by
    latest_record.client_confirmed_at = datetime.utcnow()
    latest_record.client_note = note
    _audit(db, "financial_override_saved", vendor.id, actor=confirmed_by, details={"concept": concept})
    db.commit()

    return {"status": "saved", "vendor_id": vendor_id, "concept": concept, "value": value}


# ---------- Scheduled watch (Workflow B) ----------

@app.post("/refresh-all")
def refresh_all_vendors(db: Session = Depends(get_db), auth=Depends(require_api_key)):
    vendors = db.query(Vendor).all()
    alerts = []
    failures = []
    run = JobRun(job_type="refresh_all", status="running", items_total=len(vendors))
    db.add(run)
    db.commit()

    for vendor in vendors:
        try:
            snapshot = _run_and_save_snapshot(db, vendor)
            pair = get_last_two_snapshots(db, vendor.id)
            if pair and pair[1].id == snapshot.id:
                alert, created = _open_alert(db, vendor, snapshot, diff_snapshots(*pair))
                if created:
                    alerts.append(_alert_dict(alert))
            run.items_succeeded += 1
            db.commit()
        except Exception as exc:
            db.rollback()
            run = db.query(JobRun).filter(JobRun.id == run.id).one()
            run.items_failed += 1
            failures.append({"vendor_id": vendor.id, "error": str(exc)[:300]})
            db.commit()

    run = db.query(JobRun).filter(JobRun.id == run.id).one()
    run.status = "completed" if not failures else "completed_with_errors"
    run.finished_at = datetime.utcnow()
    run.error = "; ".join(item["error"] for item in failures)[:2000] or None
    _audit(db, "refresh_all_completed", details={"job_run_id": run.id, "failures": len(failures)})
    db.commit()
    return {"job_run_id": run.id, "vendors_checked": len(vendors), "alerts": alerts, "failures": failures}


@app.get("/alerts")
def list_alerts(status: str = None, db: Session = Depends(get_db), auth=Depends(require_api_key)):
    query = db.query(Alert)
    if status:
        query = query.filter(Alert.status == status)
    return [_alert_dict(alert) for alert in query.order_by(Alert.created_at.desc()).all()]


@app.post("/alerts/{alert_id}/status")
def update_alert_status(
    alert_id: int,
    status: str,
    actor: str,
    assigned_to: str = None,
    resolution_note: str = None,
    db: Session = Depends(get_db),
    auth=Depends(require_api_key),
):
    allowed = {"acknowledged", "resolved", "false_positive", "escalated"}
    if status not in allowed:
        raise HTTPException(400, "status must be acknowledged, resolved, false_positive, or escalated")
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(404, "Alert not found")
    if alert.status in {"resolved", "false_positive"}:
        raise HTTPException(409, "Closed alerts cannot be changed")
    if status in {"resolved", "false_positive"} and not resolution_note:
        raise HTTPException(400, "resolution_note is required when closing an alert")
    alert.status = status
    alert.assigned_to = assigned_to or alert.assigned_to
    if status == "acknowledged":
        alert.acknowledged_at = datetime.utcnow()
    if status in {"resolved", "false_positive"}:
        alert.resolved_at = datetime.utcnow()
        alert.resolution_note = resolution_note
    if status == "escalated":
        alert.escalated_at = datetime.utcnow()
    _audit(db, "alert_status_changed", alert.vendor_id, alert.id, actor, {"status": status})
    db.commit()
    db.refresh(alert)
    return _alert_dict(alert)


@app.post("/alerts/escalate-overdue")
def escalate_overdue_alerts(db: Session = Depends(get_db), auth=Depends(require_api_key)):
    now = datetime.utcnow()
    alerts = db.query(Alert).filter(Alert.status.in_(("open", "acknowledged")), Alert.sla_due_at <= now).all()
    for alert in alerts:
        alert.status = "escalated"
        alert.escalated_at = now
        _audit(db, "alert_escalated", alert.vendor_id, alert.id, details={"reason": "sla_overdue"})
    db.commit()
    return {"escalated_alerts": [_alert_dict(alert) for alert in alerts]}


@app.get("/audit-logs")
def list_audit_logs(limit: int = 100, db: Session = Depends(get_db), auth=Depends(require_api_key)):
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit must be between 1 and 500")
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [{"id": log.id, "action": log.action, "actor": log.actor, "vendor_id": log.vendor_id, "alert_id": log.alert_id, "details": log.details, "created_at": log.created_at.isoformat()} for log in logs]


@app.get("/retention-policy")
def retention_policy(auth=Depends(require_api_key)):
    return {"audit_retention_days": AUDIT_RETENTION_DAYS, "notice": "Automated screening requires human review before adverse action."}

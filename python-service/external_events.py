"""Safe intake boundary for n8n Gazette and news/RSS workflows."""
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

from event_store import persist_event, record_source_sync, serialize_event
from models import ComplianceSnapshot
from operations import _open_or_update_alert
from source_adapters.base import EvidenceInput, NormalizedEvent

ALLOWED_SOURCES = {"gazette", "news"}
ALLOWED_SEVERITIES = {"info", "low", "medium", "high", "critical"}


def _text(value, field, required=False, limit=2000):
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{field} is required")
    return text[:limit] or None


def _timestamp(value, field):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _url(value):
    if not value:
        return None
    url = str(value).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source_url must be a valid http(s) URL")
    return url[:4000]


def ingest_external_event(db, vendor, payload):
    source = _text(payload.get("source"), "source", required=True, limit=50).lower()
    if source not in ALLOWED_SOURCES:
        raise ValueError("source must be gazette or news")
    title = _text(payload.get("title"), "title", required=True, limit=500)
    source_url = _url(payload.get("source_url"))
    if not source_url:
        raise ValueError("source_url is required")
    severity = (_text(payload.get("severity", "medium"), "severity", limit=20) or "medium").lower()
    if severity not in ALLOWED_SEVERITIES:
        raise ValueError("severity must be info, low, medium, high, or critical")
    occurred_at = _timestamp(payload.get("occurred_at"), "occurred_at")
    event_id = _text(payload.get("source_event_id"), "source_event_id", limit=500)
    if not event_id:
        event_id = hashlib.sha256(f"{source}|{source_url}|{title}|{occurred_at}".encode()).hexdigest()
    description = _text(payload.get("description"), "description", limit=4000)
    caution = "External news/RSS content may be incomplete, inaccurate, or unverified. Human review is required before any business decision."
    if source == "news":
        description = f"{description or 'External news/RSS mention.'}\n\nCaution: {caution}"
    event = NormalizedEvent(
        source=source,
        source_event_id=event_id,
        event_type="GAZETTE_NOTICE" if source == "gazette" else "NEWS_MENTION",
        category="CORPORATE" if source == "gazette" else "REPUTATION",
        title=title,
        description=description,
        occurred_at=occurred_at,
        source_timestamp=occurred_at,
        source_url=source_url,
        confidence="MEDIUM" if source == "gazette" else "LOW",
        source_reliability="AUTHORITATIVE" if source == "gazette" else "EXTERNAL",
        severity=severity.upper(),
        raw_payload={"ingested_by": "n8n", "payload": payload},
        normalized_payload={"source": source, "title": title, "source_url": source_url, "caution": caution if source == "news" else None},
        evidence=(EvidenceInput(source=source, source_url=source_url, source_record_id=event_id, occurred_at=occurred_at),),
    )
    row, created = persist_event(db, vendor.id, event)
    record_source_sync(db, vendor.id, source, success=True, source_timestamp=occurred_at, metadata={"last_event_id": event_id})
    alert = None
    criticality = (vendor.supplier_criticality or "").strip().lower()
    if created and source == "news" and criticality in {"high", "critical"}:
        snapshot = db.query(ComplianceSnapshot).filter(ComplianceSnapshot.vendor_id == vendor.id).order_by(ComplianceSnapshot.checked_at.desc()).first()
        if snapshot:
            alert, _ = _open_or_update_alert(
                db, vendor, snapshot, f"news-review:{row.id}", severity,
                f"External news review: {vendor.display_name}",
                f"External news/RSS content requires review. {caution}",
                [{"type": "risk_event", "risk_event_id": row.id, "event_type": row.event_type, "source_url": source_url}],
            )
    return {"created": created, "event": serialize_event(row), "alert_id": alert.id if alert else None, "caution": caution if source == "news" else None}

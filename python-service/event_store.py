import hashlib
import json
from datetime import datetime

from models import EventEvidence, RiskEvent, SourceSyncState


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def persist_event(db, vendor_id: int, event) -> tuple[RiskEvent, bool]:
    stable_id = event.source_event_id or _hash(event.normalized_payload)
    fingerprint = _hash({
        "vendor_id": vendor_id,
        "source": event.source,
        "source_event_id": stable_id,
        "event_type": event.event_type,
    })
    existing = db.query(RiskEvent).filter(
        RiskEvent.vendor_id == vendor_id,
        RiskEvent.source == event.source,
        RiskEvent.fingerprint == fingerprint,
    ).first()
    if existing:
        return existing, False

    row = RiskEvent(
        vendor_id=vendor_id,
        source=event.source,
        source_event_id=event.source_event_id,
        event_type=event.event_type,
        category=event.category,
        occurred_at=event.occurred_at,
        title=event.title,
        description=event.description,
        raw_payload=event.raw_payload,
        normalized_payload=event.normalized_payload,
        source_url=event.source_url,
        source_timestamp=event.source_timestamp,
        confidence=event.confidence,
        source_reliability=event.source_reliability,
        severity=event.severity,
        fingerprint=fingerprint,
    )
    db.add(row)
    db.flush()
    for item in event.evidence:
        metadata = item.metadata or {}
        db.add(EventEvidence(
            event_id=row.id,
            source=item.source,
            source_url=item.source_url,
            source_record_id=item.source_record_id,
            occurred_at=item.occurred_at,
            source_version=item.source_version,
            raw_reference=item.raw_reference,
            content_hash=_hash({"source": item.source, "record": item.source_record_id, "metadata": metadata}),
            metadata_json=metadata,
        ))
    return row, True


def record_source_sync(db, vendor_id: int, source: str, *, success: bool, source_timestamp=None, error=None, metadata=None):
    row = db.query(SourceSyncState).filter(
        SourceSyncState.vendor_id == vendor_id,
        SourceSyncState.source == source,
    ).first()
    if not row:
        row = SourceSyncState(vendor_id=vendor_id, source=source)
        db.add(row)
    now = datetime.utcnow()
    row.last_attempted_sync = now
    row.source_timestamp = source_timestamp or row.source_timestamp
    row.metadata_json = metadata or row.metadata_json or {}
    if success:
        row.status = "healthy"
        row.last_successful_sync = now
        row.last_error = None
    else:
        row.status = "degraded"
        row.last_error = (error or "Unknown source error")[:2000]
    return row


def serialize_event(event: RiskEvent) -> dict:
    return {
        "id": event.id,
        "source": event.source,
        "source_event_id": event.source_event_id,
        "event_type": event.event_type,
        "category": event.category,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "detected_at": event.detected_at.isoformat(),
        "title": event.title,
        "description": event.description,
        "source_url": event.source_url,
        "confidence": event.confidence,
        "source_reliability": event.source_reliability,
        "severity": event.severity,
        "evidence": [{
            "id": item.id,
            "source": item.source,
            "source_url": item.source_url,
            "source_record_id": item.source_record_id,
            "source_version": item.source_version,
            "retrieved_at": item.retrieved_at.isoformat(),
        } for item in event.evidence_items],
    }

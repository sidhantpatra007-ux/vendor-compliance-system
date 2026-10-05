from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class EvidenceInput:
    source: str
    source_url: str | None
    source_record_id: str | None
    source_version: str | None = None
    raw_reference: str | None = None
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedEvent:
    source: str
    source_event_id: str | None
    event_type: str
    category: str
    title: str
    description: str | None
    occurred_at: datetime | None
    source_timestamp: datetime | None
    source_url: str | None
    confidence: str
    source_reliability: str
    severity: str
    raw_payload: dict[str, Any]
    normalized_payload: dict[str, Any]
    evidence: tuple[EvidenceInput, ...] = ()

from datetime import datetime

from source_adapters.base import EvidenceInput, NormalizedEvent


EVENT_MAP = {
    "companies": ("COMPANY_STATUS_CHANGED", "CORPORATE", "MEDIUM"),
    "filings": ("FILING_SUBMITTED", "CORPORATE", "INFO"),
    "charges": ("CHARGE_REGISTERED", "FINANCIAL", "MEDIUM"),
    "insolvency_cases": ("INSOLVENCY_EVENT", "FINANCIAL", "HIGH"),
    "officers": ("OFFICER_CHANGED", "CORPORATE", "LOW"),
    "persons_with_significant_control": ("PSC_CHANGED", "OWNERSHIP", "MEDIUM"),
}


def normalize_stream_event(stream_name: str, payload: dict, detected_at: datetime | None = None) -> NormalizedEvent:
    metadata = payload.get("event") or {}
    event_type, category, severity = EVENT_MAP.get(stream_name, ("CORPORATE_CHANGE_DETECTED", "CORPORATE", "LOW"))
    resource_uri = payload.get("resource_uri")
    timepoint = metadata.get("timepoint")
    occurred_at = detected_at
    value = metadata.get("published_at")
    if isinstance(value, str):
        try:
            occurred_at = datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    event_id = f"{stream_name}:{timepoint}" if timepoint is not None else resource_uri
    url = f"https://api.company-information.service.gov.uk{resource_uri}" if resource_uri else None
    evidence = EvidenceInput("companies_house", url, event_id, raw_reference=resource_uri, occurred_at=occurred_at, metadata={"stream_name": stream_name, "timepoint": timepoint})
    return NormalizedEvent(
        source="companies_house", source_event_id=event_id, event_type=event_type, category=category,
        title=event_type.replace("_", " ").title(), description=f"Companies House {stream_name} stream event.",
        occurred_at=occurred_at, source_timestamp=occurred_at, source_url=url, confidence="HIGH",
        source_reliability="PRIMARY", severity=severity, raw_payload=payload,
        normalized_payload={"stream_name": stream_name, "upstream_event_type": metadata.get("type")}, evidence=(evidence,),
    )

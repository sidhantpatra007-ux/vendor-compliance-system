from datetime import datetime

from source_adapters.base import EvidenceInput, NormalizedEvent


def _time(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def normalize_screening_result(subject_name: str, subject_type: str, result: dict) -> NormalizedEvent | None:
    state = result.get("sanctions_match_state", "NO_MATCH")
    if state not in {"POSSIBLE_MATCH", "REVIEW_REQUIRED", "CONFIRMED_MATCH"}:
        return None
    source_version = result.get("sanctions_list_version")
    candidate_id = result.get("sanctions_candidate_unique_id")
    checked_at = _time(result.get("sanctions_checked_at"))
    severity = "CRITICAL" if state == "CONFIRMED_MATCH" else "HIGH" if state == "REVIEW_REQUIRED" else "MEDIUM"
    data = {
        "subject_name": subject_name,
        "subject_type": subject_type,
        "match_state": state,
        "matched_name": result.get("sanctions_candidate_name"),
        "matched_alias": result.get("sanctions_candidate_alias"),
        "score": result.get("sanctions_match_score"),
        "matching_fields": result.get("sanctions_matching_fields", ["name"]),
    }
    evidence = EvidenceInput(
        "uk_sanctions_list", result.get("sanctions_source_url") or "https://sanctionslist.fcdo.gov.uk/",
        candidate_id, source_version=source_version, raw_reference=result.get("sanctions_candidate_name"),
        occurred_at=checked_at, metadata=data,
    )
    return NormalizedEvent(
        source="uk_sanctions_list", source_event_id=f"{source_version or 'unknown'}:{candidate_id or subject_name}:{state}",
        event_type=f"SANCTIONS_{state}", category="COMPLIANCE", title=f"Sanctions {state.replace('_', ' ').title()}",
        description="Automated name screening requires human review before any adverse decision.",
        occurred_at=checked_at, source_timestamp=_time(result.get("sanctions_list_fetched_at")),
        source_url=evidence.source_url, confidence="HIGH" if state == "CONFIRMED_MATCH" else "MEDIUM",
        source_reliability="AUTHORITATIVE", severity=severity, raw_payload=result, normalized_payload=data, evidence=(evidence,),
    )

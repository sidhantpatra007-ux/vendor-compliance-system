"""Client for the internal, locally cached UK Sanctions List service."""

from datetime import datetime

import httpx

from config import SANCTIONS_SERVICE_URL


def check_sanctions(company_name: str) -> dict:
    return check_sanctions_subjects([{"name": company_name, "subject_type": "company"}])[0]


def check_sanctions_subjects(subjects: list[dict]) -> list[dict]:
    checked_at = datetime.utcnow().isoformat()
    try:
        response = httpx.post(
            f"{SANCTIONS_SERVICE_URL.rstrip('/')}/screen",
            json={
                "subjects": subjects,
                "match_threshold": 90,
                "review_threshold": 80,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return [{
            "sanctions_match": False,
            "sanctions_match_name": None,
            "sanctions_match_score": None,
            "sanctions_match_unique_id": None,
            "sanctions_checked_at": checked_at,
            "sanctions_screening_available": False,
            "sanctions_screening_error": str(exc),
            "sanctions_list_version": None,
            "sanctions_list_fetched_at": None,
            "sanctions_review_required": True,
        } for _ in subjects]

    results = []
    for result in payload.get("results") or []:
        candidate = result.get("candidate") or {}
        results.append({
            "sanctions_match": bool(result.get("matched")),
            "sanctions_match_name": candidate.get("matched_name") if result.get("matched") else None,
            "sanctions_match_score": result.get("score"),
            "sanctions_match_unique_id": candidate.get("unique_id") if result.get("matched") else None,
            "sanctions_checked_at": checked_at,
            "sanctions_screening_available": True,
            "sanctions_screening_error": None,
            "sanctions_list_version": payload.get("list_version"),
            "sanctions_list_fetched_at": payload.get("list_fetched_at"),
            "sanctions_review_required": bool(result.get("review_required")),
        })
    if len(results) != len(subjects):
        return [{
            "sanctions_match": False,
            "sanctions_match_name": None,
            "sanctions_match_score": None,
            "sanctions_match_unique_id": None,
            "sanctions_checked_at": checked_at,
            "sanctions_screening_available": False,
            "sanctions_screening_error": "Sanctions service returned an incomplete result set",
            "sanctions_list_version": payload.get("list_version"),
            "sanctions_list_fetched_at": payload.get("list_fetched_at"),
            "sanctions_review_required": True,
        } for _ in subjects]
    return results

"""
Turns a structured score diff into a plain-English summary using
Gemini. The prompt is deliberately constrained to only the factual
diff data — no open-ended research — so the summary stays grounded
in what actually changed, not speculation.
"""

import logging

from google import genai
from config import GEMINI_API_KEY

LOGGER = logging.getLogger(__name__)
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def summarize_diff(vendor_name: str, diff: dict) -> str:
    fallback = _fallback_summary(vendor_name, diff)

    if not client:
        return fallback

    prompt = f"""Summarize only the supplied vendor-risk facts in 2-4 sentences.
Do not speculate or make legal, sanctions, or insolvency conclusions.

Vendor: {vendor_name}
Previous score: {diff['previous_score']} ({diff['previous_grade']})
Current score: {diff['current_score']} ({diff['current_grade']})
Score change: {diff['score_delta']:+d}

New issues:
{_format_factors(diff['new_factors'])}

Resolved issues:
{_format_factors(diff['resolved_factors'])}

Changed details:
{_format_changed(diff['changed_factors'])}
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        summary = (response.text or "").strip()
        return summary[:2000] if summary else fallback
    except Exception as exc:
        LOGGER.warning("Gemini summary unavailable; using deterministic fallback: %s", exc)
        return fallback


def _fallback_summary(vendor_name: str, diff: dict) -> str:
    delta = diff["score_delta"]
    current = diff["current_score"]
    previous = diff["previous_score"]

    new_factors = diff.get("new_factors") or []
    resolved_factors = diff.get("resolved_factors") or []

    parts = [
        f"{vendor_name}'s score changed from {previous} to {current} ({delta:+d})."
    ]

    if new_factors:
        reasons = "; ".join(
            factor.get("description", factor.get("code", "New risk signal"))
            for factor in new_factors[:3]
        )
        parts.append(f"New signals: {reasons}.")
    elif resolved_factors:
        reasons = "; ".join(
            factor.get("description", factor.get("code", "Resolved risk signal"))
            for factor in resolved_factors[:3]
        )
        parts.append(f"Resolved signals: {reasons}.")
    else:
        parts.append("The change was identified from the latest monitored supplier data.")

    parts.append("Review the linked evidence before making a business decision.")
    return " ".join(parts)


def _format_factors(factors: list[dict]) -> str:
    if not factors:
        return "None"
    return "\n".join(
        f"- {factor['description']} ({factor['severity']} severity)"
        for factor in factors
    )


def _format_changed(changed: list[dict]) -> str:
    if not changed:
        return "None"
    return "\n".join(
        f"- Was: {item['before']} → Now: {item['after']}"
        for item in changed
    )
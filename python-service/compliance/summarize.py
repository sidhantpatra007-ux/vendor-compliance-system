"""
Turns a structured score diff into a plain-English summary using
Gemini. The prompt is deliberately constrained to only the factual
diff data — no open-ended research — so the summary stays grounded
in what actually changed, not speculation.
"""

from google import genai
from config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)


def summarize_diff(vendor_name: str, diff: dict) -> str:
    if diff["score_delta"] == 0 and not diff["new_factors"] and not diff["resolved_factors"]:
        return f"{vendor_name}'s compliance score is unchanged since the last check."

    prompt = f"""You are summarizing a change in a vendor's compliance score for a business owner who is not a legal or financial expert. Be concise (2-4 sentences), factual, and only use the information provided below. Do not speculate beyond what's given.

Vendor: {vendor_name}
Previous score: {diff['previous_score']} (grade {diff['previous_grade']})
Current score: {diff['current_score']} (grade {diff['current_grade']})
Score change: {diff['score_delta']:+d}

New issues detected since last check:
{_format_factors(diff['new_factors'])}

Issues resolved since last check:
{_format_factors(diff['resolved_factors'])}

Details that changed (same issue, different severity):
{_format_changed(diff['changed_factors'])}

Write a short summary explaining what changed and why the score moved."""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text


def _format_factors(factors: list[dict]) -> str:
    if not factors:
        return "None"
    return "\n".join(f"- {f['description']} ({f['points']} points, {f['severity']} severity)" for f in factors)


def _format_changed(changed: list[dict]) -> str:
    if not changed:
        return "None"
    return "\n".join(f"- Was: {c['before']} → Now: {c['after']}" for c in changed)
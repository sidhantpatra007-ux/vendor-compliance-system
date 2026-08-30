"""
test_evaluate.py

Run this inside the python-service container to sanity-check the full
pipeline (Companies House + XBRL/Arelle + sanctions + scoring) against
one real company, before wiring any of it into n8n.

Usage (from inside the container):
    python test_evaluate.py 00000006

(00000006 is Marine and General Mutual Life Assurance Society — an old,
stable company number, good for a first smoke test. Swap in a real
vendor's number once this runs clean.)
"""

import copy
import sys
import json
from compliance.evaluate import evaluate_company


def _redact_base64_for_display(obj):
    if isinstance(obj, dict):
        redacted = {}
        for k, v in obj.items():
            if k == "evidence_image_base64" and isinstance(v, str):
                redacted[k] = f"<base64 image, {len(v)} chars, truncated for display>"
            else:
                redacted[k] = _redact_base64_for_display(v)
        return redacted
    if isinstance(obj, list):
        return [_redact_base64_for_display(item) for item in obj]
    return obj


def main():
    if len(sys.argv) != 2:
        print("Usage: python test_evaluate.py <company_number>")
        sys.exit(1)

    company_number = sys.argv[1]
    print(f"Evaluating {company_number}...\n")

    try:
        result = evaluate_company(company_number)
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        sys.exit(1)

    print(f"Company: {result['company_name']}")
    print(f"Composite score: {result['composite_score']}  (grade {result['risk_grade']})")
    print(f"Sanctions blocked: {result['sanctions_blocked']}")
    if result["blocked"]:
        print(f"⚠ BLOCKED: {result['block_reasons']}")
    if result["unscored_categories"]:
        print(f"⚠ Unscored categories: {result['unscored_categories']}")
    if result.get("recommend_manual_review"):
        print("⚠ recommend_manual_review: True — extracted financial figures need human verification")
    print()

    print("-- Category breakdown --")
    for category, data in result["categories"].items():
        print(f"  {category}: {data['score']}/100 (weight {data['weight']})")
        for f in data["factors"]:
            print(f"    - [{f['severity']}] {f['description']} ({f['points']} pts)")

    print()
    print("-- Raw financial signals (sanity check these look real, not all-ABSENT) --")
    display_signals = result.get("signals_with_unverified_estimates", result["signals"])
    financial_keys = [k for k in result["signals"] if "net_assets" in k or "current_ratio" in k or "profit_loss" in k or "financial_data" in k]
    for k in financial_keys:
        print(f"  {k}: {display_signals.get(k)}")
        unverified_flag = f"{k.rsplit('_', 1)[0]}_unverified" if k.endswith(("_state", "_value")) else None
        # print any backfilled value's warning right under it
        if k.endswith("_value"):
            prefix = k[: -len("_value")]
            if display_signals.get(f"{prefix}_unverified"):
                source = display_signals.get(f"{prefix}_unverified_source")
                page = display_signals.get(f"{prefix}_unverified_page")
                ambiguous = display_signals.get(f"{prefix}_ambiguous")
                note = f"    ⚠ UNVERIFIED — from {source}"
                if page is not None:
                    note += f", page {page}"
                if ambiguous:
                    all_vals = display_signals.get(f"{prefix}_unverified_all_values_on_line")
                    note += f" — multiple values on source line ({all_vals}), year unclear"
                note += ". OCR/PDF scans can misread digits (e.g. 8 vs 6) — verify against the evidence image before trusting this figure."
                print(note)

    print()
    print("-- Sanctions signals --")
    sanctions_keys = [k for k in result["signals"] if "sanctions" in k]
    for k in sanctions_keys:
        print(f"  {k}: {result['signals'][k]}")

    summary = result.get("extracted_financial_summary")
    if summary and summary.get("candidates"):
        print()
        print("-- Extracted financial candidates (UNVERIFIED — client must confirm) --")
        print(f"  ⚠ {summary['warning']}")
        print()
        for concept, c in summary["candidates"].items():
            value_str = f"{c['value']} {c['currency'] or ''}".strip()
            if c["state"] == "AMBIGUOUS_MULTIPLE_VALUES":
                value_str += f"  (multiple values on this line: {c['all_values_on_line']})"
            print(f"  [{c['review_id']}]")
            print(f"    {concept}: {value_str}")
            print(f"    source: {c['source']}, page {c['page']}")
            print(f"    matched on: \"{c['matched_text']}\" in line: \"{c['raw_line']}\"")
            print()
    elif result.get("unscored_categories"):
        print()
        print("-- No extracted financial candidates found (PDF/OCR both came back empty) --")

    print()
    print("Full JSON:")
    display_result = _redact_base64_for_display(copy.deepcopy(result))
    print(json.dumps(display_result, indent=2, default=str))


if __name__ == "__main__":
    main()
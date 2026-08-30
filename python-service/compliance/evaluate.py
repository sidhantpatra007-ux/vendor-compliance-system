import os
import uuid
from datetime import datetime, timezone

from companies_house.client import (
    CompaniesHouseError,
    get_company_profile,
    get_company_officers,
    get_company_pscs,
    get_company_charges,
    get_company_insolvency,
    get_filing_history,
    search_disqualified_officers,
)
from companies_house.parser import (
    parse_company_profile,
    parse_overdue_flags,
    count_recent_resignations,
    parse_active_officers,
    parse_active_pscs,
    parse_charge_signals,
    parse_insolvency_signals,
    parse_recent_late_filing_signal,
)
from companies_house.xbrl import extract_financial_signals
from companies_house.pdf_extract import download_accounts_pdf, extract_candidates_from_pdf
from companies_house.ocr_extract import extract_candidates_via_ocr
from companies_house.evidence import capture_all_pdf_pages
from sanctions.checker import check_sanctions_subjects
from compliance.scoring import score_from_signals
# NOTE: no longer imports db — client overrides used to be applied here,
# before scoring, via the old db.py's client_overrides table. That table
# no longer exists (db.py was removed in the Supabase migration).
# Overrides are now applied downstream in main.py's
# _carry_forward_overrides(), to the DISPLAYED FinancialRecord value only
# — never fed back into scoring, which stays computed from real signals
# only. This function now always returns the raw, unedited extraction.

EXTRACTED_VALUE_WARNING = (
    "Scanned accounts are normal. Only single-value, validated PDF/OCR "
    "candidates may influence financial scoring; ambiguous values remain "
    "evidence for human review."
)

_SIGNAL_TO_CONCEPT = {
    "net_assets": "net_assets",
    "profit_loss": "profit_loss",
}


def _best_candidate_per_concept(pdf_extraction, ocr_extraction):
    candidates = {}

    if pdf_extraction and isinstance(pdf_extraction, dict):
        for concept, data in pdf_extraction.get("concepts", {}).items():
            if data.get("state") in ("PRESENT", "AMBIGUOUS_MULTIPLE_VALUES", "NIL"):
                candidates[concept] = {**data, "concept": concept, "source_tier": "pdf_text"}

    if ocr_extraction and isinstance(ocr_extraction, list):
        for data in ocr_extraction:
            concept = data.get("concept")
            if concept in candidates:
                continue
            if data.get("state") in ("PRESENT", "AMBIGUOUS_MULTIPLE_VALUES", "NIL"):
                candidates[concept] = {**data, "source_tier": "ocr"}

    return candidates


def _candidate_is_usable(candidate: dict) -> bool:
    if candidate.get("state") == "NIL":
        return True
    if candidate.get("state") != "PRESENT" or candidate.get("value") is None:
        return False
    try:
        value = float(candidate["value"])
    except (TypeError, ValueError):
        return False
    if abs(value) > 1_000_000_000_000:
        return False
    confidence = candidate.get("ocr_confidence")
    return candidate.get("source_tier") != "ocr" or (confidence is not None and confidence >= 85)


def _apply_financial_candidates(signals: dict, candidates: dict) -> None:
    signals["financial_evidence_available"] = bool(candidates)
    if signals.get("financial_scoring_available"):
        signals["financial_source"] = "xbrl"
        signals["financial_confidence"] = "high"
        return

    usable = {key: value for key, value in candidates.items() if _candidate_is_usable(value)}
    source = "ocr" if any(v.get("source_tier") == "ocr" for v in usable.values()) else "pdf_text"
    for concept in ("net_assets", "profit_loss"):
        candidate = usable.get(concept)
        if candidate:
            signals[f"{concept}_state"] = candidate["state"]
            signals[f"{concept}_value"] = candidate.get("value")

    assets = usable.get("current_assets")
    liabilities = usable.get("current_liabilities")
    if assets and liabilities and assets.get("state") == liabilities.get("state") == "PRESENT" and liabilities.get("value"):
        signals["current_ratio_state"] = "PRESENT"
        signals["current_ratio_value"] = float(assets["value"]) / float(liabilities["value"])

    axes = sum([
        signals.get("net_assets_state") in {"PRESENT", "NIL", "INFERRED"},
        signals.get("profit_loss_state") in {"PRESENT", "NIL"},
        signals.get("current_ratio_state") == "PRESENT",
    ])
    signals["financial_data_completeness"] = axes / 3
    signals["financial_scoring_available"] = axes > 0
    signals["financial_source"] = source if axes else "none"
    signals["financial_confidence"] = "medium" if axes else "low"


def _capture_reference_snapshot(company_number: str, existing_pdf_path: str = None) -> dict:
    """
    ALWAYS attempts to capture an image of page 1 of the latest accounts
    filing — regardless of whether XBRL succeeded, PDF-text succeeded, or
    OCR was needed. This means a client looking at a clean, fully-verified
    (XBRL-sourced, no review needed) result still sees the actual filing,
    not just a bare score with nothing to visually cross-check.

    Reuses `existing_pdf_path` if the PDF/OCR tiers already downloaded one
    in this same run, to avoid a second network round-trip for the same file.
    """
    downloaded_here = False
    pdf_path = existing_pdf_path

    try:
        if pdf_path is None:
            pdf_path = download_accounts_pdf(company_number)
            downloaded_here = True

        if pdf_path is None:
            return {"available": False, "reason": "No PDF rendition found for the latest accounts filing."}

        evidence = capture_from_pdfplumber_page(company_number, pdf_path, page_num=1)
        if evidence is None:
            return {"available": False, "reason": "Filing PDF found but page 1 could not be rendered."}

        return {
            "available": True,
            "page": 1,
            "image_base64": evidence["image_base64"],
            "saved_path": evidence["saved_path"],
        }
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}
    finally:
        if downloaded_here and pdf_path:
            try:
                os.unlink(pdf_path)
            except OSError:
                pass


def _safe_companies_house_call(call, default: dict = None) -> dict:
    try:
        return call()
    except CompaniesHouseError:
        return default or {}


def _normalise_name(value: str) -> str:
    return " ".join((value or "").upper().replace(",", " ").split())


def _disqualified_officer_names(officers: list[dict]) -> list[str]:
    names = []
    for officer in officers:
        name = officer["name"]
        result = _safe_companies_house_call(lambda: search_disqualified_officers(name))
        if any(_normalise_name(item.get("title")) == _normalise_name(name) for item in result.get("items", [])):
            names.append(name)
    return names


def _data_quality(signals: dict) -> dict:
    issues = []
    if not signals.get("sanctions_screening_available", True):
        issues.append("sanctions_screening_unavailable")
    if not signals.get("financial_scoring_available"):
        issues.append("financial_metrics_unassessed")
    elif signals.get("financial_source") in {"ocr", "pdf_text"}:
        issues.append("financial_metrics_from_document_extraction")
    if signals.get("director_or_psc_sanctions_review_required"):
        issues.append("people_sanctions_review_required")
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sanctions_list_fetched_at": signals.get("sanctions_list_fetched_at"),
        "confidence": "low" if "financial_metrics_unassessed" in issues or "sanctions_screening_unavailable" in issues else "medium" if issues else "high",
        "issues": issues,
    }


def evaluate_company(company_number: str) -> dict:
    profile_raw = get_company_profile(company_number)
    officers_raw = get_company_officers(company_number)
    pscs_raw = _safe_companies_house_call(lambda: get_company_pscs(company_number))
    charges_raw = _safe_companies_house_call(lambda: get_company_charges(company_number))
    insolvency_raw = _safe_companies_house_call(lambda: get_company_insolvency(company_number))
    filing_history_raw = _safe_companies_house_call(lambda: get_filing_history(company_number, items_per_page=100))

    parsed_profile = parse_company_profile(profile_raw)
    overdue_flags = parse_overdue_flags(profile_raw)
    recent_resignations = count_recent_resignations(officers_raw)
    active_officers = parse_active_officers(officers_raw)
    active_pscs = parse_active_pscs(pscs_raw)
    disqualified_names = _disqualified_officer_names(active_officers)

    financial_signals = extract_financial_signals(company_number)
    financial_signals["financial_scoring_available"] = bool(
        financial_signals.get("financial_data_available")
        and financial_signals.get("financial_data_completeness", 0) > 0
    )
    financial_signals["financial_source"] = "xbrl" if financial_signals["financial_scoring_available"] else "none"
    financial_signals["financial_confidence"] = "high" if financial_signals["financial_scoring_available"] else "low"
    screening_subjects = [{"name": parsed_profile["company_name_on_file"], "subject_type": "company"}]
    screening_subjects.extend({"name": person["name"], "subject_type": "person"} for person in active_officers)
    screening_subjects.extend({"name": person["name"], "subject_type": "person"} for person in active_pscs if person["kind"] == "individual-person-with-significant-control")
    screening_results = check_sanctions_subjects(screening_subjects)
    sanctions_signals = screening_results[0]
    people_sanctions_matches = [
        {"name": subject["name"], "subject_type": subject["subject_type"], **result}
        for subject, result in zip(screening_subjects[1:], screening_results[1:])
        if result.get("sanctions_match") or result.get("sanctions_review_required")
    ]

    signals = {
        **overdue_flags,
        **parse_charge_signals(charges_raw),
        **parse_insolvency_signals(insolvency_raw),
        **parse_recent_late_filing_signal(filing_history_raw),
        "company_status": parsed_profile["company_status"],
        "recent_resignations": recent_resignations,
        "registered_office_address": parsed_profile["registered_office_address"],
        "active_officers": active_officers,
        "active_pscs": active_pscs,
        "active_officer_count": len(active_officers),
        "active_psc_count": len(active_pscs),
        "disqualified_officer_names": disqualified_names,
        "has_disqualified_officer": bool(disqualified_names),
        "people_sanctions_matches": people_sanctions_matches,
        "director_or_psc_sanctions_match": any(match.get("sanctions_match") for match in people_sanctions_matches),
        "director_or_psc_sanctions_review_required": any(match.get("sanctions_review_required") for match in people_sanctions_matches),
        **financial_signals,
        **sanctions_signals,
    }
    pdf_extraction = None
    ocr_extraction = None
    pdf_path = None
    evidence_set = uuid.uuid4().hex

    if not financial_signals.get("financial_scoring_available"):
        try:
            pdf_path = download_accounts_pdf(company_number)
            pdf_extraction = extract_candidates_from_pdf(company_number, pdf_path, evidence_set=evidence_set)
        except Exception as e:
            pdf_extraction = {
                "extraction_available": False,
                "extraction_source": "pdf",
                "error": f"{type(e).__name__}: {e}",
            }

        pdf_requires_ocr = (
            pdf_extraction is not None
            and pdf_extraction.get("extraction_available")
            and not any(c.get("state") in {"PRESENT", "NIL"} for c in pdf_extraction.get("concepts", {}).values())
        )

        if pdf_requires_ocr:
            try:
                ocr_extraction = extract_candidates_via_ocr(company_number, pdf_path, evidence_set=evidence_set)
            except Exception as e:
                ocr_extraction = {
                    "extraction_available": False,
                    "extraction_source": "ocr",
                    "error": f"{type(e).__name__}: {e}",
                }

    if pdf_path is None:
        try:
            pdf_path = download_accounts_pdf(company_number)
        except Exception:
            pdf_path = None
    filing_evidence_pages = capture_all_pdf_pages(company_number, pdf_path, evidence_set) if pdf_path else []

    if pdf_path:
        try:
            os.unlink(pdf_path)
        except OSError:
            pass

    def _has_flagged_concepts(extraction) -> bool:
        if not extraction:
            return False
        if isinstance(extraction, dict):
            concepts = extraction.get("concepts", {})
            return any(c.get("needs_manual_review") for c in concepts.values())
        if isinstance(extraction, list):
            return any(c.get("needs_manual_review") for c in extraction)
        return False

    recommend_manual_review = (
        _has_flagged_concepts(pdf_extraction)
        or _has_flagged_concepts(ocr_extraction)
        or sanctions_signals.get("sanctions_review_required", False)
        or signals.get("director_or_psc_sanctions_review_required", False)
    )

    raw_candidates = _best_candidate_per_concept(pdf_extraction, ocr_extraction)
    _apply_financial_candidates(signals, raw_candidates)
    signals["data_quality"] = _data_quality(signals)
    result = score_from_signals(signals)

    signals_with_unverified_estimates = _augment_signals_for_display(signals, raw_candidates)

    extracted_financial_summary = {
        "warning": EXTRACTED_VALUE_WARNING,
        "candidates": {
            concept: {
                "review_id": f"{company_number}:{concept}",
                "value": data.get("value"),
                "currency": data.get("currency"),
                "all_values_on_line": data.get("all_values"),
                "state": data.get("state"),
                "source": data.get("source_tier"),
                "matched_text": data.get("matched_keyword"),
                "raw_line": data.get("raw_line"),
                "page": data.get("page"),
                "evidence_image_base64": data.get("evidence_image_base64"),
                "evidence_saved_path": data.get("evidence_saved_path"),
                "client_edited": data.get("client_edited", False),
                "client_edited_at": data.get("client_edited_at"),
                "client_note": data.get("client_note"),
            }
            for concept, data in raw_candidates.items()
        },
    }

    return {
        "company_number": company_number,
        "company_name": parsed_profile["company_name_on_file"],
        "signals": signals,
        "signals_with_unverified_estimates": signals_with_unverified_estimates,
        "pdf_extraction": pdf_extraction,
        "ocr_extraction": ocr_extraction,
        "extracted_financial_summary": extracted_financial_summary,
        "reference_filing_snapshot": None,
        "filing_evidence_pages": filing_evidence_pages,
        "recommend_manual_review": recommend_manual_review,
        **result,
    }


def _augment_signals_for_display(signals: dict, candidates: dict) -> dict:
    display = dict(signals)

    for signal_prefix, concept_key in _SIGNAL_TO_CONCEPT.items():
        state_key = f"{signal_prefix}_state"
        value_key = f"{signal_prefix}_value"

        if display.get(state_key) != "ABSENT":
            continue

        candidate = candidates.get(concept_key)
        if not candidate or candidate.get("value") is None:
            continue

        display[value_key] = candidate["value"]
        display[state_key] = "ABSENT (unverified estimate shown below)" if not candidate.get("client_edited") else "CLIENT_CONFIRMED"
        display[f"{signal_prefix}_unverified"] = not candidate.get("client_edited", False)
        display[f"{signal_prefix}_unverified_source"] = candidate.get("source_tier")
        display[f"{signal_prefix}_unverified_page"] = candidate.get("page")
        display[f"{signal_prefix}_ambiguous"] = candidate.get("state") == "AMBIGUOUS_MULTIPLE_VALUES"
        if candidate.get("all_values"):
            display[f"{signal_prefix}_unverified_all_values_on_line"] = candidate["all_values"]

    if display.get("current_ratio_state") == "ABSENT":
        ca = candidates.get("current_assets")
        cl = candidates.get("current_liabilities")
        if ca and cl and ca.get("value") is not None and cl.get("value"):
            try:
                ratio = round(ca["value"] / cl["value"], 2)
                display["current_ratio_value"] = ratio
                display["current_ratio_state"] = "ABSENT (unverified estimate shown below)"
                display["current_ratio_unverified"] = True
                display["current_ratio_unverified_source"] = "derived from current_assets / current_liabilities candidates"
                display["current_ratio_ambiguous"] = (
                    ca.get("state") == "AMBIGUOUS_MULTIPLE_VALUES"
                    or cl.get("state") == "AMBIGUOUS_MULTIPLE_VALUES"
                )
            except ZeroDivisionError:
                pass

    return display

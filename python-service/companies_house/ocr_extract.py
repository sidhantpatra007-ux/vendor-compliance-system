"""
companies_house/ocr_extract.py

OCR fallback extraction for scanned Companies House filings — used only
when structured XBRL data is unavailable AND pdf_extract.py's text-layer
approach also found nothing (i.e. the filing looks like a scanned image,
not a text-layer PDF).

Design notes:
  - Reuses CONCEPT_KEYWORDS and amount_parse.py from pdf_extract.py so
    both tiers look for the same concepts with the same currency/locale
    handling, rather than maintaining two separate keyword lists.
  - Processes one page at a time (not the whole PDF into memory at
    once) to avoid OOM on large documents, with MAX_OCR_PAGES as a
    hard cap and progress printed to stderr so a legitimate multi-page
    run doesn't look like a silent hang.
  - Every matched candidate is inherently lower-confidence than the
    PDF-text tier (OCR misreads, no guaranteed line structure) — so
    needs_manual_review is always True here, same as pdf_extract.py,
    and each match carries an evidence image of its source page.

Requires: pdf2image, pytesseract
"""

import sys

from pdf2image import convert_from_path
from pdf2image.pdf2image import pdfinfo_from_path
import pytesseract

from companies_house.pdf_extract import CONCEPT_KEYWORDS, _matches
from companies_house.amount_parse import parse_amounts_in_line

# Companies House scanned filings are typically 3-4 pages. 30 gives
# generous headroom for outliers without letting a malformed or
# unusually large document run unbounded.
MAX_OCR_PAGES = 30
MAX_CANDIDATE_PAGES = 8
SCREEN_DPI = 140
OCR_DPI = 300

_PAGE_MARKERS = (
    "balance sheet", "statement of financial position", "profit and loss",
    "income statement", "current assets", "net assets", "creditors",
    "capital and reserves", "notes to the financial statements",
)

_NIL_RE_WORDS = ("nil", "none")


def _is_nil_remainder(remainder_lower: str) -> bool:
    stripped = remainder_lower.strip()
    if stripped.startswith(("-", "–", "—")):
        return True
    for word in _NIL_RE_WORDS:
        if stripped.startswith(word):
            return True
    return False


def _mean_confidence(data: dict) -> float | None:
    values = []
    for value in data.get("conf", []):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            values.append(parsed)
    return round(sum(values) / len(values), 1) if values else None


def _financial_page_score(text: str) -> int:
    lowered = (text or "").lower()
    marker_hits = sum(marker in lowered for marker in _PAGE_MARKERS)
    concept_hits = sum(
        any(_matches(lowered, keyword) for keyword in keywords)
        for keywords in CONCEPT_KEYWORDS.values()
    )
    return marker_hits * 3 + concept_hits


def _ocr_page_lines(text: str, page_num: int, avg_confidence: float = None, page_score: int = 0) -> list[dict]:
    """
    Scans OCR'd text from a single page for CONCEPT_KEYWORDS matches,
    using the same amount-parsing rules as pdf_extract.py. Returns a
    list of candidate dicts, one per matched (concept, line) pair.
    """
    candidates = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line_lower = line.lower()

        for concept, keywords in CONCEPT_KEYWORDS.items():
            for kw in keywords:
                idx = line_lower.find(kw)
                if idx == -1 or not _matches(line_lower, kw):
                    continue

                remainder_lower = line_lower[idx + len(kw):]
                remainder_original = line[idx + len(kw):]

                if _is_nil_remainder(remainder_lower):
                    state, value, currency, all_values = "NIL", None, None, None
                else:
                    amounts = parse_amounts_in_line(remainder_original)
                    if not amounts:
                        continue  # no usable number — don't record a candidate for this line
                    elif len(amounts) > 1:
                        state = "AMBIGUOUS_MULTIPLE_VALUES"
                        value, currency = amounts[0]["value"], amounts[0]["currency"]
                        all_values = [a["value"] for a in amounts]
                    else:
                        state = "PRESENT"
                        value, currency = amounts[0]["value"], amounts[0]["currency"]
                        all_values = None

                candidates.append({
                    "concept": concept,
                    "state": state,
                    "value": value,
                    "currency": currency,
                    "all_values": all_values,
                    "matched_keyword": kw,
                    "raw_line": line,
                    "page": page_num,
                    "ocr_confidence": avg_confidence,
                    "page_financial_score": page_score,
                    "validation": {
                        "label_matched": True,
                        "single_value": state in {"PRESENT", "NIL"},
                        "financial_page": page_score > 0,
                    },
                    "needs_manual_review": True,
                    "evidence_image_base64": None,
                    "evidence_saved_path": None,
                })
                break  # first matching keyword wins for this concept on this line

    return candidates


def extract_candidates_via_ocr(
    company_number: str,
    pdf_path: str,
    max_pages: int = MAX_OCR_PAGES,
    dpi: int = OCR_DPI,
    evidence_set: str = None,
) -> list[dict]:
    """
    Processes a scanned PDF one page at a time via OCR, extracting
    candidate financial-concept lines. Only rasterizes one page into
    memory at a time; hard page cap as a safety net. Attaches an
    evidence image to every page that produced at least one candidate.
    """
    from companies_house.evidence import capture_from_pil_image

    try:
        total_pages = pdfinfo_from_path(pdf_path)["Pages"]
    except Exception:
        return []
    pages_to_process = min(total_pages, max_pages)
    ranked_pages = []

    for page_num in range(1, pages_to_process + 1):
        try:
            image = convert_from_path(pdf_path, first_page=page_num, last_page=page_num, dpi=SCREEN_DPI)[0]
            score = _financial_page_score(pytesseract.image_to_string(image))
            if score:
                ranked_pages.append((score, page_num))
            del image
        except Exception:
            continue

    selected = sorted(ranked_pages, key=lambda item: (-item[0], item[1]))[:MAX_CANDIDATE_PAGES]
    print(f"[ocr] selected {len(selected)} financial page(s) from {pages_to_process}", file=sys.stderr, flush=True)
    all_candidates = []
    for page_score, page_num in selected:
        try:
            images = convert_from_path(pdf_path, first_page=page_num, last_page=page_num, dpi=dpi)
            if not images:
                continue
            image = images[0]
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            confidence = _mean_confidence(data)
            candidates = _ocr_page_lines(pytesseract.image_to_string(image), page_num, confidence, page_score)
            if candidates:
                evidence = capture_from_pil_image(company_number, image, page_num, evidence_set=evidence_set)
                for candidate in candidates:
                    candidate["evidence_image_base64"] = evidence["image_base64"] if evidence else None
                    candidate["evidence_saved_path"] = evidence["saved_path"] if evidence else None
            all_candidates.extend(candidates)
            del images, image
        except Exception:
            continue
    return all_candidates

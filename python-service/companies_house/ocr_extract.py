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

# 210 DPI balances OCR accuracy against memory/time per page.
OCR_DPI = 210

_NIL_RE_WORDS = ("nil", "none")


def _is_nil_remainder(remainder_lower: str) -> bool:
    stripped = remainder_lower.strip()
    if stripped.startswith(("-", "–", "—")):
        return True
    for word in _NIL_RE_WORDS:
        if stripped.startswith(word):
            return True
    return False


def _ocr_page_lines(text: str, page_num: int, avg_confidence: float = None) -> list[dict]:
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
) -> list[dict]:
    """
    Processes a scanned PDF one page at a time via OCR, extracting
    candidate financial-concept lines. Only rasterizes one page into
    memory at a time; hard page cap as a safety net. Attaches an
    evidence image to every page that produced at least one candidate.
    """
    from companies_house.evidence import capture_from_pil_image

    all_candidates = []

    info = pdfinfo_from_path(pdf_path)
    total_pages = info["Pages"]
    pages_to_process = min(total_pages, max_pages)

    if total_pages > max_pages:
        print(f"[ocr] WARNING: {total_pages} pages found, capping at {max_pages}", file=sys.stderr, flush=True)

    print(f"[ocr] starting OCR pass: {pages_to_process} page(s) at {dpi} DPI", file=sys.stderr, flush=True)

    for page_num in range(1, pages_to_process + 1):
        print(f"[ocr] processing page {page_num}/{pages_to_process}...", file=sys.stderr, flush=True)

        images = convert_from_path(pdf_path, first_page=page_num, last_page=page_num, dpi=dpi)
        if not images:
            print(f"[ocr] page {page_num} produced no image, skipping", file=sys.stderr, flush=True)
            continue

        page_image = images[0]

        try:
            data = pytesseract.image_to_data(page_image, output_type=pytesseract.Output.DICT)
            confidences = [int(c) for c in data.get("conf", []) if c not in ("-1", -1)]
            avg_confidence = round(sum(confidences) / len(confidences), 1) if confidences else None
        except Exception:
            avg_confidence = None

        text = pytesseract.image_to_string(page_image)
        candidates = _ocr_page_lines(text, page_num, avg_confidence)

        if candidates:
            evidence = capture_from_pil_image(company_number, page_image, page_num)
            for c in candidates:
                c["evidence_image_base64"] = evidence["image_base64"] if evidence else None
                c["evidence_saved_path"] = evidence["saved_path"] if evidence else None

        all_candidates.extend(candidates)

        print(
            f"[ocr] page {page_num} done — {len(candidates)} candidate(s), confidence={avg_confidence}",
            file=sys.stderr, flush=True,
        )

        del images, page_image

    print(
        f"[ocr] finished — {pages_to_process} page(s) processed, {len(all_candidates)} total candidate(s)",
        file=sys.stderr, flush=True,
    )

    return all_candidates
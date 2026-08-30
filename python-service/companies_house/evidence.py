"""
companies_house/evidence.py

Captures a visual "evidence" image of the PDF page a financial concept
was extracted from, so a human reviewer (and eventually the client
themselves) can look at the actual filing rather than trust a regex or
OCR match blind.

Every concept pulled from pdf_extract.py or ocr_extract.py is, by this
module's own design intent, unconfirmed until reviewed — see both of
those files' docstrings. This module makes that reviewable in practice:
each flagged concept gets (a) a saved PNG on disk for archival, and
(b) a base64 thumbnail embedded directly in the JSON result, so a
dashboard can display it with no extra file-serving setup required.
"""

import base64
import io
import os
from typing import Optional

EVIDENCE_DIR = os.environ.get("EVIDENCE_DIR", "/app/evidence")

# Thumbnail width used for the base64-embedded copy. Full-resolution
# version stays on disk if a reviewer wants to zoom in on the real file.
THUMBNAIL_MAX_WIDTH = 900


def _company_dir(company_number: str, evidence_set: str = None) -> str:
    path = os.path.join(EVIDENCE_DIR, company_number, evidence_set or "latest")
    os.makedirs(path, exist_ok=True)
    return path


def _pil_to_base64(pil_image, max_width: int = THUMBNAIL_MAX_WIDTH) -> str:
    img = pil_image
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def capture_from_pdfplumber_page(
    company_number: str,
    pdf_path: str,
    page_num: int,
    resolution: int = 150,
    evidence_set: str = None,
) -> Optional[dict]:
    """
    Renders a single page of the source PDF (via pdfplumber, which
    doesn't already have the page as an image in memory — unlike the
    OCR tier) and returns evidence metadata, or None on any failure.
    Never raises — a failed evidence capture should never break scoring.
    """
    import pdfplumber

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return None
            page = pdf.pages[page_num - 1]
            page_image = page.to_image(resolution=resolution)
            pil_image = page_image.original

            out_path = os.path.join(_company_dir(company_number, evidence_set), f"page_{page_num}.png")
            pil_image.save(out_path)

            return {
                "saved_path": out_path,
                "image_base64": _pil_to_base64(pil_image),
                "page": page_num,
            }
    except Exception:
        return None


def capture_from_pil_image(company_number: str, pil_image, page_num: int, evidence_set: str = None) -> Optional[dict]:
    """
    Same as capture_from_pdfplumber_page, but for the OCR tier, which
    already has the page rendered as a PIL image in memory (from
    pdf2image) — no need to re-open and re-render the PDF a second time.
    """
    try:
        out_path = os.path.join(_company_dir(company_number, evidence_set), f"page_{page_num}.png")
        pil_image.save(out_path)

        return {
            "saved_path": out_path,
            "image_base64": _pil_to_base64(pil_image),
            "page": page_num,
        }
    except Exception:
        return None


def capture_all_pdf_pages(
    company_number: str,
    pdf_path: str,
    evidence_set: str,
    max_pages: int = 30,
    resolution: int = 150,
) -> list[dict]:
    """Render every page of the latest accounts PDF for human review."""
    import pdfplumber

    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages[:max_pages], start=1):
                image = page.to_image(resolution=resolution).original
                out_path = os.path.join(_company_dir(company_number, evidence_set), f"page_{page_num}.png")
                image.save(out_path)
                pages.append({"page": page_num, "saved_path": out_path})
    except Exception:
        return []
    return pages

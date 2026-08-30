"""
debug_pdf_text.py

Diagnostic for the PDF extraction path: downloads a company's PDF-only
accounts filing and reports, per page, how much text pdfplumber actually
extracted. This distinguishes three very different failure modes that
all currently show up identically as "everything ABSENT":

  1. Genuinely scanned/image-only pages (near-zero text extracted)
  2. Real text, but none of it matches our keyword list (terminology
     mismatch — e.g. IFRS consolidated statement wording vs. UK SME wording)
  3. Real text, keywords might even match, but numbers are formatted in
     a way our parser doesn't handle (e.g. European "1.234.567,00" style)

Usage:
    python debug_pdf_text.py <company_number> [--full]

Without --full, prints a short preview per page (first 400 chars) plus
character counts. With --full, dumps the entire extracted text for every
page — useful to paste back for review.
"""

import sys
import tempfile
import os

import httpx
import pdfplumber

from companies_house.pdf_extract import get_latest_accounts_pdf_url
from config import COMPANIES_HOUSE_API_KEY


def dump_pdf_text(company_number: str, full: bool = False) -> None:
    pdf_url = get_latest_accounts_pdf_url(company_number)
    if pdf_url is None:
        print(f"No PDF accounts filing found for {company_number}.")
        return

    print(f"Fetching PDF for {company_number}...")
    resp = httpx.get(
        pdf_url,
        auth=(COMPANIES_HOUSE_API_KEY, ""),
        headers={"Accept": "application/pdf"},
        follow_redirects=True,
        timeout=30.0,
    )
    resp.raise_for_status()
    print(f"Downloaded {len(resp.content):,} bytes.\n")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    try:
        with pdfplumber.open(tmp_path) as pdf:
            print(f"{len(pdf.pages)} page(s) total.\n")
            total_chars = 0
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                total_chars += len(text)
                print(f"--- Page {page_num}: {len(text)} characters extracted ---")
                if len(text.strip()) == 0:
                    print("  (EMPTY — this page likely has no text layer, i.e. it's a scanned image)")
                elif full:
                    print(text)
                else:
                    preview = text.strip().replace("\n", " | ")[:400]
                    print(f"  {preview}...")
                print()

            print(f"=== Total extracted: {total_chars:,} characters across {len(pdf.pages)} page(s) ===")
            if total_chars < 200:
                print(">>> This strongly suggests a SCANNED document with no usable text layer.")
                print(">>> pdfplumber-based extraction cannot work here — would need OCR instead.")
            else:
                print(">>> Real text was extracted. If concepts still came back ABSENT, this is a")
                print(">>> terminology and/or number-format mismatch, not a scanning problem.")
                print(">>> Search the text above for how THIS document phrases 'net assets',")
                print(">>> 'total equity', etc. — and check whether numbers use '1.234.567,00'")
                print(">>> (European) instead of '1,234,567.00' (UK/US) formatting.")
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_pdf_text.py <company_number> [--full]")
        sys.exit(1)

    full_mode = "--full" in sys.argv
    dump_pdf_text(sys.argv[1], full=full_mode)
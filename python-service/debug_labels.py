"""
debug_labels.py

Diagnostic script: dumps every distinct fact label from a company's latest
filed accounts (for the latest reporting period) that looks like it could
be a net-assets/equity related line — so you can read the EXACT wording
this filer's software used, instead of guessing more keyword variants.

Usage:
    python debug_labels.py <company_number>

Example (via docker compose):
    docker compose exec python-service python debug_labels.py 04223669
"""

import os
import sys
import tempfile

import httpx
from arelle import Cntlr

from config import COMPANIES_HOUSE_API_KEY
from companies_house.xbrl import get_latest_accounts_document_url

# Loosen or tighten this if you're chasing a different concept
# (e.g. add "turnover"/"revenue"/"sales" to debug turnover instead).
FILTER_WORDS = ("asset", "equity", "fund", "reserve", "capital")


def dump_labels(company_number: str) -> None:
    result = get_latest_accounts_document_url(company_number)
    if result is None:
        print(f"No usable iXBRL/XHTML accounts document found for {company_number}.")
        return

    doc_url, content_type = result
    print(f"Fetching accounts document for {company_number} ({content_type})...")

    resp = httpx.get(
        doc_url,
        auth=(COMPANIES_HOUSE_API_KEY, ""),
        headers={"Accept": content_type},
        follow_redirects=True,
        timeout=30.0,
    )
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    try:
        cntlr = Cntlr.Cntlr(logFileName=None)
        model_xbrl = cntlr.modelManager.load(tmp_path)

        if model_xbrl is None or model_xbrl.facts is None:
            print("Arelle could not parse any facts from this document.")
            return

        period_ends = [c.endDatetime for c in model_xbrl.contexts.values() if c.endDatetime]
        latest_period_end = max(period_ends) if period_ends else None
        if latest_period_end:
            print(f"Latest period end: {latest_period_end}\n")

        seen = set()
        matches = []
        for fact in model_xbrl.facts:
            if fact.concept is None or fact.context is None:
                continue
            if latest_period_end is not None and fact.context.endDatetime != latest_period_end:
                continue

            label = (fact.concept.label(lang="en") or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)

            lower = label.lower()
            if any(word in lower for word in FILTER_WORDS):
                matches.append((label, fact.value))

        if not matches:
            print(f"No labels matched any of {FILTER_WORDS}. "
                  f"Try widening FILTER_WORDS in this script.")
            return

        print(f"Found {len(matches)} matching label(s):\n")
        for label, value in matches:
            print(f"  {label!r:70} -> value={value!r}")

        model_xbrl.close()
    finally:
        os.unlink(tmp_path)


def dump_all_labels(company_number: str) -> None:
    """Same as dump_labels but with no keyword filter — prints EVERY
    distinct tagged fact for the latest period. Use this when you need to
    confirm a concept (e.g. net assets) genuinely isn't tagged at all,
    rather than just being missed by FILTER_WORDS."""
    result = get_latest_accounts_document_url(company_number)
    if result is None:
        print(f"No usable iXBRL/XHTML accounts document found for {company_number}.")
        return

    doc_url, content_type = result
    resp = httpx.get(
        doc_url,
        auth=(COMPANIES_HOUSE_API_KEY, ""),
        headers={"Accept": content_type},
        follow_redirects=True,
        timeout=30.0,
    )
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    try:
        cntlr = Cntlr.Cntlr(logFileName=None)
        model_xbrl = cntlr.modelManager.load(tmp_path)

        if model_xbrl is None or model_xbrl.facts is None:
            print("Arelle could not parse any facts from this document.")
            return

        period_ends = [c.endDatetime for c in model_xbrl.contexts.values() if c.endDatetime]
        latest_period_end = max(period_ends) if period_ends else None
        if latest_period_end:
            print(f"Latest period end: {latest_period_end}\n")

        seen = set()
        rows = []
        for fact in model_xbrl.facts:
            if fact.concept is None or fact.context is None:
                continue
            if latest_period_end is not None and fact.context.endDatetime != latest_period_end:
                continue
            label = (fact.concept.label(lang="en") or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            rows.append((label, fact.value))

        print(f"{len(rows)} distinct tagged fact(s) for the latest period:\n")
        for label, value in rows:
            print(f"  {label!r:70} -> value={value!r}")

        model_xbrl.close()
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Usage: python debug_labels.py <company_number> [--all]")
        sys.exit(1)

    if len(sys.argv) == 3 and sys.argv[2] == "--all":
        dump_all_labels(sys.argv[1])
    else:
        dump_labels(sys.argv[1])
"""
companies_house/xbrl.py

Fetches the latest filed accounts document and extracts financial
concepts via Arelle. Returns a flat signals dict in the same shape
evaluate.py already builds — no nested "facts" object, so it merges
straight into the existing signals dict with **.

Requires: arelle-release, lxml (see dockerfile note below)
"""

import re
import tempfile
import os
from datetime import datetime
from typing import Optional

from arelle import Cntlr
from companies_house.client import _get, CompaniesHouseError

DOCUMENT_API_BASE = "https://document-api.company-information.service.gov.uk"

# Keyword substrings matched against each fact's resolved standard label
# (not raw XML tag names). Sourced from a mix of:
#   - the official FRS 102/105 model accounts and Companies House
#     micro-entity filing guidance (the "textbook" labels), and
#   - actual wording seen in filed accounts and produced by the filing
#     software SMBs/startups actually use in practice (Companies House
#     WebFiling, FreeAgent, TaxCalc, Inform Direct, Easy Digital Filing,
#     VT, Sage, Xero-fed statutory accounts tools, and accountant/forum
#     threads showing real filed figures).
#
# UK GAAP labels stay fairly consistent even when the underlying XBRL
# taxonomy/tag names vary between filing software and standard versions —
# but "consistent" still means several real variants per concept, not one
# canonical phrase. This list deliberately trades some noise for recall,
# while guarding against three known substring traps (see _matches below):
#
#   1. "profit and loss account" is ambiguous — it labels BOTH this
#      year's P&L movement AND the cumulative reserve balance under
#      "Capital and reserves". Left out of profit_loss entirely to avoid
#      misattributing a multi-year reserve as the current period result.
#   2. "current assets" / "current liabilities" are substrings of
#      "non-current assets" / "non-current liabilities" (IFRS/FRS 101
#      style balance sheets that split current vs. long-term). Guarded.
#   3. "current assets" / "current liabilities" are ALSO substrings of
#      "net current assets" / "net current liabilities" — a subtotal
#      (assets minus liabilities), not the total the keyword is meant to
#      find. Matching this would silently substitute a subtotal for a
#      different figure. Guarded.
#
# A previous version of this list also had a bare "profit" match in
# profit_loss, which is even broader than the P&L account trap and would
# match almost anything containing the word — removed in favour of only
# unambiguous current-period phrasing.
CONCEPT_KEYWORDS = {
    "turnover": [
        "turnover", "total turnover", "revenue", "total revenue",
        "gross revenue", "sales", "total sales",
        "revenue from contracts with customers",  # IFRS 15 style, seen from startups using contract-revenue wording
        "fee income",  # common in services/agency micro-entities
    ],
    "net_assets": [
        "net assets", "net liabilities",
        "total net assets", "total net liabilities",
        "net assets/(liabilities)", "net assets (liabilities)",
        "shareholders funds", "shareholders' funds",
        "total shareholders funds", "total shareholders' funds",
        "total equity", "equity attributable to owners of the company",
        "capital and reserves", "total capital and reserves",
        "members funds", "members' funds", "total members' funds",
        "stockholders equity", "stockholders' equity",  # occasionally seen from US-influenced startup filings
    ],
    "current_assets": [
        "current assets", "total current assets",
    ],
    "current_liabilities": [
        "creditors: amounts falling due within one year",
        "creditors falling due within one year",
        "creditors due within one year",
        "trade and other payables falling due within one year",  # IFRS-style phrasing occasionally seen
        "current liabilities", "total current liabilities",
    ],
    "profit_loss": [
        "profit for the financial year", "loss for the financial year",
        "profit for the period", "loss for the period",
        "profit for the year", "loss for the year",
        "profit/(loss) for the year", "profit/(loss) for the period",
        "profit/(loss) for the financial year",
        "net profit for the year", "net loss for the year",
        "profit on ordinary activities after taxation",
        "loss on ordinary activities after taxation",
        "profit after taxation", "loss after taxation",
        "profit after tax", "loss after tax",
        "result for the year", "result for the financial year",
    ],
    # The following are NOT part of the original 3-concept scoring axes.
    # They exist only to support a safe, explicit net_assets fallback: some
    # real filings (confirmed via debug_labels.py against a live company)
    # simply never tag a "net assets"/"shareholders' funds"/"capital and
    # reserves" fact at all — the balance sheet stops at "Total assets less
    # current liabilities". For a company with no long-term creditors, no
    # provisions, and no accruals, that subtotal numerically equals net
    # assets. But it is NOT the same concept, and using it unconditionally
    # would be wrong for any company that DOES have those items sitting
    # below the line. So we only infer net_assets from this subtotal when
    # all three of the below-the-line items are confirmed ABSENT/NIL.
    "total_assets_less_current_liabilities": [
        "total assets less current liabilities",
    ],
    "creditors_due_after_one_year": [
        "creditors: amounts falling due after more than one year",
        "creditors falling due after more than one year",
        "creditors due after one year",
        "creditors due after more than one year",
    ],
    "provisions_for_liabilities": [
        "provisions for liabilities", "provisions for liability",
    ],
    "accruals_deferred_income": [
        "accruals and deferred income", "accruals",
    ],
}

# Keywords that need substring-boundary guarding because a longer, more
# specific phrase legitimately contains them as a substring. Maps the
# bare keyword to the disqualifying prefixes that must NOT immediately
# precede it in the label.
_GUARDED_PREFIXES = {
    "current assets": ("non", "net"),
    "current liabilities": ("non", "net"),
}


def _matches(label: str, keyword: str) -> bool:
    """
    True if `keyword` is present in `label`, unless it's one of the
    guarded short phrases and immediately preceded by a disqualifying
    prefix (e.g. "current assets" inside "non-current assets" or
    "net current assets").
    """
    idx = label.find(keyword)
    if idx == -1:
        return False

    disqualifiers = _GUARDED_PREFIXES.get(keyword)
    if not disqualifiers:
        return True

    prefix = label[:idx]
    # normalise hyphens/extra whitespace so "non-current" and "non current" both work
    prefix_norm = re.sub(r"[-\s]+", " ", prefix).strip()
    for bad in disqualifiers:
        if prefix_norm.endswith(bad):
            return False
    return True


def get_latest_accounts_document_url(company_number: str) -> Optional[tuple[str, str]]:
    """
    Finds the most recent 'accounts' filing and returns (url, content_type)
    for a downloadable iXBRL/XHTML document, or None if nothing usable is
    on file. Companies House filings are available as PDF, XBRL, or
    sometimes neither — PDF is the default if you don't explicitly ask for
    a different format, so the content_type returned here MUST be sent as
    an Accept header on the actual content request, or you silently get
    the PDF back regardless of what's technically available.
    """
    history = _get(
        f"/company/{company_number}/filing-history",
        params={"category": "accounts", "items_per_page": 5},
    )
    items = history.get("items", [])
    if not items:
        return None

    latest = items[0]
    document_metadata_url = latest.get("links", {}).get("document_metadata")
    if not document_metadata_url:
        return None

    doc_id = document_metadata_url.rstrip("/").split("/")[-1]
    metadata = _get_document_metadata(doc_id)

    resources = metadata.get("resources", {})
    for content_type in ("application/xhtml+xml", "text/html"):
        if content_type in resources:
            return f"{DOCUMENT_API_BASE}/document/{doc_id}/content", content_type

    return None  # only PDF (or nothing) available — no structured data to extract


def _get_document_metadata(doc_id: str) -> dict:
    import httpx
    from config import COMPANIES_HOUSE_API_KEY

    url = f"{DOCUMENT_API_BASE}/document/{doc_id}"
    with httpx.Client(auth=(COMPANIES_HOUSE_API_KEY, "")) as client:
        response = client.get(url, timeout=10.0)
    if response.status_code != 200:
        raise CompaniesHouseError(f"Document metadata fetch failed for {doc_id}: {response.status_code}")
    return response.json()


def _infer_net_assets(facts: dict) -> tuple[str, Optional[float]]:
    """
    Falls back to "Total assets less current liabilities" as a stand-in
    for net_assets, but ONLY when it's actually safe: i.e. there's no
    evidence of long-term creditors, provisions, or accruals that would
    make the subtotal diverge from the true net assets figure. Returns
    (state, value) where state is "INFERRED" if the fallback was used,
    otherwise leaves the original ABSENT/NIL/PRESENT state untouched by
    the caller (this function is only invoked when net_assets is ABSENT).

    "Safe" here means each of the three below-the-line concepts is either
    ABSENT (not tagged / not applicable to this filing) or NIL (tagged but
    explicitly zero) — never a nonzero PRESENT value, which would mean the
    subtotal is definitely not equal to net assets.
    """
    subtotal = facts["total_assets_less_current_liabilities"]
    if subtotal["state"] != "PRESENT":
        return "ABSENT", None

    below_the_line = (
        facts["creditors_due_after_one_year"],
        facts["provisions_for_liabilities"],
        facts["accruals_deferred_income"],
    )
    for item in below_the_line:
        if item["state"] == "PRESENT" and item["value"]:
            # a real nonzero long-term liability/provision/accrual exists —
            # the subtotal is NOT net assets, don't infer
            return "ABSENT", None

    return "INFERRED", subtotal["value"]


def extract_financial_signals(company_number: str) -> dict:
    """
    Returns a flat dict to merge into evaluate.py's signals dict:

    {
        "financial_data_available": bool,
        "financial_data_completeness": float,  # 0..1, fraction of 3 concepts found
        "net_assets_state": "PRESENT" | "NIL" | "INFERRED" | "ABSENT",
        "net_assets_value": float | None,
        "net_assets_inferred": bool,  # True if net_assets_value came from the
                                       # "total assets less current liabilities"
                                       # fallback rather than a directly tagged fact
        "current_ratio_state": "PRESENT" | "ABSENT",
        "current_ratio_value": float | None,
        "profit_loss_state": "PRESENT" | "NIL" | "ABSENT",
        "profit_loss_value": float | None,
    }

    Note on "INFERRED": treat this with lower confidence than "PRESENT" in
    any downstream scoring — it's a subtotal standing in for net assets
    because this filer never tagged net assets directly, verified safe
    only insofar as no long-term creditors/provisions/accruals were found
    tagged with a nonzero value. It is not a directly reported fact.
    """
    result = get_latest_accounts_document_url(company_number)
    if result is None:
        return {
            "financial_data_available": False,
            "financial_data_completeness": 0.0,
            "net_assets_state": "ABSENT",
            "net_assets_value": None,
            "net_assets_inferred": False,
            "current_ratio_state": "ABSENT",
            "current_ratio_value": None,
            "profit_loss_state": "ABSENT",
            "profit_loss_value": None,
        }

    doc_url, content_type = result
    facts = _run_arelle(doc_url, content_type)

    net_assets_state = facts["net_assets"]["state"]
    net_assets_value = facts["net_assets"]["value"]
    net_assets_inferred = False

    if net_assets_state == "ABSENT":
        inferred_state, inferred_value = _infer_net_assets(facts)
        if inferred_state == "INFERRED":
            net_assets_state = "INFERRED"
            net_assets_value = inferred_value
            net_assets_inferred = True

    # completeness still counts net_assets as "found" whether it was
    # directly tagged (PRESENT/NIL) or safely inferred — either way we
    # have a usable figure. It stays uncounted only if truly ABSENT.
    axes_found = sum(
        1 for state in (net_assets_state, facts["profit_loss"]["state"])
        if state != "ABSENT"
    )
    if facts["current_assets"]["state"] != "ABSENT" and facts["current_liabilities"]["state"] != "ABSENT":
        axes_found += 1
    completeness = axes_found / 3

    current_ratio_value = None
    current_ratio_state = "ABSENT"
    if facts["current_assets"]["state"] == "PRESENT" and facts["current_liabilities"]["state"] == "PRESENT":
        cl = facts["current_liabilities"]["value"]
        if cl:
            current_ratio_value = facts["current_assets"]["value"] / cl
            current_ratio_state = "PRESENT"

    return {
        "financial_data_available": True,
        "financial_data_completeness": completeness,
        "net_assets_state": net_assets_state,
        "net_assets_value": net_assets_value,
        "net_assets_inferred": net_assets_inferred,
        "current_ratio_state": current_ratio_state,
        "current_ratio_value": current_ratio_value,
        "profit_loss_state": facts["profit_loss"]["state"],
        "profit_loss_value": facts["profit_loss"]["value"],
    }


def _run_arelle(doc_url: str, content_type: str) -> dict:
    import httpx
    from config import COMPANIES_HOUSE_API_KEY

    # THE FIX: Companies House defaults to PDF unless you explicitly ask
    # for a different format via Accept. Without this header, every request
    # here silently returned PDF regardless of what the metadata check found.
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

        results = {key: {"state": "ABSENT", "value": None} for key in CONCEPT_KEYWORDS}
        if model_xbrl is None or model_xbrl.facts is None:
            return results

        period_ends = [c.endDatetime for c in model_xbrl.contexts.values() if c.endDatetime]
        latest_period_end = max(period_ends) if period_ends else None

        for fact in model_xbrl.facts:
            if fact.concept is None or fact.context is None:
                continue
            if latest_period_end is not None and fact.context.endDatetime != latest_period_end:
                continue

            label = (fact.concept.label(lang="en") or "").lower()
            for key, keywords in CONCEPT_KEYWORDS.items():
                if results[key]["state"] == "PRESENT":
                    continue
                if any(_matches(label, kw) for kw in keywords):
                    if fact.isNil:
                        results[key] = {"state": "NIL", "value": None}
                    else:
                        try:
                            value = float(str(fact.value).replace(",", ""))
                            results[key] = {"state": "PRESENT", "value": value}
                        except (ValueError, TypeError):
                            continue

        model_xbrl.close()
        return results
    finally:
        os.unlink(tmp_path)
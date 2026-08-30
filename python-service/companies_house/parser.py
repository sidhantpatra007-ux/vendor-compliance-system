from datetime import datetime, timedelta
from typing import Optional


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None


def parse_company_profile(raw: dict) -> dict:
    """Pull out only the fields we actually need from a /company/{number} response."""
    raw = raw or {}
    confirmation_statement = raw.get("confirmation_statement", {}) or {}
    registered_office = raw.get("registered_office_address", {}) or {}

    return {
        "company_number": raw.get("company_number"),
        "company_name_on_file": raw.get("company_name"),
        "company_status": raw.get("company_status"),  # "active" / "dissolved" / "liquidation" etc
        "date_of_creation": _parse_date(raw.get("date_of_creation")),
        "confirmation_statement_due": _parse_date(confirmation_statement.get("next_due")),
        "registered_office_address": {
            "address_line_1": registered_office.get("address_line_1"),
            "locality": registered_office.get("locality"),
            "postal_code": registered_office.get("postal_code"),
        },
    }


def parse_officers(raw: dict) -> list[dict]:
    """Pull out just the fields we care about for each officer, active ones only."""
    raw = raw or {}
    officers = []
    for item in raw.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("resigned_on"):
            continue  # skip former officers — we only care about who's currently active
        officers.append({
            "name": item.get("name"),
            "role": item.get("officer_role"),
            "appointed_on": item.get("appointed_on"),
        })
    return officers


def parse_latest_filing_date(raw: dict) -> Optional[datetime]:
    """Most recent filing's date, if any filings exist."""
    raw = raw or {}
    items = raw.get("items", []) or []
    if not items or not isinstance(items[0], dict):
        return None
    return _parse_date(items[0].get("date"))


def parse_filing_dates(raw: dict) -> list[datetime]:
    """All filing dates in this page of history, most recent first — needed for pattern detection (e.g. 3 late filings in a row)."""
    raw = raw or {}
    dates = []
    for item in raw.get("items", []) or []:
        if isinstance(item, dict):
            parsed = _parse_date(item.get("date"))
            if parsed:
                dates.append(parsed)
    return dates


def parse_overdue_flags(raw: dict) -> dict:
    """Companies House already computes these booleans for us — no need to derive lateness ourselves."""
    raw = raw or {}
    accounts = raw.get("accounts", {}) or {}
    confirmation_statement = raw.get("confirmation_statement", {}) or {}
    return {
        "accounts_overdue": accounts.get("overdue", False),
        "confirmation_statement_overdue": confirmation_statement.get("overdue", False),
        "has_insolvency_history": raw.get("has_insolvency_history", False),
    }


def parse_active_officers(raw: dict) -> list[dict]:
    raw = raw or {}
    return [
        {"name": item.get("name"), "role": item.get("officer_role")}
        for item in raw.get("items", []) or []
        if isinstance(item, dict) and item.get("name") and not item.get("resigned_on")
    ]


def parse_active_pscs(raw: dict) -> list[dict]:
    raw = raw or {}
    return [
        {"name": item.get("name"), "kind": item.get("kind")}
        for item in raw.get("items", []) or []
        if isinstance(item, dict) and item.get("name") and not item.get("ceased_on")
    ]


def parse_charge_signals(raw: dict) -> dict:
    raw = raw or {}
    items = raw.get("items", []) or []
    outstanding = [item for item in items if isinstance(item, dict) and item.get("status") == "outstanding"]

    has_floating_charge = False
    for charge in outstanding:
        # Check top-level charge object
        if charge.get("contains_floating_charge"):
            has_floating_charge = True
            break

        # Companies House returns `particulars` as a dictionary, not a list
        particulars = charge.get("particulars")
        if isinstance(particulars, dict) and particulars.get("contains_floating_charge"):
            has_floating_charge = True
            break
        elif isinstance(particulars, list):
            if any(p.get("contains_floating_charge") for p in particulars if isinstance(p, dict)):
                has_floating_charge = True
                break

    return {
        "charge_count": len(items),
        "outstanding_charge_count": len(outstanding),
        "has_floating_charge": has_floating_charge,
    }


def parse_insolvency_signals(raw: dict) -> dict:
    raw = raw or {}
    cases = raw.get("cases", []) if isinstance(raw, dict) else []
    return {"active_insolvency_case_count": len(cases), "has_active_insolvency_case": bool(cases)}


def parse_recent_late_filing_signal(raw: dict) -> dict:
    raw = raw or {}
    items = raw.get("items", []) or []
    late_markers = ("late filing", "overdue", "penalty")
    flagged = [
        item for item in items
        if isinstance(item, dict) and any(marker in (item.get("description") or "").lower() for marker in late_markers)
    ]
    return {"late_filing_notice_count": len(flagged), "repeated_late_filing_notices": len(flagged) >= 2}


def count_recent_resignations(raw_officers: dict, window_days: int = 90) -> int:
    """Officer resignations within the last `window_days` — this is the 'mass resignation' signal."""
    raw_officers = raw_officers or {}
    cutoff = datetime.utcnow() - timedelta(days=window_days)
    count = 0
    for item in raw_officers.get("items", []) or []:
        if isinstance(item, dict):
            resigned_on = _parse_date(item.get("resigned_on"))
            if resigned_on and resigned_on >= cutoff:
                count += 1
    return count
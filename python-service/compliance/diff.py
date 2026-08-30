"""
Compares a vendor's two most recent snapshots to identify exactly
what changed and why — the mechanism behind "pinpoint which vendor
dropped and what caused it."
"""

from sqlalchemy.orm import Session
from models import ComplianceSnapshot

TRACKED_SIGNAL_KEYS = (
    "company_status",
    "accounts_overdue",
    "confirmation_statement_overdue",
    "outstanding_charge_count",
    "has_floating_charge",
    "has_active_insolvency_case",
    "recent_resignations",
    "active_officer_count",
    "active_psc_count",
    "registered_office_address",
    "has_disqualified_officer",
    "sanctions_match",
    "director_or_psc_sanctions_match",
    "net_assets_drop_percent",
    "consecutive_losses",
)


def get_last_two_snapshots(db: Session, vendor_id: int):
    snapshots = (
        db.query(ComplianceSnapshot)
        .filter(ComplianceSnapshot.vendor_id == vendor_id)
        .order_by(ComplianceSnapshot.checked_at.desc())
        .limit(2)
        .all()
    )
    if len(snapshots) < 2:
        return None  # not enough history yet to diff
    current, previous = snapshots[0], snapshots[1]
    return previous, current


def diff_snapshots(previous: ComplianceSnapshot, current: ComplianceSnapshot) -> dict:
    score_delta = current.composite_score - previous.composite_score

    prev_codes = {f["code"]: f for f in previous.factors}
    curr_codes = {f["code"]: f for f in current.factors}

    new_factors = [f for code, f in curr_codes.items() if code not in prev_codes]
    resolved_factors = [f for code, f in prev_codes.items() if code not in curr_codes]

    changed_factors = []
    for code, curr_f in curr_codes.items():
        if code in prev_codes and curr_f["description"] != prev_codes[code]["description"]:
            changed_factors.append({
                "code": code,
                "before": prev_codes[code]["description"],
                "after": curr_f["description"],
            })

    changed_signals = []
    for key in TRACKED_SIGNAL_KEYS:
        before = (previous.signals or {}).get(key)
        after = (current.signals or {}).get(key)
        if before != after:
            changed_signals.append({"key": key, "before": before, "after": after})

    return {
        "previous_score": previous.composite_score,
        "current_score": current.composite_score,
        "score_delta": score_delta,
        "previous_grade": previous.risk_grade,
        "current_grade": current.risk_grade,
        "new_factors": new_factors,
        "resolved_factors": resolved_factors,
        "changed_factors": changed_factors,
        "changed_signals": changed_signals,
        "previous_checked_at": previous.checked_at.isoformat(),
        "current_checked_at": current.checked_at.isoformat(),
    }

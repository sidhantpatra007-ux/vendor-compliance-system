"""
Multi-dimensional compliance scoring, modeled on the D&B pattern of
separate purpose-scoped scores rather than one blended deduction total.

Each category is scored independently 0-100 (100 = no concern).
A composite score is a WEIGHTED AVERAGE of categories, not a running
subtraction — this means one catastrophic category can't silently
swallow signal from the others, and each category can be read on its
own by whichever downstream consumer cares about it.

Every deduction is a structured Factor, not a plain string, so a
dashboard or n8n workflow can group/filter/sum by category or code
without re-parsing text.

CHANGE (v1.1.0): added financial_health (XBRL/Arelle-derived) and
sanctions_screening categories. Sanctions is treated as a hard override
on risk_grade rather than just another weighted input — a real match
is a legal exposure question, not a point on a risk gradient, so it
shouldn't be dilutable by an otherwise clean score.

CHANGE (v1.4.0): financial_health now recognizes net_assets_state ==
"INFERRED" (see companies_house/xbrl.py) — some filers never tag a
net assets/shareholders' funds fact directly, only "total assets less
current liabilities". When that subtotal is confirmed safe to use as a
stand-in (no long-term creditors/provisions/accruals found), xbrl.py
reconstructs net_assets_value from it and marks the state INFERRED
rather than PRESENT. Scoring treats INFERRED the same as PRESENT for
the purpose of the negative-net-assets check (it's still a usable
number), but adds its own zero-point, low-severity factor so this is
visible to anyone reading the factors list — a reconstructed figure and
a directly reported one should not look identical in the output.
"""

from dataclasses import dataclass
from typing import Optional

SCORING_VERSION = "1.6.0"


@dataclass
class Factor:
    code: str
    category: str
    description: str
    points: int
    severity: str


CATEGORY_WEIGHTS = {
    "registration_validity": 0.20,
    "filing_compliance": 0.15,
    "governance_stability": 0.10,
    "insolvency_risk": 0.10,
    "financial_health": 0.25,
    "sanctions_screening": 0.20,
}
assert abs(sum(CATEGORY_WEIGHTS.values()) - 1.0) < 1e-9

RISK_GRADE_BANDS = [
    (90, "A"),
    (75, "B"),
    (55, "C"),
    (30, "D"),
    (0,  "E"),
]


def _grade_for(score: int) -> str:
    for threshold, grade in RISK_GRADE_BANDS:
        if score >= threshold:
            return grade
    return "E"


def _score_registration_validity(signals: dict) -> tuple[int, list[Factor]]:
    status = signals.get("company_status")
    factors = []
    score = 100

    terminal_statuses = {
        "dissolved": ("STATUS_DISSOLVED", -100, "critical", "Company has been dissolved"),
        "liquidation": ("STATUS_LIQUIDATION", -70, "critical", "Company is in liquidation"),
        "administration": ("STATUS_ADMINISTRATION", -40, "high", "Company is in administration"),
        "receivership": ("STATUS_RECEIVERSHIP", -40, "high", "Company is in receivership"),
    }

    if status in terminal_statuses:
        code, points, severity, desc = terminal_statuses[status]
        score += points
        factors.append(Factor(code, "registration_validity", desc, points, severity))

    return max(0, score), factors


def _score_filing_compliance(signals: dict) -> tuple[int, list[Factor]]:
    score = 100
    factors = []

    if signals.get("accounts_overdue"):
        score -= 15
        factors.append(Factor(
            "ACCOUNTS_OVERDUE", "filing_compliance",
            "Annual accounts are overdue with Companies House",
            -15, "medium"
        ))

    if signals.get("confirmation_statement_overdue"):
        score -= 5
        factors.append(Factor(
            "CONFIRMATION_STATEMENT_OVERDUE", "filing_compliance",
            "Confirmation statement is overdue",
            -10, "medium"
        ))

    if signals.get("repeated_late_filing_notices"):
        score -= 10
        factors.append(Factor(
            "REPEATED_LATE_FILING_NOTICES", "filing_compliance",
            "Multiple late-filing or overdue notices appear in recent filing history",
            -15, "medium"
        ))

    return max(0, score), factors


def _score_governance_stability(signals: dict) -> tuple[int, list[Factor]]:
    score = 100
    factors = []
    resignations = signals.get("recent_resignations", 0)

    if resignations >= 3:
        points = -min(20, 5 * resignations)
        score += points
        factors.append(Factor(
            "MASS_OFFICER_RESIGNATION", "governance_stability",
            f"{resignations} officer resignations within the monitoring window",
            points, "high"
        ))
    elif resignations >= 1:
        points = 0
        factors.append(Factor(
            "OFFICER_RESIGNATION", "governance_stability",
            f"{resignations} officer resignation(s) within the monitoring window",
            points, "low"
        ))

    if signals.get("has_disqualified_officer"):
        score = 0
        factors.append(Factor(
            "DISQUALIFIED_OFFICER", "governance_stability",
            "A current officer appears in the Companies House disqualified-officers search",
            -100, "critical"
        ))

    return max(0, score), factors


def _score_insolvency_risk(signals: dict) -> tuple[int, list[Factor]]:
    score = 100
    factors = []

    if signals.get("has_insolvency_history"):
        score -= 15
        factors.append(Factor(
            "INSOLVENCY_HISTORY", "insolvency_risk",
            "Company has a recorded insolvency history",
            -30, "high"
        ))

    if signals.get("has_active_insolvency_case"):
        score = 0
        factors.append(Factor(
            "ACTIVE_INSOLVENCY_CASE", "insolvency_risk",
            "Companies House reports an active insolvency case",
            -100, "critical"
        ))

    return max(0, score), factors


def _score_financial_health(signals: dict) -> tuple[int, list[Factor]]:
    """
    Scored from Arelle-extracted XBRL facts (see companies_house/xbrl.py).
    Each sub-signal has an explicit PRESENT/NIL/ABSENT (and, for
    net_assets specifically, INFERRED) state — ABSENT (e.g. a
    micro-entity that legally didn't file a P&L) contributes NO factor
    and no deduction, rather than being scored as a bad value.

    If NO structured accounts data exists at all (e.g. a PDF-only filing —
    common for large PLCs whose agent-filed accounts never reach Companies
    House in iXBRL/HTML form), the category scores a neutral 50 rather than
    defaulting to a perfect 100. A total absence of data is not the same
    thing as a clean bill of health, and shouldn't be scored as one — 50
    signals "unknown," not "verified fine." This is also flagged as
    UNSCORED so downstream consumers (e.g. the composite score, or a
    client-facing report) can treat it as low-confidence rather than as
    a real assessment.
    """
    score = 100
    factors = []

    if not signals.get("financial_scoring_available"):
        factors.append(Factor(
            "FINANCIAL_HEALTH_UNASSESSED", "financial_health",
            "No validated financial metric was available. This does not reduce the vendor score, "
            "but financial health needs review.",
            0, "low"
        ))
        return 100, factors

    net_assets_state = signals.get("net_assets_state")
    if net_assets_state in ("PRESENT", "INFERRED"):
        if signals["net_assets_value"] < 0:
            score -= 25
            factors.append(Factor(
                "NEGATIVE_NET_ASSETS", "financial_health",
                "Balance sheet shows negative net assets", -30, "high"
            ))
        if net_assets_state == "INFERRED":
            factors.append(Factor(
                "INFERRED_NET_ASSETS", "financial_health",
                "Net assets was not directly reported in this filing — the value was "
                "reconstructed from the 'total assets less current liabilities' subtotal "
                "because this filer did not tag net assets/shareholders' funds separately. "
                "Treat this figure with lower confidence than a directly reported one.",
                0, "low"
            ))
    elif net_assets_state == "NIL":
        score -= 10
        factors.append(Factor(
            "NIL_NET_ASSETS", "financial_health",
            "Net assets reported as nil", -10, "low"
        ))

    if signals.get("current_ratio_state") == "PRESENT":
        ratio = signals["current_ratio_value"]
        if ratio < 0.75:
            score -= 15
            factors.append(Factor(
                "CRITICAL_CURRENT_RATIO", "financial_health",
                f"Current ratio of {ratio:.2f} — severe short-term liquidity pressure",
                -15, "medium"
            ))
        elif ratio < 1:
            score -= 8
            factors.append(Factor(
                "WEAK_CURRENT_RATIO", "financial_health",
                f"Current ratio of {ratio:.2f} — current liabilities exceed current assets",
                -8, "low"
            ))

    if signals.get("profit_loss_state") == "PRESENT" and signals["profit_loss_value"] < 0:
        score -= 8
        factors.append(Factor(
            "LOSS_MAKING", "financial_health",
            "Loss-making in last filed period", -8, "low"
        ))

    completeness = signals.get("financial_data_completeness", 0)
    if 0 < completeness < 0.5:
        factors.append(Factor(
            "LOW_FINANCIAL_DATA_COMPLETENESS", "financial_health",
            "Fewer than half of expected financial metrics were found in the filing — treat this category's score as lower-confidence",
            0, "low"
        ))

    return max(0, score), factors


def _score_sanctions_screening(signals: dict) -> tuple[int, list[Factor]]:
    score = 100
    factors = []

    if not signals.get("sanctions_screening_available", True):
        factors.append(Factor(
            "SANCTIONS_SCREENING_UNAVAILABLE", "sanctions_screening",
            "The local UK Sanctions List service was unavailable, so this vendor was not screened. Manual review is required before approval.",
            0, "medium",
        ))

    if signals.get("net_assets_drop_percent", 0) >= 0.30:
        score -= 10
        factors.append(Factor(
            "MATERIAL_NET_ASSETS_DROP", "financial_health",
            f"Net assets fell {signals['net_assets_drop_percent']:.0%} since the prior assessed filing",
            -10, "medium"
        ))

    if signals.get("consecutive_losses"):
        score -= 10
        factors.append(Factor(
            "CONSECUTIVE_LOSSES", "financial_health",
            "Losses were reported in two consecutive assessed filings", -10, "medium"
        ))
        return score, factors

    if signals.get("sanctions_match"):
        score = 0
        factors.append(Factor(
            "SANCTIONS_MATCH", "sanctions_screening",
            f"Name matches UK Sanctions List entry '{signals.get('sanctions_match_name')}' "
            f"(match confidence {signals.get('sanctions_match_score')}%) — legal review required before proceeding",
            -100, "critical"
        ))

    if signals.get("director_or_psc_sanctions_match"):
        score = 0
        factors.append(Factor(
            "DIRECTOR_OR_PSC_SANCTIONS_MATCH", "sanctions_screening",
            "A director or person with significant control matched the UK Sanctions List",
            -100, "critical"
        ))

    return score, factors


CATEGORY_SCORERS = {
    "registration_validity": _score_registration_validity,
    "filing_compliance": _score_filing_compliance,
    "governance_stability": _score_governance_stability,
    "insolvency_risk": _score_insolvency_risk,
    "financial_health": _score_financial_health,
    "sanctions_screening": _score_sanctions_screening,
}


# Factor codes that mean "we couldn't actually assess this category" —
# used to flag a category as unscored rather than genuinely clean/verified.
UNSCORED_MARKER_CODES = {"FINANCIAL_HEALTH_UNASSESSED", "SANCTIONS_SCREENING_UNAVAILABLE"}


def score_from_signals(signals: dict) -> dict:
    categories = {}
    all_factors = []
    weighted_sum = 0.0
    assessed_weight = 0.0
    unscored_categories = []

    for category, scorer in CATEGORY_SCORERS.items():
        cat_score, cat_factors = scorer(signals)
        weight = CATEGORY_WEIGHTS[category]
        is_unscored = any(f.code in UNSCORED_MARKER_CODES for f in cat_factors)
        if is_unscored:
            unscored_categories.append(category)
        else:
            weighted_sum += cat_score * weight
            assessed_weight += weight
        categories[category] = {
            "score": None if is_unscored else cat_score,
            "weight": weight,
            "unscored": is_unscored,
            "factors": [f.__dict__ for f in cat_factors],
        }
        all_factors.extend(cat_factors)

    composite = round(weighted_sum / assessed_weight) if assessed_weight else 0
    grade = _grade_for(composite)

    # Hard override: any CRITICAL-severity factor is a disqualifying condition,
    # not a point on a risk gradient — a dissolved company or a sanctions match
    # shouldn't be dilutable by five other categories scoring 100. Weighted
    # averaging is the right tool for "how risky is this," it's the wrong tool
    # for "can this company legally/practically be done business with at all."
    block_reasons = [f for f in all_factors if f.severity == "critical"]
    blocked = len(block_reasons) > 0
    sanctions_blocked = any(f.code in {"SANCTIONS_MATCH", "DIRECTOR_OR_PSC_SANCTIONS_MATCH"} for f in all_factors)
    if blocked:
        grade = "E"

    return {
        "composite_score": composite,
        "risk_grade": grade,
        "blocked": blocked,
        "block_reasons": [f.__dict__ for f in block_reasons],
        "sanctions_blocked": sanctions_blocked,
        "unscored_categories": unscored_categories,
        "scoring_version": SCORING_VERSION,
        "categories": categories,
        "factors": [f.__dict__ for f in all_factors],
    }

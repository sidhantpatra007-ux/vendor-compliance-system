from dataclasses import dataclass

SCORING_VERSION = "1.7.0"


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
    "governance_stability": 0.15,
    "insolvency_risk": 0.15,
    "financial_health": 0.10,
    "sanctions_screening": 0.25,
}

RISK_GRADE_BANDS = [
    (90, "A"),
    (75, "B"),
    (55, "C"),
    (30, "D"),
    (0, "E"),
]

UNSCORED_MARKER_CODES = {
    "FINANCIAL_HEALTH_UNASSESSED",
    "SANCTIONS_SCREENING_UNAVAILABLE",
}


def _grade_for(score: int) -> str:
    for threshold, grade in RISK_GRADE_BANDS:
        if score >= threshold:
            return grade
    return "E"


def _score_registration_validity(signals: dict):
    score = 100
    factors = []

    statuses = {
        "dissolved": ("STATUS_DISSOLVED", -100, "critical", "Company has been dissolved"),
        "liquidation": ("STATUS_LIQUIDATION", -70, "critical", "Company is in liquidation"),
        "administration": ("STATUS_ADMINISTRATION", -40, "high", "Company is in administration"),
        "receivership": ("STATUS_RECEIVERSHIP", -40, "high", "Company is in receivership"),
    }

    status = signals.get("company_status")
    if status in statuses:
        code, points, severity, description = statuses[status]
        score += points
        factors.append(Factor(code, "registration_validity", description, points, severity))

    return max(0, score), factors


def _score_filing_compliance(signals: dict):
    score = 100
    factors = []

    if signals.get("accounts_overdue"):
        score -= 15
        factors.append(Factor(
            "ACCOUNTS_OVERDUE",
            "filing_compliance",
            "Annual accounts are overdue with Companies House",
            -15,
            "medium",
        ))

    if signals.get("confirmation_statement_overdue"):
        score -= 5
        factors.append(Factor(
            "CONFIRMATION_STATEMENT_OVERDUE",
            "filing_compliance",
            "Confirmation statement is overdue",
            -5,
            "medium",
        ))

    if signals.get("repeated_late_filing_notices"):
        score -= 10
        factors.append(Factor(
            "REPEATED_LATE_FILING_NOTICES",
            "filing_compliance",
            "Multiple late-filing or overdue notices appear in recent filing history",
            -10,
            "medium",
        ))

    return max(0, score), factors


def _score_governance_stability(signals: dict):
    score = 100
    factors = []
    resignations = signals.get("recent_resignations", 0)

    if resignations >= 3:
        points = -min(20, 5 * resignations)
        score += points
        factors.append(Factor(
            "MASS_OFFICER_RESIGNATION",
            "governance_stability",
            f"{resignations} officer resignations within the monitoring window",
            points,
            "high",
        ))
    elif resignations >= 1:
        factors.append(Factor(
            "OFFICER_RESIGNATION",
            "governance_stability",
            f"{resignations} officer resignation(s) within the monitoring window",
            0,
            "low",
        ))

    if signals.get("has_disqualified_officer"):
        score = 0
        factors.append(Factor(
            "DISQUALIFIED_OFFICER",
            "governance_stability",
            "A current officer appears in the Companies House disqualified-officers search",
            -100,
            "critical",
        ))

    if signals.get("psc_changed_recently"):
        score -= 3
        factors.append(Factor(
            "PSC_CHANGED_RECENTLY",
            "governance_stability",
            "Person with significant control details changed since the prior assessment",
            -3,
            "low",
        ))

    if signals.get("psc_details_unclear"):
        score -= 3
        factors.append(Factor(
            "PSC_DETAILS_UNCLEAR",
            "governance_stability",
            "PSC control details are missing or could not be confirmed from Companies House data",
            -3,
            "low",
        ))

    return max(0, score), factors


def _score_insolvency_risk(signals: dict):
    score = 100
    factors = []

    if signals.get("has_insolvency_history"):
        score -= 15
        factors.append(Factor(
            "INSOLVENCY_HISTORY",
            "insolvency_risk",
            "Company has a recorded insolvency history",
            -15,
            "high",
        ))

    if signals.get("has_active_insolvency_case"):
        score = 0
        factors.append(Factor(
            "ACTIVE_INSOLVENCY_CASE",
            "insolvency_risk",
            "Companies House reports an active insolvency case",
            -100,
            "critical",
        ))

    return max(0, score), factors


def _score_financial_health(signals: dict):
    if not signals.get("financial_scoring_available"):
        return 100, [
            Factor(
                "FINANCIAL_HEALTH_UNASSESSED",
                "financial_health",
                "No validated financial metric was available. This does not reduce the vendor score, but financial health needs review.",
                0,
                "low",
            )
        ]

    score = 100
    factors = []

    net_assets_state = signals.get("net_assets_state")
    net_assets_value = signals.get("net_assets_value")

    if net_assets_state in {"PRESENT", "INFERRED"} and net_assets_value is not None:
        if net_assets_value < 0:
            score -= 25
            factors.append(Factor(
                "NEGATIVE_NET_ASSETS",
                "financial_health",
                "Balance sheet shows negative net assets",
                -25,
                "high",
            ))

        if net_assets_state == "INFERRED":
            factors.append(Factor(
                "INFERRED_NET_ASSETS",
                "financial_health",
                "Net assets was reconstructed from filing data and needs lower-confidence review.",
                0,
                "low",
            ))

    elif net_assets_state == "NIL":
        score -= 10
        factors.append(Factor(
            "NIL_NET_ASSETS",
            "financial_health",
            "Net assets reported as nil",
            -10,
            "low",
        ))

    if signals.get("current_ratio_state") == "PRESENT":
        ratio = signals.get("current_ratio_value")
        if ratio is not None and ratio < 0.75:
            score -= 15
            factors.append(Factor(
                "CRITICAL_CURRENT_RATIO",
                "financial_health",
                f"Current ratio of {ratio:.2f} indicates severe short-term liquidity pressure",
                -15,
                "medium",
            ))
        elif ratio is not None and ratio < 1:
            score -= 8
            factors.append(Factor(
                "WEAK_CURRENT_RATIO",
                "financial_health",
                f"Current ratio of {ratio:.2f} means current liabilities exceed current assets",
                -8,
                "low",
            ))

    if (
        signals.get("profit_loss_state") == "PRESENT"
        and (signals.get("profit_loss_value") or 0) < 0
    ):
        score -= 8
        factors.append(Factor(
            "LOSS_MAKING",
            "financial_health",
            "Loss-making in the last assessed filing period",
            -8,
            "low",
        ))

    if signals.get("net_assets_drop_percent", 0) >= 0.30:
        score -= 10
        factors.append(Factor(
            "MATERIAL_NET_ASSETS_DROP",
            "financial_health",
            f"Net assets fell {signals['net_assets_drop_percent']:.0%} since the prior assessed filing",
            -10,
            "medium",
        ))

    if signals.get("consecutive_losses"):
        score -= 10
        factors.append(Factor(
            "CONSECUTIVE_LOSSES",
            "financial_health",
            "Losses were reported in two consecutive assessed filings",
            -10,
            "medium",
        ))

    completeness = signals.get("financial_data_completeness", 0)
    if 0 < completeness < 0.5:
        factors.append(Factor(
            "LOW_FINANCIAL_DATA_COMPLETENESS",
            "financial_health",
            "Fewer than half of expected financial metrics were found. Treat this category as lower confidence.",
            0,
            "low",
        ))

    return max(0, score), factors


def _score_sanctions_screening(signals: dict):
    score = 100
    factors = []

    if not signals.get("sanctions_screening_available", True):
        factors.append(Factor(
            "SANCTIONS_SCREENING_UNAVAILABLE",
            "sanctions_screening",
            "The UK Sanctions List service was unavailable. Manual review is required before approval.",
            0,
            "medium",
        ))

    if signals.get("sanctions_match_state") == "CONFIRMED_MATCH":
        factors.append(Factor(
            "SANCTIONS_MATCH",
            "sanctions_screening",
            "A confirmed sanctions match requires legal and human review before proceeding.",
            -100,
            "critical",
        ))

    if signals.get("director_or_psc_sanctions_match_state") == "CONFIRMED_MATCH":
        factors.append(Factor(
            "DIRECTOR_OR_PSC_SANCTIONS_MATCH",
            "sanctions_screening",
            "A director or PSC matched the UK Sanctions List.",
            -100,
            "critical",
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


def score_from_signals(signals: dict) -> dict:
    categories = {}
    all_factors = []
    weighted_sum = 0.0
    assessed_weight = 0.0
    unscored_categories = []

    for category, scorer in CATEGORY_SCORERS.items():
        category_score, factors = scorer(signals)
        weight = CATEGORY_WEIGHTS[category]
        unscored = any(
            factor.code in UNSCORED_MARKER_CODES
            for factor in factors
        )

        if unscored:
            unscored_categories.append(category)
        else:
            weighted_sum += category_score * weight
            assessed_weight += weight

        categories[category] = {
            "score": None if unscored else category_score,
            "weight": weight,
            "unscored": unscored,
            "factors": [factor.__dict__ for factor in factors],
        }
        all_factors.extend(factors)

    composite_score = round(weighted_sum / assessed_weight) if assessed_weight else 0
    block_reasons = [
        factor for factor in all_factors
        if factor.severity == "critical"
    ]

    return {
        "composite_score": composite_score,
        "risk_grade": "E" if block_reasons else _grade_for(composite_score),
        "blocked": bool(block_reasons),
        "block_reasons": [factor.__dict__ for factor in block_reasons],
        "sanctions_blocked": any(
            factor.code in {
                "SANCTIONS_MATCH",
                "DIRECTOR_OR_PSC_SANCTIONS_MATCH",
            }
            for factor in all_factors
        ),
        "unscored_categories": unscored_categories,
        "scoring_version": SCORING_VERSION,
        "categories": categories,
        "factors": [factor.__dict__ for factor in all_factors],
    }
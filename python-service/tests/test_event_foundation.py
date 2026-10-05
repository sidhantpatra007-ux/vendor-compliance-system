import unittest

from compliance.scoring import score_from_signals
from source_adapters.companies_house import normalize_stream_event
from source_adapters.sanctions import normalize_screening_result


def signals(**updates):
    value = {
        "company_status": "active",
        "financial_scoring_available": True,
        "net_assets_state": "PRESENT",
        "net_assets_value": 100,
        "profit_loss_state": "PRESENT",
        "profit_loss_value": 10,
        "current_ratio_state": "PRESENT",
        "current_ratio_value": 1.5,
        "sanctions_match_state": "NO_MATCH",
        "director_or_psc_sanctions_match_state": "NO_MATCH",
    }
    value.update(updates)
    return value


class EventFoundationTests(unittest.TestCase):
    def test_financial_factors_do_not_belong_to_sanctions(self):
        result = score_from_signals(signals(net_assets_drop_percent=.35, consecutive_losses=True))
        factors = {factor["code"]: factor for factor in result["factors"]}
        self.assertEqual(factors["MATERIAL_NET_ASSETS_DROP"]["category"], "financial_health")
        self.assertEqual(factors["CONSECUTIVE_LOSSES"]["category"], "financial_health")

    def test_confirmed_sanctions_is_not_skipped_after_losses(self):
        result = score_from_signals(signals(consecutive_losses=True, sanctions_match_state="CONFIRMED_MATCH"))
        self.assertTrue(result["sanctions_blocked"])
        self.assertEqual(result["risk_grade"], "E")

    def test_review_candidate_is_not_a_confirmed_match(self):
        result = score_from_signals(signals(sanctions_match_state="REVIEW_REQUIRED"))
        self.assertFalse(result["sanctions_blocked"])

    def test_companies_house_event_has_stable_source_identifier(self):
        event = normalize_stream_event("officers", {
            "resource_uri": "/company/01234567/officers/abc",
            "event": {"timepoint": 42, "published_at": "2026-09-24T10:00:00Z"},
        })
        self.assertEqual(event.source_event_id, "officers:42")
        self.assertEqual(event.category, "CORPORATE")

    def test_sanctions_candidate_requires_review(self):
        event = normalize_screening_result("Example Ltd", "company", {
            "sanctions_match_state": "REVIEW_REQUIRED",
            "sanctions_checked_at": "2026-09-24T10:00:00Z",
            "sanctions_list_version": "v1",
            "sanctions_candidate_unique_id": "123",
            "sanctions_candidate_name": "Example Listed Entity",
            "sanctions_match_score": 98.0,
        })
        self.assertEqual(event.event_type, "SANCTIONS_REVIEW_REQUIRED")
        self.assertEqual(event.severity, "HIGH")


if __name__ == "__main__":
    unittest.main()

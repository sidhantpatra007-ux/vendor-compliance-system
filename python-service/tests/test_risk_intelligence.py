import unittest
from types import SimpleNamespace

from risk_intelligence import alert_policy, calculate_trend


class RiskIntelligenceTests(unittest.TestCase):
    def test_critical_supplier_has_four_hour_sla(self):
        self.assertEqual(alert_policy("Critical", "low"), ("critical", 4))

    def test_high_supplier_has_high_minimum_severity(self):
        self.assertEqual(alert_policy("High", "medium"), ("high", 24))

    def test_low_supplier_keeps_original_severity(self):
        self.assertEqual(alert_policy("Low", "medium"), ("medium", 72))

    def test_trend_requires_three_snapshots(self):
        snapshots = [SimpleNamespace(composite_score=100, checked_at=1), SimpleNamespace(composite_score=95, checked_at=2)]
        self.assertEqual(calculate_trend(snapshots), "INSUFFICIENT_DATA")

    def test_trend_detects_deterioration(self):
        snapshots = [SimpleNamespace(composite_score=100, checked_at=1), SimpleNamespace(composite_score=98, checked_at=2), SimpleNamespace(composite_score=90, checked_at=3)]
        self.assertEqual(calculate_trend(snapshots), "DETERIORATING")


if __name__ == "__main__":
    unittest.main()

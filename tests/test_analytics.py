import unittest

from analytics import build_analytics


class AnalyticsTests(unittest.TestCase):
    def test_no_transactions(self):
        result = build_analytics([], [])
        self.assertEqual(result["summary"]["total_income"], 0.0)
        self.assertEqual(result["summary"]["total_expenses"], 0.0)
        self.assertIsNone(result["summary"]["savings_rate"])

    def test_only_income(self):
        result = build_analytics([], [{"amount": 1000, "source": "salary", "date": "2026-01-15"}])
        self.assertEqual(result["summary"]["net_cash_flow"], 1000.0)
        self.assertEqual(result["summary"]["savings_rate"], 100.0)

    def test_only_expenses(self):
        result = build_analytics([{"amount": 200, "category": "food", "date": "2026-01-15"}], [])
        self.assertEqual(result["summary"]["total_expenses"], 200.0)
        self.assertEqual(result["summary"]["net_cash_flow"], -200.0)
        self.assertIsNone(result["summary"]["savings_rate"])

    def test_income_and_expenses(self):
        result = build_analytics(
            [{"amount": 300, "category": "food", "date": "2026-01-15"}],
            [{"amount": 1000, "source": "salary", "date": "2026-01-01"}],
        )
        self.assertEqual(result["summary"]["savings"], 700.0)
        self.assertEqual(result["summary"]["savings_rate"], 70.0)

    def test_zero_income_is_safe(self):
        result = build_analytics(
            [{"amount": 100, "category": "food", "date": "2026-01-01"}],
            [{"amount": 0, "source": "invalid", "date": "2026-01-01"}],
        )
        self.assertIsNone(result["summary"]["savings_rate"])

    def test_same_day_transactions_are_not_collapsed(self):
        expenses = [
            {"amount": 10, "category": "food", "date": "2026-01-01"},
            {"amount": 20, "category": "transportation", "date": "2026-01-01"},
        ]
        result = build_analytics(expenses, [])
        self.assertEqual(result["summary"]["total_expenses"], 30.0)
        self.assertEqual(result["summary"]["expense_transaction_count"], 2)

    def test_monthly_average_includes_zero_activity_month(self):
        result = build_analytics(
            [
                {"amount": 100, "category": "food", "date": "2026-01-01"},
                {"amount": 300, "category": "food", "date": "2026-03-01"},
            ],
            [],
        )
        self.assertEqual(result["period"]["months_count"], 3)
        self.assertAlmostEqual(result["summary"]["average_monthly_expenses"], 133.33, places=2)
        self.assertEqual(result["monthly"][1]["expenses"], 0.0)

    def test_category_percentages_use_total_expenses(self):
        result = build_analytics(
            [
                {"amount": 75, "category": "food", "date": "2026-01-01"},
                {"amount": 25, "category": "travel", "date": "2026-01-02"},
            ],
            [],
        )
        self.assertEqual(result["categories"][0]["percentage"], 75.0)
        self.assertEqual(result["categories"][1]["percentage"], 25.0)

    def test_invalid_dates_and_negative_values_are_ignored(self):
        result = build_analytics(
            [
                {"amount": -10, "category": "food", "date": "2026-01-01"},
                {"amount": 50, "category": "food", "date": "not-a-date"},
                {"amount": 20, "category": "food", "date": "2026-01-01"},
            ],
            [],
        )
        self.assertEqual(result["summary"]["total_expenses"], 20.0)
        self.assertEqual(result["validation"]["invalid_expenses_ignored"], 2)

    def test_large_amounts_keep_precision(self):
        result = build_analytics(
            [{"amount": 100000000.99, "category": "other", "date": "2026-01-01"}],
            [{"amount": 200000000.99, "source": "business", "date": "2026-01-01"}],
        )
        self.assertEqual(result["summary"]["net_cash_flow"], 100000000.0)

    def test_duplicate_rows_are_counted_as_transactions_not_silently_removed(self):
        row = {"amount": 50, "category": "food", "description": "lunch", "date": "2026-01-01"}
        result = build_analytics([row, row.copy()], [])
        self.assertEqual(result["summary"]["total_expenses"], 100.0)
        self.assertEqual(result["summary"]["expense_transaction_count"], 2)

    def test_budget_metrics(self):
        result = build_analytics(
            [{"amount": 900, "category": "food", "date": "2026-01-01"}],
            [],
            [{"category": "food", "amount": 1000, "month": "2026-01"}],
        )
        budget = result["budget"]["items"][0]
        self.assertEqual(budget["remaining"], 100.0)
        self.assertEqual(budget["utilization_pct"], 90.0)
        self.assertTrue(budget["approaching_limit"])


if __name__ == "__main__":
    unittest.main()

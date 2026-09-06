import os
import tempfile
import unittest

from database import ExpenseDatabase


class DatabaseTests(unittest.TestCase):
    def test_sqlite_crud_and_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.db")
            db = ExpenseDatabase(path)
            expense_id = db.add_expense(25, "Lunch", "Food", "2026-01-01")
            income_id = db.add_income(1000, "Salary", "2026-01-01")
            self.assertGreater(expense_id, 0)
            self.assertGreater(income_id, 0)
            self.assertEqual(db.get_expenses(days=36500)[0]["amount"], 25)
            self.assertEqual(db.get_income(days=36500)[0]["amount"], 1000)
            db.set_budget("Food", 100, "2026-01")
            budget = db.get_budget_vs_actual("2026-01")[0]
            self.assertEqual(budget["spent"], 25)
            self.assertEqual(budget["remaining"], 75)
            self.assertTrue(db.delete_expense(expense_id))
            self.assertTrue(db.delete_income(income_id))


if __name__ == "__main__":
    unittest.main()

"""One-time migration helper: SQLite finance.db -> PostgreSQL/Neon.

Usage:
    DATABASE_URL='postgresql://...' python migrate_sqlite_to_postgres.py finance.db

The destination schema is created by ExpenseDatabase. Exact duplicate expense
rows are skipped using their amount/description/category/date fingerprint.
"""

from __future__ import annotations

import os
import sqlite3
import sys

from database import ExpenseDatabase


def migrate(source_path: str, user_id: str = "legacy") -> None:
    if not os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
        raise SystemExit("DATABASE_URL must be a PostgreSQL connection string for migration.")

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    target = ExpenseDatabase()

    expense_count = income_count = budget_count = 0
    with source:
        for row in source.execute("SELECT amount, description, category, date FROM expenses ORDER BY id"):
            existing = [
                item for item in target.get_all_expenses(user_id)
                if float(item["amount"]) == float(row["amount"])
                and item["description"] == row["description"]
                and item["category"] == row["category"]
                and item["date"] == row["date"]
            ]
            if not existing:
                target.add_expense(user_id, row["amount"], row["description"], row["category"], row["date"])
                expense_count += 1

        # Older SQLite projects may not have an income table.
        tables = {r["name"] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "income" in tables:
            for row in source.execute("SELECT amount, source, date FROM income ORDER BY id"):
                existing = [
                    item for item in target.get_all_income(user_id)
                    if float(item["amount"]) == float(row["amount"])
                    and item["source"] == row["source"]
                    and item["date"] == row["date"]
                ]
                if not existing:
                    target.add_income(user_id, row["amount"], row["source"], row["date"])
                    income_count += 1

        if "budgets" in tables:
            for row in source.execute("SELECT category, amount, month FROM budgets ORDER BY id"):
                target.set_budget(user_id, row["category"], row["amount"], row["month"])
                budget_count += 1

    source.close()
    print(f"Migration complete: {expense_count} expenses, {income_count} income rows, {budget_count} budgets.")


if __name__ == "__main__":
    migrate(sys.argv[1] if len(sys.argv) > 1 else "finance.db", os.getenv("MIGRATION_USER_ID", "legacy"))

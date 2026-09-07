"""Reset the local SQLite database for development.

For production PostgreSQL, prefer targeted SQL/migrations rather than deleting
the entire database from an application script.
"""

import os
import sys

from database import ExpenseDatabase

DB_PATH = os.getenv("LOCAL_DB_PATH", "finance.db")


def reset_database(confirm: bool = False) -> None:
    if not confirm:
        answer = input("WARNING: this will delete local expenses, income and budgets. Type 'yes' to confirm: ")
        if answer.strip().lower() != "yes":
            print("Aborted.")
            return

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Deleted {DB_PATH}")

    db = ExpenseDatabase(DB_PATH)
    print("Fresh local database created")
    print(f"Expenses: {len(db.get_expenses('legacy', days=36500))}")
    print(f"Income: {len(db.get_income('legacy', days=36500))}")


if __name__ == "__main__":
    reset_database(confirm="--yes" in sys.argv)

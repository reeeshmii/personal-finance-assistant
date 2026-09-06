"""Persistence layer for the Personal Finance Assistant.

SQLite is used for local development. Production deployments should use a
persistent PostgreSQL database (for example Neon on Vercel).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator, Optional

from dotenv import load_dotenv

load_dotenv()


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///finance.db").strip()


def _is_postgres(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _resolve_sqlite_path(url: str, override: Optional[str] = None) -> str:
    if override:
        return override
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :] or "finance.db"
    return url or "finance.db"


class ExpenseDatabase:
    """Small database repository supporting local SQLite and production Postgres."""

    def __init__(self, db_path: Optional[str] = None):
        self.database_url = _database_url()
        self.is_postgres = _is_postgres(self.database_url)
        self.db_path = _resolve_sqlite_path(self.database_url, db_path) if not self.is_postgres else None
        self._init_db()

    @contextmanager
    def _get_conn(self) -> Iterator[Any]:
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "PostgreSQL support requires psycopg. Install the project requirements."
                ) from exc

            with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
                yield conn
            return

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.is_postgres else statement

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            if self.is_postgres:
                statements = [
                    """
                    CREATE TABLE IF NOT EXISTS expenses (
                        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        amount DOUBLE PRECISION NOT NULL CHECK(amount > 0),
                        description TEXT NOT NULL,
                        category TEXT NOT NULL,
                        date TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS income (
                        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        amount DOUBLE PRECISION NOT NULL CHECK(amount > 0),
                        source TEXT NOT NULL,
                        date TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS budgets (
                        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        category TEXT NOT NULL,
                        amount DOUBLE PRECISION NOT NULL CHECK(amount > 0),
                        month TEXT NOT NULL,
                        UNIQUE(category, month)
                    )
                    """,
                ]
            else:
                statements = [
                    """
                    CREATE TABLE IF NOT EXISTS expenses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        amount REAL NOT NULL CHECK(amount > 0),
                        description TEXT NOT NULL,
                        category TEXT NOT NULL,
                        date TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS income (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        amount REAL NOT NULL CHECK(amount > 0),
                        source TEXT NOT NULL,
                        date TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS budgets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL,
                        amount REAL NOT NULL CHECK(amount > 0),
                        month TEXT NOT NULL,
                        UNIQUE(category, month)
                    )
                    """,
                ]

            for statement in statements:
                conn.execute(self._sql(statement))

            conn.execute(self._sql("CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date)"))
            conn.execute(self._sql("CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category)"))
            conn.execute(self._sql("CREATE INDEX IF NOT EXISTS idx_income_date ON income(date)"))

    @staticmethod
    def _validate_iso_date(value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except (TypeError, ValueError) as exc:
            raise ValueError("Date must be in YYYY-MM-DD format.") from exc
        return parsed.isoformat()

    @staticmethod
    def _validate_month(value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m")
        except (TypeError, ValueError) as exc:
            raise ValueError("Month must be in YYYY-MM format.") from exc
        return value

    # ------------------------------------------------------------------ #
    # Expenses
    # ------------------------------------------------------------------ #

    def add_expense(self, amount: float, description: str, category: str, date: Optional[str] = None) -> int:
        if amount <= 0:
            raise ValueError("Expense amount must be positive.")
        description = str(description or "").strip()
        category = str(category or "other").strip().lower() or "other"
        if not description:
            raise ValueError("Expense description is required.")
        expense_date = self._validate_iso_date(date or datetime.now().strftime("%Y-%m-%d"))

        with self._get_conn() as conn:
            cur = conn.execute(
                self._sql("INSERT INTO expenses (amount, description, category, date) VALUES (?,?,?,?) RETURNING id"),
                (float(amount), description, category, expense_date),
            )
            return int(cur.fetchone()["id"] if self.is_postgres else cur.fetchone()[0])

    def delete_expense(self, expense_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(self._sql("DELETE FROM expenses WHERE id = ?"), (expense_id,))
            return cur.rowcount > 0

    def get_expenses(self, days: int = 30, category: Optional[str] = None) -> list[dict]:
        days = max(1, int(days))
        start_date = (date.today() - timedelta(days=days - 1)).isoformat()
        query = "SELECT * FROM expenses WHERE date >= ?"
        params: list[Any] = [start_date]
        if category:
            query += " AND category = ?"
            params.append(category.strip().lower())
        query += " ORDER BY date DESC, id DESC"
        with self._get_conn() as conn:
            rows = conn.execute(self._sql(query), params).fetchall()
        return [dict(row) for row in rows]

    def get_all_expenses(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(self._sql("SELECT * FROM expenses ORDER BY date ASC, id ASC")).fetchall()
        return [dict(row) for row in rows]

    def get_spending_by_category(self, days: int = 30) -> dict[str, float]:
        start_date = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                self._sql(
                    """SELECT category, SUM(amount) AS total
                       FROM expenses WHERE date >= ?
                       GROUP BY category ORDER BY total DESC"""
                ),
                (start_date,),
            ).fetchall()
        return {row["category"]: float(row["total"]) for row in rows}

    # ------------------------------------------------------------------ #
    # Income
    # ------------------------------------------------------------------ #

    def add_income(self, amount: float, source: str, date: Optional[str] = None) -> int:
        if amount <= 0:
            raise ValueError("Income amount must be positive.")
        source = str(source or "").strip()
        if not source:
            raise ValueError("Income source is required.")
        income_date = self._validate_iso_date(date or datetime.now().strftime("%Y-%m-%d"))
        with self._get_conn() as conn:
            cur = conn.execute(
                self._sql("INSERT INTO income (amount, source, date) VALUES (?,?,?) RETURNING id"),
                (float(amount), source, income_date),
            )
            return int(cur.fetchone()["id"] if self.is_postgres else cur.fetchone()[0])

    def delete_income(self, income_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(self._sql("DELETE FROM income WHERE id = ?"), (income_id,))
            return cur.rowcount > 0

    def get_income(self, days: int = 3650) -> list[dict]:
        start_date = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                self._sql("SELECT * FROM income WHERE date >= ? ORDER BY date DESC, id DESC"),
                (start_date,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_all_income(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(self._sql("SELECT * FROM income ORDER BY date ASC, id ASC")).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Budgets
    # ------------------------------------------------------------------ #

    def set_budget(self, category: str, amount: float, month: Optional[str] = None) -> None:
        if amount <= 0:
            raise ValueError("Budget amount must be positive.")
        category = str(category or "").strip().lower()
        if not category:
            raise ValueError("Budget category is required.")
        month = self._validate_month(month or datetime.now().strftime("%Y-%m"))
        with self._get_conn() as conn:
            statement = (
                "INSERT INTO budgets (category, amount, month) VALUES (?,?,?) "
                "ON CONFLICT(category, month) DO UPDATE SET amount = "
                + ("EXCLUDED.amount" if self.is_postgres else "excluded.amount")
            )
            conn.execute(self._sql(statement), (category, float(amount), month))

    def get_budgets(self, month: Optional[str] = None) -> list[dict]:
        month = self._validate_month(month or datetime.now().strftime("%Y-%m"))
        with self._get_conn() as conn:
            rows = conn.execute(self._sql("SELECT * FROM budgets WHERE month = ? ORDER BY category"), (month,)).fetchall()
        return [dict(row) for row in rows]

    def get_budget_vs_actual(self, month: Optional[str] = None) -> list[dict]:
        month = self._validate_month(month or datetime.now().strftime("%Y-%m"))
        year, mon = map(int, month.split("-"))
        next_month = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
        start = f"{month}-01"
        end = (next_month - timedelta(days=1)).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                self._sql(
                    """SELECT b.category, b.amount AS budget,
                              COALESCE(SUM(e.amount), 0) AS spent,
                              b.amount - COALESCE(SUM(e.amount), 0) AS remaining
                       FROM budgets b
                       LEFT JOIN expenses e ON e.category = b.category
                         AND e.date >= ? AND e.date <= ?
                       WHERE b.month = ?
                       GROUP BY b.category, b.amount ORDER BY b.category"""
                ),
                (start, end, month),
            ).fetchall()

        result = []
        for row in rows:
            budget = float(row["budget"])
            spent = float(row["spent"])
            result.append(
                {
                    "category": row["category"],
                    "budget": budget,
                    "spent": spent,
                    "remaining": budget - spent,
                    "utilization_pct": (spent / budget * 100.0) if budget else 0.0,
                    "over_budget": spent > budget,
                }
            )
        return result

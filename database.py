"""Persistence layer for the Personal Finance Assistant.

SQLite is used for local development. Production deployments use persistent
PostgreSQL (for example Neon on Vercel). Every record is scoped to an
anonymous browser user_id so users do not see each other's financial data.
"""

from __future__ import annotations

import os
import re
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


def _validate_user_id(user_id: str) -> str:
    value = str(user_id or "").strip()
    if not value:
        raise ValueError("user_id is required.")
    if len(value) > 100:
        raise ValueError("user_id is too long.")
    return value


class ExpenseDatabase:
    """Repository supporting SQLite locally and PostgreSQL in production."""

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
                        user_id TEXT NOT NULL,
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
                        user_id TEXT NOT NULL,
                        amount DOUBLE PRECISION NOT NULL CHECK(amount > 0),
                        source TEXT NOT NULL,
                        date TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS budgets (
                        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        category TEXT NOT NULL,
                        amount DOUBLE PRECISION NOT NULL CHECK(amount > 0),
                        month TEXT NOT NULL,
                        CONSTRAINT budgets_user_category_month_key UNIQUE(user_id, category, month)
                    )
                    """,
                ]
            else:
                statements = [
                    """
                    CREATE TABLE IF NOT EXISTS expenses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
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
                        user_id TEXT NOT NULL,
                        amount REAL NOT NULL CHECK(amount > 0),
                        source TEXT NOT NULL,
                        date TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS budgets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        category TEXT NOT NULL,
                        amount REAL NOT NULL CHECK(amount > 0),
                        month TEXT NOT NULL,
                        UNIQUE(user_id, category, month)
                    )
                    """,
                ]

            for statement in statements:
                conn.execute(self._sql(statement))

            # Migrate an older deployed schema that did not have user_id.
            for table in ("expenses", "income", "budgets"):
                if self.is_postgres:
                    conn.execute(self._sql(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'legacy'"
                    ))
                else:
                    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if "user_id" not in columns:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy'")

            conn.execute(self._sql("CREATE INDEX IF NOT EXISTS idx_expenses_user_date ON expenses(user_id, date)"))
            conn.execute(self._sql("CREATE INDEX IF NOT EXISTS idx_expenses_user_category ON expenses(user_id, category)"))
            conn.execute(self._sql("CREATE INDEX IF NOT EXISTS idx_income_user_date ON income(user_id, date)"))
            conn.execute(self._sql("CREATE INDEX IF NOT EXISTS idx_budgets_user_month ON budgets(user_id, month)"))

            # Older Postgres deployments had UNIQUE(category, month). Remove
            # that old constraint so different users can have the same budget.
            if self.is_postgres:
                rows = conn.execute("""
                    SELECT con.conname
                    FROM pg_constraint con
                    JOIN pg_class rel ON rel.oid = con.conrelid
                    WHERE rel.relname = 'budgets'
                      AND con.contype = 'u'
                      AND (
                          SELECT array_agg(att.attname ORDER BY x.ordinality)::text[]
                          FROM unnest(con.conkey) WITH ORDINALITY AS x(attnum, ordinality)
                          JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = x.attnum
                      ) = ARRAY['category','month']::text[]
                """).fetchall()
                for row in rows:
                    conn.execute(f'ALTER TABLE budgets DROP CONSTRAINT IF EXISTS "{row["conname"]}"')
                conn.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conrelid = 'budgets'::regclass
                              AND contype = 'u'
                              AND conname = 'budgets_user_category_month_key'
                        ) THEN
                            ALTER TABLE budgets ADD CONSTRAINT budgets_user_category_month_key UNIQUE (user_id, category, month);
                        END IF;
                    END $$;
                """)

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

    # Expenses -----------------------------------------------------------
    def add_expense(self, user_id: str, amount: float, description: str, category: str, date: Optional[str] = None) -> int:
        user_id = _validate_user_id(user_id)
        if amount <= 0:
            raise ValueError("Expense amount must be positive.")
        description = str(description or "").strip()
        category = str(category or "other").strip().lower() or "other"
        if not description:
            raise ValueError("Expense description is required.")
        expense_date = self._validate_iso_date(date or datetime.now().strftime("%Y-%m-%d"))

        with self._get_conn() as conn:
            cur = conn.execute(
                self._sql("INSERT INTO expenses (user_id, amount, description, category, date) VALUES (?,?,?,?,?) RETURNING id"),
                (user_id, float(amount), description, category, expense_date),
            )
            row = cur.fetchone()
            return int(row["id"] if self.is_postgres else row[0])

    def delete_expense(self, user_id: str, expense_id: int) -> bool:
        user_id = _validate_user_id(user_id)
        with self._get_conn() as conn:
            cur = conn.execute(self._sql("DELETE FROM expenses WHERE id = ? AND user_id = ?"), (expense_id, user_id))
            return cur.rowcount > 0

    def get_expenses(self, user_id: str, days: int = 30, category: Optional[str] = None) -> list[dict]:
        user_id = _validate_user_id(user_id)
        days = max(1, int(days))
        start_date = (date.today() - timedelta(days=days - 1)).isoformat()
        query = "SELECT * FROM expenses WHERE user_id = ? AND date >= ?"
        params: list[Any] = [user_id, start_date]
        if category:
            query += " AND category = ?"
            params.append(category.strip().lower())
        query += " ORDER BY date DESC, id DESC"
        with self._get_conn() as conn:
            rows = conn.execute(self._sql(query), params).fetchall()
        return [dict(row) for row in rows]

    def get_all_expenses(self, user_id: str) -> list[dict]:
        user_id = _validate_user_id(user_id)
        with self._get_conn() as conn:
            rows = conn.execute(self._sql("SELECT * FROM expenses WHERE user_id = ? ORDER BY date ASC, id ASC"), (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_spending_by_category(self, user_id: str, days: int = 30) -> dict[str, float]:
        user_id = _validate_user_id(user_id)
        start_date = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                self._sql("""SELECT category, SUM(amount) AS total
                    FROM expenses WHERE user_id = ? AND date >= ?
                    GROUP BY category ORDER BY total DESC"""),
                (user_id, start_date),
            ).fetchall()
        return {row["category"]: float(row["total"]) for row in rows}

    # Income -------------------------------------------------------------
    def add_income(self, user_id: str, amount: float, source: str, date: Optional[str] = None) -> int:
        user_id = _validate_user_id(user_id)
        if amount <= 0:
            raise ValueError("Income amount must be positive.")
        source = str(source or "").strip()
        if not source:
            raise ValueError("Income source is required.")
        income_date = self._validate_iso_date(date or datetime.now().strftime("%Y-%m-%d"))
        with self._get_conn() as conn:
            cur = conn.execute(
                self._sql("INSERT INTO income (user_id, amount, source, date) VALUES (?,?,?,?) RETURNING id"),
                (user_id, float(amount), source, income_date),
            )
            row = cur.fetchone()
            return int(row["id"] if self.is_postgres else row[0])

    def delete_income(self, user_id: str, income_id: int) -> bool:
        user_id = _validate_user_id(user_id)
        with self._get_conn() as conn:
            cur = conn.execute(self._sql("DELETE FROM income WHERE id = ? AND user_id = ?"), (income_id, user_id))
            return cur.rowcount > 0

    def get_income(self, user_id: str, days: int = 3650) -> list[dict]:
        user_id = _validate_user_id(user_id)
        start_date = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                self._sql("SELECT * FROM income WHERE user_id = ? AND date >= ? ORDER BY date DESC, id DESC"),
                (user_id, start_date),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_all_income(self, user_id: str) -> list[dict]:
        user_id = _validate_user_id(user_id)
        with self._get_conn() as conn:
            rows = conn.execute(self._sql("SELECT * FROM income WHERE user_id = ? ORDER BY date ASC, id ASC"), (user_id,)).fetchall()
        return [dict(row) for row in rows]

    # Budgets ------------------------------------------------------------
    def set_budget(self, user_id: str, category: str, amount: float, month: Optional[str] = None) -> None:
        user_id = _validate_user_id(user_id)
        if amount <= 0:
            raise ValueError("Budget amount must be positive.")
        category = str(category or "").strip().lower()
        if not category:
            raise ValueError("Budget category is required.")
        month = self._validate_month(month or datetime.now().strftime("%Y-%m"))
        with self._get_conn() as conn:
            statement = (
                "INSERT INTO budgets (user_id, category, amount, month) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id, category, month) DO UPDATE SET amount = "
                + ("EXCLUDED.amount" if self.is_postgres else "excluded.amount")
            )
            conn.execute(self._sql(statement), (user_id, category, float(amount), month))

    def get_budgets(self, user_id: str, month: Optional[str] = None) -> list[dict]:
        user_id = _validate_user_id(user_id)
        month = self._validate_month(month or datetime.now().strftime("%Y-%m"))
        with self._get_conn() as conn:
            rows = conn.execute(self._sql("SELECT * FROM budgets WHERE user_id = ? AND month = ? ORDER BY category"), (user_id, month)).fetchall()
        return [dict(row) for row in rows]

    def get_budget_vs_actual(self, user_id: str, month: Optional[str] = None) -> list[dict]:
        user_id = _validate_user_id(user_id)
        month = self._validate_month(month or datetime.now().strftime("%Y-%m"))
        year, mon = map(int, month.split("-"))
        next_month = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
        start = f"{month}-01"
        end = (next_month - timedelta(days=1)).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                self._sql("""SELECT b.category, b.amount AS budget,
                              COALESCE(SUM(e.amount), 0) AS spent,
                              b.amount - COALESCE(SUM(e.amount), 0) AS remaining
                       FROM budgets b
                       LEFT JOIN expenses e ON e.user_id = b.user_id AND e.category = b.category
                         AND e.date >= ? AND e.date <= ?
                       WHERE b.user_id = ? AND b.month = ?
                       GROUP BY b.category, b.amount ORDER BY b.category"""),
                (start, end, user_id, month),
            ).fetchall()

        result = []
        for row in rows:
            budget = float(row["budget"])
            spent = float(row["spent"])
            result.append({
                "category": row["category"],
                "budget": budget,
                "spent": spent,
                "remaining": budget - spent,
                "utilization_pct": (spent / budget * 100.0) if budget else 0.0,
                "over_budget": spent > budget,
            })
        return result

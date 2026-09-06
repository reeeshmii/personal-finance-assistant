"""Pure financial analytics over normalized transaction records.

All calculations happen server-side from raw numeric values. Values are rounded
only in the JSON response layer, not while aggregating.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from statistics import median
from typing import Any, Iterable, Optional


def _money(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception:
        return Decimal("0")
    return number if number.is_finite() else Decimal("0")


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _valid_expenses(expenses: Iterable[dict]) -> tuple[list[dict], int]:
    valid: list[dict] = []
    rejected = 0
    for row in expenses:
        amount = _money(row.get("amount"))
        tx_date = _parse_date(row.get("date"))
        if amount <= 0 or tx_date is None:
            rejected += 1
            continue
        valid.append({**row, "amount": amount, "date": tx_date})
    return valid, rejected


def _valid_income(income: Iterable[dict]) -> tuple[list[dict], int]:
    valid: list[dict] = []
    rejected = 0
    for row in income:
        amount = _money(row.get("amount"))
        tx_date = _parse_date(row.get("date"))
        if amount <= 0 or tx_date is None:
            rejected += 1
            continue
        valid.append({**row, "amount": amount, "date": tx_date})
    return valid, rejected


def _month_key(value: date) -> str:
    return value.strftime("%Y-%m")


def _month_label(month: str) -> str:
    return datetime.strptime(month, "%Y-%m").strftime("%b %Y")


def _month_range(start: date, end: date) -> list[str]:
    current = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    months: list[str] = []
    while current <= last:
        months.append(_month_key(current))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def _round(value: Decimal, places: int = 2) -> float:
    return float(value.quantize(Decimal("1." + "0" * places)))


def _pct(numerator: Decimal, denominator: Decimal) -> Optional[float]:
    if denominator == 0:
        return None
    return _round((numerator / denominator) * Decimal("100"), 2)


def build_analytics(
    expenses: Iterable[dict],
    income: Iterable[dict],
    budgets: Iterable[dict] = (),
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    """Build a complete analytics snapshot.

    Input -> validation -> optional date filtering -> aggregation -> metrics.
    Calendar months between the first and last in-range transaction are retained,
    including zero-activity months, so monthly averages are not biased upward.
    """
    valid_expenses, rejected_expenses = _valid_expenses(expenses)
    valid_income, rejected_income = _valid_income(income)

    requested_start = _parse_date(start_date) if start_date else None
    requested_end = _parse_date(end_date) if end_date else None
    if requested_start and requested_end and requested_start > requested_end:
        raise ValueError("start_date must be on or before end_date")

    all_dates = [r["date"] for r in (*valid_expenses, *valid_income)]
    data_start = min(all_dates) if all_dates else None
    data_end = max(all_dates) if all_dates else None

    effective_start = requested_start or data_start
    effective_end = requested_end or data_end
    if effective_start and effective_end:
        valid_expenses = [r for r in valid_expenses if effective_start <= r["date"] <= effective_end]
        valid_income = [r for r in valid_income if effective_start <= r["date"] <= effective_end]

    total_income = sum((r["amount"] for r in valid_income), Decimal("0"))
    total_expenses = sum((r["amount"] for r in valid_expenses), Decimal("0"))
    savings = total_income - total_expenses

    if effective_start and effective_end:
        months = _month_range(effective_start, effective_end)
    else:
        months = []

    expense_by_category: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    transaction_amounts: list[Decimal] = []
    for row in valid_expenses:
        category = str(row.get("category") or "uncategorized").strip().lower() or "uncategorized"
        expense_by_category[category] += row["amount"]
        transaction_amounts.append(row["amount"])

    monthly_income: dict[str, Decimal] = {m: Decimal("0") for m in months}
    monthly_expenses: dict[str, Decimal] = {m: Decimal("0") for m in months}
    for row in valid_income:
        key = _month_key(row["date"])
        if key in monthly_income:
            monthly_income[key] += row["amount"]
    for row in valid_expenses:
        key = _month_key(row["date"])
        if key in monthly_expenses:
            monthly_expenses[key] += row["amount"]

    monthly = []
    for month in months:
        inc = monthly_income[month]
        exp = monthly_expenses[month]
        net = inc - exp
        monthly.append(
            {
                "month": month,
                "label": _month_label(month),
                "income": _round(inc),
                "expenses": _round(exp),
                "savings": _round(net),
                "savings_rate": _pct(net, inc),
            }
        )

    # Month-over-month expense changes, including zero-activity months.
    for idx, row in enumerate(monthly):
        if idx == 0:
            row["expense_mom_change_pct"] = None
            row["income_mom_change_pct"] = None
            continue
        previous = monthly[idx - 1]
        prev_exp = Decimal(str(previous["expenses"]))
        prev_inc = Decimal(str(previous["income"]))
        row["expense_mom_change_pct"] = _pct(Decimal(str(row["expenses"])) - prev_exp, prev_exp)
        row["income_mom_change_pct"] = _pct(Decimal(str(row["income"])) - prev_inc, prev_inc)

    category_rows = [
        {
            "category": category,
            "amount": _round(amount),
            "percentage": _pct(amount, total_expenses) or 0.0,
        }
        for category, amount in sorted(expense_by_category.items(), key=lambda item: item[1], reverse=True)
    ]

    avg_monthly_income = (total_income / Decimal(len(months))) if months else Decimal("0")
    avg_monthly_expenses = (total_expenses / Decimal(len(months))) if months else Decimal("0")
    avg_transaction = (total_expenses / Decimal(len(transaction_amounts))) if transaction_amounts else Decimal("0")

    largest = sorted(valid_expenses, key=lambda row: row["amount"], reverse=True)[:5]
    largest_transactions = [
        {
            "id": row.get("id"),
            "amount": _round(row["amount"]),
            "description": row.get("description") or "Expense",
            "category": row.get("category") or "uncategorized",
            "date": row["date"].isoformat(),
        }
        for row in largest
    ]

    unusual_transactions: list[dict] = []
    if transaction_amounts:
        med = median(transaction_amounts)
        threshold = med * Decimal("2")
        for row in sorted(valid_expenses, key=lambda item: item["amount"], reverse=True):
            if row["amount"] >= threshold and row["amount"] > Decimal("0"):
                unusual_transactions.append(
                    {
                        "id": row.get("id"),
                        "amount": _round(row["amount"]),
                        "description": row.get("description") or "Expense",
                        "category": row.get("category") or "uncategorized",
                        "date": row["date"].isoformat(),
                    }
                )
            if len(unusual_transactions) >= 5:
                break

    # Budget metrics are meaningful only when budgets exist for the relevant month.
    budget_rows: list[dict] = []
    for budget in budgets:
        month = str(budget.get("month") or "")
        try:
            budget_month = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            continue
        category = str(budget.get("category") or "uncategorized").strip().lower() or "uncategorized"
        amount = _money(budget.get("amount"))
        if amount <= 0:
            continue
        spent = monthly_expenses.get(_month_key(budget_month), Decimal("0")) if _month_key(budget_month) in monthly_expenses else Decimal("0")
        budget_rows.append(
            {
                "month": month,
                "category": category,
                "budget": _round(amount),
                "spent": _round(spent),
                "remaining": _round(amount - spent),
                "utilization_pct": _pct(spent, amount) or 0.0,
                "over_budget": spent > amount,
                "approaching_limit": amount > 0 and spent >= amount * Decimal("0.8") and spent <= amount,
            }
        )

    # Trend interpretation is intentionally data-driven rather than generic.
    expense_change = None
    if len(monthly) >= 2:
        first = Decimal(str(monthly[0]["expenses"]))
        last = Decimal(str(monthly[-1]["expenses"]))
        if first == 0 and last == 0:
            expense_change = "stable"
        elif last > first:
            expense_change = "increasing"
        elif last < first:
            expense_change = "decreasing"
        else:
            expense_change = "stable"

    largest_increase = None
    largest_decrease = None
    if len(monthly) >= 2:
        changes = []
        for idx in range(1, len(monthly)):
            change = Decimal(str(monthly[idx]["expenses"])) - Decimal(str(monthly[idx - 1]["expenses"]))
            changes.append((change, monthly[idx]["month"], monthly[idx - 1]["month"]))
        if changes:
            inc = max(changes, key=lambda item: item[0])
            dec = min(changes, key=lambda item: item[0])
            if inc[0] > 0:
                largest_increase = {"month": inc[1], "from_month": inc[2], "change": _round(inc[0])}
            if dec[0] < 0:
                largest_decrease = {"month": dec[1], "from_month": dec[2], "change": _round(dec[0])}

    insights: list[str] = []
    if not valid_expenses and not valid_income:
        insights.append("Add income or expense transactions to generate personalized insights.")
    if category_rows:
        top = category_rows[0]
        insights.append(f"{top['category'].title()} is your largest spending category at ₹{top['amount']:,.2f} ({top['percentage']:.1f}% of expenses).")
        if top["percentage"] >= 30:
            insights.append(f"{top['category'].title()} consumes a large share of total spending; review this category against your budget or goals.")
    if expense_change:
        insights.append(f"Spending is {expense_change} across the available monthly history.")
    if largest_increase:
        insights.append(f"The largest month-over-month spending increase was ₹{largest_increase['change']:,.2f} in {largest_increase['month']}.")
    if savings < 0 and total_income > 0:
        insights.append(f"Expenses exceed recorded income by ₹{abs(savings):,.2f} in the selected period.")
    elif total_income > 0 and savings >= 0:
        insights.append(f"Recorded savings are ₹{savings:,.2f}, a {_pct(savings, total_income) or 0:.1f}% savings rate.")
    if unusual_transactions:
        insights.append(f"{len(unusual_transactions)} high-value transaction(s) are at least twice the median expense amount.")
    over_budget = [row for row in budget_rows if row["over_budget"]]
    approaching = [row for row in budget_rows if row["approaching_limit"]]
    if over_budget:
        insights.append(f"{len(over_budget)} budget categor{'y' if len(over_budget) == 1 else 'ies'} exceed the recorded spending limit.")
    elif approaching:
        insights.append(f"{len(approaching)} budget categor{'y' if len(approaching) == 1 else 'ies'} are at or above 80% utilization.")

    return {
        "period": {
            "start_date": effective_start.isoformat() if effective_start else None,
            "end_date": effective_end.isoformat() if effective_end else None,
            "months_count": len(months),
        },
        "summary": {
            "total_income": _round(total_income),
            "total_expenses": _round(total_expenses),
            "net_cash_flow": _round(savings),
            "savings": _round(savings),
            "savings_rate": _pct(savings, total_income),
            "average_monthly_income": _round(avg_monthly_income),
            "average_monthly_expenses": _round(avg_monthly_expenses),
            "average_transaction_amount": _round(avg_transaction),
            "expense_transaction_count": len(valid_expenses),
            "income_transaction_count": len(valid_income),
        },
        "categories": category_rows,
        "top_spending_categories": category_rows[:5],
        "largest_transactions": largest_transactions,
        "unusual_transactions": unusual_transactions,
        "monthly": monthly,
        "budget": {
            "available": bool(budget_rows),
            "items": budget_rows,
            "over_budget_count": len(over_budget),
            "approaching_limit_count": len(approaching),
        },
        "trends": {
            "spending_direction": expense_change,
            "largest_increase": largest_increase,
            "largest_decrease": largest_decrease,
        },
        "insights": insights,
        "validation": {
            "invalid_expenses_ignored": rejected_expenses,
            "invalid_income_ignored": rejected_income,
        },
    }

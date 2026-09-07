"""FastAPI application for the Personal Finance Assistant."""

from __future__ import annotations

import os
import socket
from contextlib import closing

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from analytics import build_analytics
from database import ExpenseDatabase
from nlp_processor import FinanceNLP

app = FastAPI(title="Personal Finance Assistant", version="4.0")

# Same-origin Vercel requests do not need CORS. These origins support local dev
# and can be extended with CORS_ORIGINS="https://your-domain.vercel.app".
_default_origins = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://127.0.0.1:3000"
cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-User-ID"],
)

db = ExpenseDatabase()
nlp = FinanceNLP()


class ChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1, max_length=100)


class ExpenseResponse(BaseModel):
    success: bool
    message: str
    expense_id: int | None = None
    income_id: int | None = None
    insights: str | None = None


class BudgetRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=80)
    amount: float = Field(..., gt=0, finite=True)
    month: str | None = None

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("month")
    @classmethod
    def validate_month(cls, value: str | None) -> str | None:
        if value is None:
            return value
        import re
        if not re.fullmatch(r"\d{4}-\d{2}", value):
            raise ValueError("month must use YYYY-MM format")
        return value


@app.get("/", include_in_schema=False)
async def serve_frontend():
    return FileResponse("index.html", media_type="text/html")


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": "postgresql" if db.is_postgres else "sqlite",
        "groq_configured": bool(nlp.groq_api_key),
    }


def _load_analytics(user_id: str, days: int = 3650) -> dict:
    expenses = db.get_expenses(user_id=user_id, days=days)
    income = db.get_income(user_id=user_id, days=days)
    # Budgets are month-specific; loading the current set lets analytics expose
    # budget utilization without inventing budget data.
    budgets = db.get_budgets(user_id=user_id)
    return build_analytics(expenses, income, budgets)


_SUMMARY_TRIGGERS = ("summary", "report", "spending", "how much", "show", "total")
_GREET_TRIGGERS = ("hello", "hi", "hey", "help")


@app.post("/api/chat", response_model=ExpenseResponse)
async def chat_endpoint(chat_message: ChatMessage):
    raw = chat_message.message.strip()
    lower = raw.lower()

    try:
        # 1. Deterministic routing begins with transaction classification.
        transaction = nlp.parse_transaction(raw)
        tx_type = transaction.get("type")

        if tx_type == "expense" and transaction.get("amount", 0) > 0:
            expense_id = db.add_expense(
                user_id=chat_message.user_id,
                amount=transaction["amount"],
                description=transaction["description"],
                category=transaction["category"],
                date=transaction["date"],
            )
            recent = db.get_expenses(user_id=chat_message.user_id, days=7)
            return ExpenseResponse(
                success=True,
                message=(
                    f"Added ₹{transaction['amount']:.2f} for {transaction['description']} "
                    f"({transaction['category']}) on {transaction['date']}."
                ),
                expense_id=expense_id,
                insights=nlp.generate_insights(recent),
            )

        if tx_type == "income" and transaction.get("amount", 0) > 0:
            income_id = db.add_income(
                user_id=chat_message.user_id,
                amount=transaction["amount"],
                source=transaction["source"],
                date=transaction["date"],
            )
            return ExpenseResponse(
                success=True,
                message=(
                    f"Added ₹{transaction['amount']:.2f} income from {transaction['source']} "
                    f"on {transaction['date']}."
                ),
                income_id=income_id,
                insights="Income is now included in savings and cash-flow analytics.",
            )

        # 2. Deterministic financial summaries use application calculations.
        if any(trigger in lower for trigger in _SUMMARY_TRIGGERS):
            days = 30
            if "week" in lower:
                days = 7
            elif "year" in lower:
                days = 365
            analytics = _load_analytics(chat_message.user_id, days)
            summary = analytics["summary"]
            if summary["expense_transaction_count"] == 0 and summary["income_transaction_count"] == 0:
                return ExpenseResponse(
                    success=True,
                    message="No financial transactions found for this period.",
                    insights="Try 'I spent ₹250 on lunch' or 'I received ₹5000 salary'.",
                )
            return ExpenseResponse(
                success=True,
                message=(
                    f"Financial summary: income ₹{summary['total_income']:,.2f}, "
                    f"expenses ₹{summary['total_expenses']:,.2f}, "
                    f"net cash flow ₹{summary['net_cash_flow']:,.2f}."
                ),
                insights=" ".join(analytics["insights"][:3]) or None,
            )

        # 3. Help/greetings remain deterministic.
        if any(trigger in lower for trigger in _GREET_TRIGGERS):
            return ExpenseResponse(
                success=True,
                message=(
                    "Hello! I'm your personal finance assistant.\n\n"
                    "• Track: 'I spent ₹25 on lunch'\n"
                    "• Income: 'I received ₹5000 salary'\n"
                    "• View: 'Show my spending this month'\n"
                    "• Analyse: 'What is my savings rate?'\n"
                    "• Budget: 'Set ₹3000 budget for groceries'"
                ),
            )

        # 4. Context-aware Groq response. The model receives calculated analytics,
        # never raw secrets, and is instructed not to invent financial facts.
        analytics = _load_analytics(3650)
        answer = nlp.answer_finance_question(raw, analytics)
        if answer:
            return ExpenseResponse(success=True, message=answer)

        return ExpenseResponse(
            success=True,
            message=(
                "I can track income and expenses and explain your financial analytics. "
                "Try 'I spent ₹250 on dinner', 'I received ₹5000 salary', or "
                "'What is my savings rate?'"
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        print(f"Chat error: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=500, detail="Unable to process the request right now.") from exc


@app.get("/api/analytics")
async def get_analytics(
    days: int = Query(3650, ge=1, le=36500),
    x_user_id: str = Header(..., alias="X-User-ID"),
):
    try:
        return _load_analytics(x_user_id, days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        print(f"Analytics error: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=500, detail="Unable to calculate analytics.") from exc


@app.get("/api/expenses")
async def get_expenses(
    days: int = Query(30, ge=1, le=36500),
    category: str | None = None,
    x_user_id: str = Header(..., alias="X-User-ID"),
):
    try:
        return {"expenses": db.get_expenses(user_id=x_user_id, days=days, category=category)}
    except Exception as exc:
        print(f"Expense query error: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=500, detail="Unable to load expenses.") from exc


@app.delete("/api/expenses/{expense_id}")
async def delete_expense(expense_id: int, x_user_id: str = Header(..., alias="X-User-ID")):
    deleted = db.delete_expense(user_id=x_user_id, expense_id=expense_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Expense not found")
    return {"success": True, "message": f"Expense {expense_id} deleted"}


@app.get("/api/income")
async def get_income(
    days: int = Query(3650, ge=1, le=36500),
    x_user_id: str = Header(..., alias="X-User-ID"),
):
    return {"income": db.get_income(user_id=x_user_id, days=days)}


@app.delete("/api/income/{income_id}")
async def delete_income(income_id: int, x_user_id: str = Header(..., alias="X-User-ID")):
    deleted = db.delete_income(user_id=x_user_id, income_id=income_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Income transaction not found")
    return {"success": True, "message": f"Income {income_id} deleted"}


@app.get("/api/spending-by-category")
async def get_spending_by_category(
    days: int = Query(30, ge=1, le=36500),
    x_user_id: str = Header(..., alias="X-User-ID"),
):
    return db.get_spending_by_category(user_id=x_user_id, days=days)


@app.post("/api/budgets")
async def set_budget(budget: BudgetRequest, x_user_id: str = Header(..., alias="X-User-ID")):
    try:
        db.set_budget(x_user_id, budget.category, budget.amount, budget.month)
        return {"success": True, "message": f"Budget set for {budget.category}"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/budgets")
async def get_budgets(month: str | None = None, x_user_id: str = Header(..., alias="X-User-ID")):
    try:
        return {"budgets": db.get_budgets(x_user_id, month)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/budgets/vs-actual")
async def get_budget_vs_actual(month: str | None = None, x_user_id: str = Header(..., alias="X-User-ID")):
    try:
        return {"data": db.get_budget_vs_actual(x_user_id, month)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.getsockname()[1]


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", _find_free_port()))
    print(f"Finance Assistant running at http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)

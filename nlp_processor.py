"""Natural-language transaction parsing and AI responses using Groq.

Groq is server-side only. If GROQ_API_KEY is missing or the API fails, the
application falls back to deterministic local parsing and rule-based insights.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "food": ["lunch", "dinner", "breakfast", "snack", "meal"],
    "groceries": ["grocery", "groceries", "supermarket", "vegetable", "fruit", "milk", "bread"],
    "merchants": ["blinkit", "zepto", "bigbasket", "zomato","swiggy","amazon","flipkart"],
    "transportation": ["bus", "train", "taxi", "uber", "ola", "auto", "fuel", "petrol", "metro"],
    "entertainment": ["movie", "game", "concert", "netflix", "hotstar", "theater"],
    "shopping": ["buy", "purchase", "shopping", "clothes", "amazon", "flipkart", "mall"],
    "bills": ["bill", "electricity", "water", "internet", "phone", "recharge"],
    "healthcare": ["doctor", "hospital", "medicine", "pharmacy", "clinic"],
    "education": ["book", "course", "tuition", "fees", "college", "school"],
    "travel": ["flight", "hotel", "vacation", "trip", "airbnb"],
    "dining": ["restaurant", "cafe", "coffee", "zomato", "swiggy"],
    "utilities": ["gas", "maintenance", "repair"],
    "subscriptions": ["subscription", "spotify", "youtube", "prime", "membership"],
}
ALL_CATEGORIES = list(CATEGORY_KEYWORDS.keys()) + ["other"]

_AMOUNT_PATTERNS = [
    r"₹\s*(\d+(?:\.\d{1,2})?)",
    r"(\d+(?:\.\d{1,2})?)\s*₹",
    r"(?:spend|spent|pay|paid|buy|bought|cost|salary|earned|earn|received|got|income)\s+(?:rs\.?\s*)?(\d+(?:\.\d{1,2})?)",
    r"rs\.?\s*(\d+(?:\.\d{1,2})?)",
    r"\b(\d+(?:\.\d{1,2})?)\b",
]
_DATE_KEYWORDS = {"today": 0, "yesterday": 1}
_INCOME_WORDS = ("salary", "income", "earned", "earn", "received", "got paid", "freelance income", "stipend", "bonus")
_EXPENSE_WORDS = ("spent", "spend", "paid", "pay", "bought", "buy", "cost", "expense")


class FinanceNLP:
    """Groq-backed parser with a safe local fallback."""

    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
        self.client = None
        if self.groq_api_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=self.groq_api_key, timeout=15.0, max_retries=1)
                print(f"Groq client initialized ({self.model})")
            except Exception as exc:
                print(f"Groq client initialization failed; local fallback enabled: {exc}")
        else:
            print("No GROQ_API_KEY configured; local parser only")

    def parse_transaction(self, user_input: str) -> Dict:
        if self.client:
            result = self._groq_parse(user_input)
            if result:
                return result
        return self._local_parse_transaction(user_input)
    
    def parse_transaction(self, user_input: str) -> Dict:  
        if self.client:
            result = self._groq_parse(user_input)

        # Only accept Groq result if it successfully identified
        # an actual income or expense transaction.
        if result and result.get("type") in {"expense", "income"}:
            return result
        # If Groq returns unknown/fails, use deterministic local parsing.
        return self._local_parse_transaction(user_input)

    

    def parse_income(self, user_input: str) -> Dict:
        result = self.parse_transaction(user_input)
        if result.get("type") != "income":
            raise ValueError("Input is not an income transaction.")
        return result

    def generate_insights(self, expenses: List[Dict]) -> str:
        if not expenses:
            return "Start adding expenses to get insights!"
        total = sum(float(e.get("amount", 0)) for e in expenses if float(e.get("amount", 0)) > 0)
        if total <= 0:
            return "Add valid expense amounts to generate insights."
        categories: Dict[str, float] = {}
        for e in expenses:
            amount = float(e.get("amount", 0))
            if amount <= 0:
                continue
            category = str(e.get("category") or "other")
            categories[category] = categories.get(category, 0) + amount
        if not categories:
            return "Add valid expense transactions to generate insights."
        top_cat, top_amt = max(categories.items(), key=lambda x: x[1])
        tips = []
        if top_amt / total > 0.5:
            tips.append(f"Over half your recent spending is on {top_cat}.")
        amounts = [float(e.get("amount", 0)) for e in expenses if float(e.get("amount", 0)) > 0]
        if len(amounts) >= 3:
            avg = sum(amounts) / len(amounts)
            last = amounts[0]
            if last > avg * 2:
                tips.append("The latest expense is unusually high compared with the recent transaction average.")
            tips.append(f"Average transaction: ₹{avg:.2f}")
        tips.append(f"Recent total: ₹{total:.2f} | Top: {top_cat} (₹{top_amt:.2f})")
        return "  ".join(tips)

    def answer_finance_question(self, question: str, analytics: dict) -> Optional[str]:
        """Answer a question using only deterministic analytics supplied by the app."""
        if not self.client:
            return None
        context = json.dumps(analytics, ensure_ascii=False, separators=(",", ":"))
        system_prompt = (
            "You are a personal finance assistant. Answer using ONLY the supplied analytics data. "
            "Do not invent transactions, income, budgets, dates, or financial facts. If the data is "
            "insufficient, say so clearly. Keep the answer concise, practical, and non-judgmental. "
            "The application itself performs all financial calculations; do not replace or contradict them."
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Analytics JSON:\n{context}\n\nQuestion: {question}"},
                ],
                temperature=0.2,
                max_completion_tokens=400,
            )
            text = (response.choices[0].message.content or "").strip()
            return text or None
        except Exception as exc:
            print(f"Groq question failed: {type(exc).__name__}: {exc}")
            return None

    def _groq_parse(self, user_input: str) -> Optional[Dict]:
        system_prompt = (
            "Classify a user's financial transaction. Return ONLY valid JSON with exactly these keys: "
            "type (expense, income, or unknown), amount (positive number or 0), "
            "description (string), source (string), category (one of the allowed categories), "
            "date (YYYY-MM-DD). Use today's date if no date is mentioned. "
            "Income examples include salary, stipend, freelance payment, bonus, or money received. "
            "Expense examples include spending, purchases, bills, food, travel, or payments. "
            f"Allowed categories: {', '.join(ALL_CATEGORIES)}."
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input.strip()},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                max_completion_tokens=250,
            )
            raw = response.choices[0].message.content
            if not raw:
                return None
            data = json.loads(raw)
            tx_type = str(data.get("type", "unknown")).lower()
            if tx_type not in {"expense", "income", "unknown"}:
                return None
            amount = float(data.get("amount", 0))
            if amount <= 0 or tx_type == "unknown":
                return {"type": "unknown", "amount": 0}
            tx_date = self._validate_or_today(str(data.get("date", "")))
            category = str(data.get("category", "other")).lower().strip()
            if category not in ALL_CATEGORIES:
                category = "other"
            if tx_type == "income":
                return {
                    "type": "income",
                    "amount": amount,
                    "source": str(data.get("source") or data.get("description") or "Income").strip() or "Income",
                    "description": str(data.get("description") or "Income").strip() or "Income",
                    "date": tx_date,
                }
            return {
                "type": "expense",
                "amount": amount,
                "description": str(data.get("description") or "Expense").strip() or "Expense",
                "category": category,
                "date": tx_date,
            }
        except Exception as exc:
            # Covers missing/invalid API responses, timeouts, rate limits and provider errors.
            print(f"Groq transaction parsing failed: {type(exc).__name__}: {exc}")
            return None

    def _local_parse_transaction(self, user_input: str) -> Dict:
        text = user_input.strip()
        lower = text.lower()
        amount = self._extract_amount(lower)
        if amount <= 0:
            return {"type": "unknown", "amount": 0}
        tx_date = self._extract_date(lower)
        if any(word in lower for word in _INCOME_WORDS) and not any(word in lower for word in _EXPENSE_WORDS):
            source = self._clean_description(text) or "Income"
            return {"type": "income", "amount": amount, "source": source, "description": source, "date": tx_date}
        if any(word in lower for word in _EXPENSE_WORDS):
            return {
        "type": "expense",
        "amount": amount,
        "description": self._clean_description(text) or "Expense",
        "category": self._detect_category(lower),
        "date": tx_date,
         }

# Natural shorthand such as:
# "blinkit 60"
# "uber 150"
# "lunch 250"
# "shampoo 300"
# "movie 500"
#
# If there is a positive amount and no income intent,
# interpret the input as an expense.
        if amount > 0:
            return {
        "type": "expense",
        "amount": amount,
        "description": self._clean_description(text) or "Expense",
        "category": self._detect_category(lower),
        "date": tx_date,
    }
        return {"type": "unknown", "amount": 0}

    def _extract_amount(self, text: str) -> float:
        for pattern in _AMOUNT_PATTERNS:
            match = re.search(pattern, text)
            if match:
                try:
                    value = float(match.group(1))
                    if value > 0:
                        return value
                except ValueError:
                    pass
        return 0.0

    def _detect_category(self, text: str) -> str:
        best_score = 0
        best_cat = "other"
        for category, keywords in CATEGORY_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in text)
            if score > best_score:
                best_score = score
                best_cat = category
        return best_cat

    def _clean_description(self, text: str) -> str:
        text = re.sub(r"₹\s*\d+(?:\.\d{1,2})?", "", text)
        text = re.sub(r"\d+(?:\.\d{1,2})?\s*₹", "", text)
        text = re.sub(r"\brs\.?\s*\d+(?:\.\d{1,2})?", "", text, flags=re.IGNORECASE)
        fillers = r"\b(?:i\s+(?:spend|spent|pay|paid|buy|bought)|paid\s+for|expense|spent|salary|earned|received|income)\b"
        text = re.sub(fillers, "", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(today|yesterday)\b", "", text, flags=re.IGNORECASE)
        return " ".join(text.split()).strip(" ,.")

    def _extract_date(self, text: str) -> str:
        today = datetime.now()
        for keyword, offset in _DATE_KEYWORDS.items():
            if keyword in text:
                return (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        if match:
            return self._validate_or_today(match.group(1))
        return today.strftime("%Y-%m-%d")

    @staticmethod
    def _validate_or_today(value: str) -> str:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return datetime.now().strftime("%Y-%m-%d")

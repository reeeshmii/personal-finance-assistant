# FinSet — Personal Finance Assistant

A lightweight personal finance assistant built with **FastAPI + vanilla JavaScript**, with deterministic server-side financial analytics and optional **Groq-powered natural-language transaction parsing and financial Q&A**.

## What was improved

- Reworked analytics into a dedicated, testable `analytics.py` business-logic layer.
- Added income tracking so savings, cash flow, and savings-rate metrics are based on actual income + expense records.
- Added monthly time-series analytics, category shares, MoM changes, largest/unusual transactions, and budget health.
- Added validation for invalid amounts/dates and safe zero-income handling.
- Migrated the AI layer from OpenAI to the official Groq Python SDK.
- Kept the Groq key server-side and configurable through environment variables.
- Added a deterministic local parser fallback when Groq is unavailable.
- Removed the insecure `/static` mount that could expose project files such as `.env` or the SQLite database.
- Added PostgreSQL support for persistent Vercel deployments while retaining SQLite for local development.
- Added a SQLite → PostgreSQL migration helper.
- Kept the existing FastAPI architecture rather than introducing a large frontend rewrite.
- Added automated analytics/database tests.

## Architecture

```text
Browser (index.html + Chart.js)
          |
          | same-origin /api requests
          v
FastAPI (main.py)
   |          |          |
   |          |          +--> FinanceNLP --> Groq (server-side)
   |          |
   |          +--------------> analytics.py (pure financial calculations)
   |
   +------------------------> database.py
                                  |
                         +--------+--------+
                         |                 |
                      SQLite          PostgreSQL/Neon
                    local only        production
```

### Why this structure?

The UI is still intentionally simple. Financial calculations are not performed in the browser; the backend returns one consistent analytics model. This prevents charts and KPI cards from using different formulas or different date ranges.

## Analytics model

For every analytics response:

**raw records → validation → date filtering → aggregation → metric calculation → JSON → UI**

Important formulas:

- **Net cash flow / savings** = `total income - total expenses`
- **Savings rate** = `(savings / total income) × 100` when income is non-zero; otherwise `N/A`
- **Category share** = `category expense / total expenses × 100`
- **Average monthly income** = `total income / number of calendar months in the analytics period`
- **Average monthly expenses** = `total expenses / number of calendar months in the analytics period`
- **Average transaction amount** = `total expenses / valid expense transaction count`
- **Budget utilization** = `actual spending / budget × 100`

Calendar months with zero transactions between the first and last in-range month are retained, so monthly averages are not artificially inflated.

The analytics layer keeps raw numeric values during aggregation and rounds only when returning/displaying results.

## Data model

### Expenses

`id`, `amount`, `description`, `category`, `date`, `created_at`

### Income

`id`, `amount`, `source`, `date`, `created_at`

### Budgets

`id`, `category`, `amount`, `month`

The original project only stored expenses and budgets. Income was added because savings, net cash flow, and savings rate cannot be calculated truthfully without income records.

## Local setup

Use Python 3.13 (Vercel currently supports Python 3.12–3.14; 3.13 is pinned here).

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
```

For local SQLite development, keep:

```env
DATABASE_URL=sqlite:///finance.db
```

Add your Groq key to `.env` if you want AI parsing/Q&A:

```env
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

Run:

```bash
python main.py
```

The application prints the local URL. You can also use Uvicorn directly:

```bash
uvicorn main:app --reload
```

## Testing

Run:

```bash
python -m unittest discover -s tests -v
```

The tests cover empty data, income-only data, expense-only data, mixed cash flow, zero income, same-day transactions, multi-month averages including zero months, category percentages, invalid values/dates, large amounts, duplicates, and budgets.

## Vercel deployment

The project intentionally does **not** include a `vercel.json`: the current Vercel FastAPI runtime detects `main.py` and the exported `app` automatically.

### 1. Create a persistent PostgreSQL database

For a Vercel deployment, use a managed PostgreSQL service such as **Neon through the Vercel Marketplace**. SQLite files are suitable for local development but are not a reliable production data store for a serverless deployment.

### 2. Set Vercel environment variables

Required:

```text
DATABASE_URL=postgresql://...
GROQ_API_KEY=...
```

Optional:

```text
GROQ_MODEL=llama-3.3-70b-versatile
CORS_ORIGINS=https://your-project.vercel.app
```

Do not commit `.env` or any database file.

### 3. Migrate your existing SQLite data

After creating the Neon database and setting `DATABASE_URL` locally to the PostgreSQL connection string:

```bash
python migrate_sqlite_to_postgres.py /path/to/finance.db
```

The script skips duplicate expense rows based on amount + description + category + date and migrates budgets. It also migrates income if the source database has an income table.

### 4. Deploy

Push the project to GitHub and import it into Vercel, or use the Vercel CLI:

```bash
npm i -g vercel
vercel
vercel --prod
```

Vercel detects the FastAPI `app` exported by `main.py`. No custom build command or `vercel.json` is required for this architecture.

### 5. Verify

After deployment:

- Open the Vercel URL.
- Open `/health`.
- Add an expense through chat.
- Add income through chat, e.g. `I received ₹5000 salary`.
- Open Analytics.
- Check that income, expenses, net cash flow, savings rate, category shares, monthly trends, and insights update.
- Ask a financial question such as `What is my savings rate?`.

## Security notes

- `GROQ_API_KEY` is read only by Python server code.
- The frontend calls `/api/chat`; it never receives the Groq key.
- The previous `StaticFiles(directory=".")` mount was removed so `.env`, `.db`, and source files are not exposed as `/static/*` assets.
- User messages are length-limited by Pydantic.
- Financial calculations are performed by deterministic Python code rather than trusted to LLM output.
- AI responses are instructed to use only the analytics context supplied by the application.

## Framework choices

The current stack is appropriate for this project and does not need a framework rewrite.

If the project grows later, reasonable next steps would be:

- **SQLAlchemy** for a richer database abstraction/migrations.
- **React/Next.js** if the UI becomes substantially more interactive.
- **Alembic** when database schema migrations become frequent.
- **pytest** for a larger test suite.

For the current resume project, introducing all of these at once would add complexity without improving the core result.

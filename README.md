# Qadri Group — AI Supply Chain Assistant

A chatbot that answers natural-language questions about Qadri Group's supply
chain (stock, purchases, issuance, imports, logistics) by translating them
into SQL, running that SQL read-only against the live PostgreSQL database,
and explaining the result in 3–4 professional, decision-oriented lines.

**Stack:** React · FastAPI · LangGraph · OpenAI GPT-4o-mini · PostgreSQL

## How it works

```
User question
  -> FastAPI  POST /api/chat  (or /api/chat/stream — same logic, streamed)
  -> LangGraph workflow (one graph, no multi-agent fan-out):
       understand_question       - fast-path glossary/definition check
       retrieve_business_context - live DB schema + verified business rules
                                    + relevant RAG snippets from the
                                    original knowledge PDFs
       generate_sql               - GPT-4o-mini writes ONE SELECT query
       validate_sql                - safety guard: SELECT-only, known
                                     tables only, row limit enforced
       execute_sql                  - runs read-only, with a statement
                                     timeout, against a SELECT-only DB role
       repair_sql                    - on a guard rejection or DB error,
                                     asks the model for a corrected query
                                     (capped at 2 attempts), looping back
                                     through validate_sql
       generate_answer              - GPT-4o-mini explains the result
       forecast_answer               - forecasting intents only (see below):
                                     explains a projection instead
  -> JSON { answer, sql_used, confidence, session_id, columns, rows }
```

**Forecasting** branches off the same graph, not a separate agent: when
`generate_sql` recognizes a forecast/projection intent, it writes a plain
historical SELECT (one row per month) instead of a final answer, prefixed
with a `FORECAST: horizon=<n>` sentinel line. That SQL runs through the
EXACT SAME `validate_sql`/`execute_sql` as any other query — no new write
path, no relaxed guard — and only the answer stage branches to
`forecast_answer`, which hands the rows to `app/analytics/` (pandas +
statsmodels + statsforecast) for a deterministic, backtested projection.
See "Forecasting / analytics" below for detail.

Safety is layered: (1) the SQL guard rejects anything that isn't a single
read-only `SELECT`/CTE referencing real tables, and (2) even if that were
bypassed, the chatbot's DB connection uses a Postgres role
(`chatbot_ro`) that physically cannot INSERT/UPDATE/DELETE/DROP anything.

## Verified SQL functions

For a few question shapes, the model calls a verified read-only PostgreSQL
function instead of writing equivalent SQL by hand:

| Function | Argument | Returns | Use for |
|---|---|---|---|
| `current_stock_of(search_item text)` | item search term | current stock for the matched item(s) | "current stock of X" / "how much X do we have" |
| `supplier_delay(search_item text)` | item search term | late suppliers and their delay in days | supplier lateness/delay questions about an item |
| `reorder_recommendation(search_item text)` | item search term | reorder timing + recommendation | "when should we reorder / buy X" |

These functions already exist in the `supplychain_automation` database and
are already `GRANT`ed to the read-only `chatbot_ro` role — no setup is
needed beyond the DB-side grant that's already in place. The chatbot calls
them as `SELECT * FROM <function_name>('<search term>')`, which runs
through the **exact same** guard and read-only executor as any other
query — there is no separate code path and no relaxation of the
SELECT-only rule.

`backend/app/knowledge/functions.py` is the single source of truth: each
entry lists the function's name, its fixed one-argument signature, what it
returns, and a one-line routing description. Two other places read from it,
and nothing else needs to change to add a new verified function here (plus
the matching DB-side grant):

- `backend/app/graph/guard.py` allows a `FROM <fn>(...)` reference to pass
  the guard's table-existence check *only* when `<fn>` is on this registry
  *and* the call's argument count matches exactly — an unknown function
  name, or a registered function called with the wrong number of
  arguments, is still rejected like any unknown table. Every other guard
  rule (single statement, SELECT/WITH only, keyword blocklist, LIMIT,
  NULLS LAST) applies unchanged. See `backend/tests/test_guard.py` for the
  approved/unapproved/wrong-arity/normal-table cases.
- `backend/app/knowledge/business_rules.py` renders the registry into the
  system prompt as its own `FUNCTION_CATALOG` block (after `SQL_CONTRACT`,
  before the live schema), telling the model when to call each function.
  Raw text-to-SQL remains the fallback for every other question shape.

The **business rules baked into the system prompt** (`backend/app/knowledge/business_rules.py`)
are verified against the *live* database — not just the original planning
docs — because the real schema diverged from the plan in places (e.g. there
is no `reorder_level` or `Critical` flag anywhere in the database; the
closest real signal is `ab_items.rank`, an ABC classification; `Production`
is a department, not a branch). Where the RAG layer's retrieved PDF content
would conflict with these verified rules, the verified rules always win.

**Confidence** (`backend/app/graph/confidence.py`) reflects how the answer
was produced, not a calibrated probability: 1.0 for a static glossary hit,
0.95 for clean SQL on the first try, 0.75/0.55 if 1/2 repairs were needed,
0.6 for a query that ran correctly but matched nothing, 0.4 while a
clarifying question is pending, 0.0 when nothing usable could be produced.
A forecast tops out at 0.7 (0.5/0.35 at moderate/low backtest confidence)
even at its best — it's a projection, never as trustworthy as an observed
fact — and 0.2 when there wasn't enough history to forecast at all.

## Project layout

```
backend/                FastAPI + LangGraph backend
  app/
    main.py              FastAPI app: POST /api/chat, POST /api/chat/stream
                          (SSE), GET /api/health
    config.py            env var loading (DATABASE_URL, OPENAI_API_KEY, ...)
    session_store.py      pluggable session memory: InMemoryStore (default)
                          or RedisStore (when REDIS_URL is set)
    logging_config.py     structured JSON-line per-request logging
    db/                  live schema introspection + read-only query executor
    graph/
      state.py, nodes.py, workflow.py   the LangGraph state machine
      guard.py                           the SQL safety guard (incl. the
                                         verified-function allow-list)
      confidence.py                      the documented confidence scheme
    llm/                 OpenAI client wrapper (generate/repair/explain/
                          explain_forecast, retry-with-backoff on transient
                          errors)
    knowledge/
      business_rules.py   verified, live-schema business rules (system prompt)
      functions.py          registry of verified read-only SQL functions
                            (current_stock_of, supplier_delay,
                            reorder_recommendation) — single source of
                            truth for guard.py's allow-list and the
                            prompt's function catalog; see "Verified SQL
                            functions" above
      dictionary.py        fast-path glossary answers (no SQL, no DB hit)
      rag.py                TF-IDF retrieval over the original knowledge PDFs
      rag_documents/        the source PDFs/MD files for the RAG corpus
      reference_data/       business_dictionary.json, synonym map, the
                             ORIGINAL planned schema (kept for reference only
                             — NOT authoritative; see business_rules.py)
    analytics/            forecasting — separate from the graph/knowledge
                          layers, no DB code of its own (see below)
      series.py             pure pandas time-series builder from query rows
      forecasting.py         backtested model bank + selection (statsmodels
                              SARIMA/Holt-Winters, statsforecast Croston)
  database/                ETL loaders that (re)populate the SAME Postgres DB
                          the chatbot reads from — separate from app/,
                          nothing here is imported by the running chatbot.
                          See backend/database/README.md before running
                          anything here: load_all.py DROPS and rebuilds
                          every table, deliberately not automated.
  scripts/
    setup_readonly_role.sql   one-time: creates the chatbot_ro DB role
    smoke_test.py              manual run of the 7 required acceptance questions
    test_fixtures/              the 100-question acceptance test bank (xlsx)
  tests/                  pytest unit tests (resilience, session store,
                          analytics/series.py, analytics/forecasting.py)
  requirements.txt
  .env.example

frontend/                Vite + React chat UI
  src/App.jsx, api.js, App.css

files/, files(1)/        original uploaded knowledge package (PDFs, JSON,
                          the planned postgres_schema.sql) — kept as-is for
                          provenance; the backend uses its own copies under
                          backend/app/knowledge/.
```

## Setup

### 1. Database

The `supplychain_automation` PostgreSQL database is assumed to already
exist and be populated. Create the chatbot's read-only role once:

```bash
psql -U <owner> -d supplychain_automation -f backend/scripts/setup_readonly_role.sql
```

(Change the role's password there — and in your `.env` — before using this
anywhere beyond local development.)

### 2. Backend

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then set OPENAI_API_KEY (and DB creds if different)
uvicorn app.main:app --reload --port 8000
```

Check `GET http://localhost:8000/api/health` — it reports DB connectivity,
whether an OpenAI key is configured, and which session store is active.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
```

`frontend/.env` sets `VITE_API_BASE` (defaults to `http://localhost:8000`).

## API

### `POST /api/chat`

```
{ "question": "How many items are on water?", "session_id": null }

-> {
     "answer": "...", "sql_used": "SELECT ...", "confidence": 0.95,
     "session_id": "...", "needs_clarification": false,
     "columns": ["items_on_water"], "rows": [{"items_on_water": 55}]
   }
```

`columns`/`rows` are the actual query result (capped at `CHATBOT_MAX_ROWS`,
the same cap the guard enforces via `LIMIT`) — both `null` on the
definition/clarification/error paths where no SQL ran.

`session_id`: pass back the value from a prior response to keep
conversation memory — needed for the time-period clarification follow-up
(e.g. "for what period?") and for "what about X instead"-style follow-ups.
Omit it for a one-off, stateless question.

If a question can't be answered from the available data, the assistant
says so plainly rather than inventing a number.

### `POST /api/chat/stream`

Same request body, same final payload — streamed as Server-Sent Events.
One `event: <phase>` line the first time each phase is reached
(`understanding` / `generating_sql` / `running_query` / `explaining`),
then a final `event: result` carrying the exact same JSON `/api/chat`
returns:

```
event: understanding
data: {"phase": "understanding"}

event: generating_sql
data: {"phase": "generating_sql"}

event: running_query
data: {"phase": "running_query"}

event: explaining
data: {"phase": "explaining"}

event: result
data: {"answer": "...", "sql_used": "...", "confidence": 0.95, ...}
```

A client that doesn't care about progress can ignore every event except
the last.

## Forecasting / analytics

Ask a forecast/projection question directly — no separate endpoint:

```
"Forecast issuance demand for item 2075-60 for the next 3 months"
"How much of resin A-85 will we need next quarter?"
```

`generate_sql` recognizes the intent and writes a plain historical SELECT
(one row per month, columns aliased `period`/`value`) instead of a final
answer, through the same item-resolution rules as any other question. That
SQL runs through the identical guard + read-only executor as every other
query — forecasting adds no new write path and does not relax the guard.
`app/analytics/series.py` turns the rows into a regular, gap-filled pandas
Series (missing months = 0 activity, not unknown); `app/analytics/
forecasting.py` backtests a small model bank on a held-out tail and keeps
whichever actually predicted best:

| Series shape | Candidates tried | Typically wins |
|---|---|---|
| Regular, ≥ ~1.5 seasonal cycles of history | mean, linear, seasonal-naive, Holt-Winters, SARIMA | Holt-Winters or SARIMA |
| Intermittent (≥35% zero periods) — e.g. slow-moving spare parts | mean, linear, seasonal-naive, Croston | Croston, though a simpler baseline can occasionally win a very short/noisy backtest — this is an honest property of backtesting on few held-out points, not a bug |
| Too short (< 4 periods) or all-zero | — | none — returns `ok: false` and a plain reason instead of a guessed number |

The LLM never does this arithmetic — it only decides *that* a forecast is
wanted and *which* item/history to pull; the maths is deterministic and
reproducible given the same series. `explain_forecast` (mirroring `explain`)
then reports the engine's own numbers, method, and confidence interval in
plain English — it cannot invent or adjust a figure.

No Prophet — pinned deps are `pandas==2.2.3`, `statsmodels==0.14.6`
(0.14.4 conflicts with this repo's already-installed scipy — see the
dependency commit), `statsforecast==1.7.8`.

The `database/` ETL loaders (ported from a teammate's separate repo — see
`backend/database/README.md`) are how you get a fuller history into
Postgres for these forecasts to actually run on; the live DB currently has
only a few months of issuance history, which is why most real forecasts
today land at "low" confidence with a wide interval — that's the engine
being honest about thin data, not a defect.

## Environment variables

See `backend/.env.example` for the full, authoritative list. Summary:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://localhost:5432/supplychain_automation` | Primary (owner-capable) Postgres connection |
| `CHATBOT_DATABASE_URL` | falls back to `DATABASE_URL` (warns) | The chatbot's own SELECT-only connection |
| `OPENAI_API_KEY` | — (required) | Text-to-SQL and answer generation |
| `OPENAI_MODEL` | `gpt-4o-mini` | |
| `CHATBOT_MAX_ROWS` | `200` | Row cap the guard enforces via `LIMIT` |
| `CHATBOT_STATEMENT_TIMEOUT_MS` | `8000` | Per-query DB statement timeout |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |
| `REDIS_URL` | unset (in-memory store) | Set to use Redis for session memory instead |
| `REDIS_SESSION_TTL_SECONDS` | `86400` | Redis session TTL, refreshed on every save |
| `LOG_LEVEL` | `INFO` | Structured request-log verbosity |

## Testing

```bash
cd backend
pytest                     # unit tests: OpenAI retry/backoff, session store,
                            # analytics/series.py, analytics/forecasting.py,
                            # the SQL guard incl. verified-function allow-list
python scripts/smoke_test.py   # the 7 required acceptance questions, against
                                # the live DB + a real OpenAI key
```

The 100-question acceptance bank (`scripts/test_fixtures/17_QADRI_AI_TEST_CASES_100.xlsx`)
has no expected-value column to grade against automatically (`Required
Tables`/`Expected Logic` describe the *original planned* schema, which
diverges from the live one in places — see `business_rules.py`). A full
runner over all 100, with a pragmatic pass/fail definition, is proposed but
intentionally not yet built pending sign-off on the grading approach.

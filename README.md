# Qadri Group — AI Supply Chain Assistant

A chatbot that answers natural-language questions about Qadri Group's supply
chain (stock, purchases, issuance, imports, logistics) by translating them
into SQL, running that SQL read-only against the live PostgreSQL database,
and explaining the result in 3–4 professional, decision-oriented lines.

**Stack:** React · FastAPI · LangGraph · OpenAI GPT-4o-mini · PostgreSQL

## How it works

```
User question
  -> FastAPI  POST /api/chat
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
       generate_answer              - GPT-4o-mini explains the result
  -> JSON { answer, sql_used, confidence, session_id }
```

Safety is layered: (1) the SQL guard rejects anything that isn't a single
read-only `SELECT`/CTE referencing real tables, and (2) even if that were
bypassed, the chatbot's DB connection uses a Postgres role
(`chatbot_ro`) that physically cannot INSERT/UPDATE/DELETE/DROP anything.

The **business rules baked into the system prompt** (`backend/app/knowledge/business_rules.py`)
are verified against the *live* database — not just the original planning
docs — because the real schema diverged from the plan in places (e.g. there
is no `reorder_level` or `Critical` flag anywhere in the database; the
closest real signal is `ab_items.rank`, an ABC classification). Where the
RAG layer's retrieved PDF content would conflict with these verified rules,
the verified rules always win.

## Project layout

```
backend/                FastAPI + LangGraph backend
  app/
    main.py              FastAPI app, POST /api/chat, GET /api/health
    config.py            env var loading (DATABASE_URL, OPENAI_API_KEY, ...)
    db/                  live schema introspection + read-only query executor
    graph/               LangGraph state/nodes/workflow + the SQL safety guard
    llm/                 OpenAI client wrapper (generate/repair/explain)
    knowledge/
      business_rules.py   verified, live-schema business rules (system prompt)
      dictionary.py        fast-path glossary answers (no SQL, no DB hit)
      rag.py                TF-IDF retrieval over the original knowledge PDFs
      rag_documents/        the source PDFs/MD files for the RAG corpus
      reference_data/       business_dictionary.json, synonym map, the
                             ORIGINAL planned schema (kept for reference only
                             — NOT authoritative; see business_rules.py)
  scripts/
    setup_readonly_role.sql   one-time: creates the chatbot_ro DB role
    test_fixtures/             the 100-question acceptance test bank
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

Check `GET http://localhost:8000/api/health` — it reports DB connectivity
and whether an OpenAI key is configured.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
```

`frontend/.env` sets `VITE_API_BASE` (defaults to `http://localhost:8000`).

## API

```
POST /api/chat
{ "question": "How many items are on water?", "session_id": null }

-> { "answer": "...", "sql_used": "SELECT ...", "confidence": 0.95,
     "session_id": "...", "needs_clarification": false }
```

`session_id`: pass back the value from a prior response to keep
conversation memory — needed for the time-period clarification follow-up
(e.g. "for what period?") and for "what about X instead"-style follow-ups.
Omit it for a one-off, stateless question.

If a question can't be answered from the available data, the assistant
says so plainly rather than inventing a number.

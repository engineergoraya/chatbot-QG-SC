# Qadri Group – AI Supply Chain Assistant Knowledge Base

Enterprise RAG knowledge system + business dictionary, built from your actual
datasets (not generic templates). Every figure was computed directly from the
uploaded files.

## What's in this package

**RAG documents (PDF, ready to chunk & embed)**
1. `01_Qadri_Supply_Chain_Overview.pdf` – architecture, data flow, group scale, how the assistant reasons
2. `02_Item_Master_Knowledge.pdf` – item code spine, classification, relationships
3. `03_Stores_Inventory_Knowledge.pdf` – stock, hold, available, valuation, reorder/critical
4. `04_Purchase_Procurement_Knowledge.pdf` – POs, suppliers, lead time, delay logic, KPIs
5. `05_Imports_Knowledge.pdf` – LC/ALC, shipment status, on-water, ETA chain, countries
6. `06_Logistics_Knowledge.pdf` – export lifecycle, containers, docs, sailing, freight
7. `07_Issuance_Consumption_Knowledge.pdf` – consumption by dept/machine/job, TotalPric rule
8. `08_Business_Terminology_Dictionary.pdf` – full term → field → interpretation table
9. `09_AI_Test_Questions.pdf` – 12 benchmark questions across all 4 reasoning types
10. `10_Assumptions_and_Data_Gaps.pdf` – what was inferred, gaps to close, build recommendations

**Engineering artifacts (drive the FastAPI / LangGraph / PostgreSQL stack)**
- `business_dictionary.json` – machine-readable term→field→logic map with deterministic value rules
- `postgres_schema.sql` – normalized schema for all 7 domains + ready-made analytical views

## Key data facts captured (as of 2026-07-24)
- 26,695 master items · 6,070 stock rows across 4 branches
- Available inventory value: **PKR 860.4M**
- Issued/consumed value in window: **PKR 3,522.8M** (2025-07-28 → 2026-07-13)
- Purchase value in window: **PKR 330.1M** · 194 suppliers
- 197 ongoing import shipments (China-dominant) · 43 critical + 32 reorder items
- 1,139 requisition lines with pending demand

## Non-negotiable interpretation rules (enforced in JSON + SQL views)
- Inventory value = `SUM(available_amount)` — never qty × price
- Consumption value = `SUM(total_pric)` — never quantity × unit_price
- Supplier delay = `purchase_date − required_date` (>0 = late)
- "On water" = import `current_status = 'In Transit'`
- "Sailing" is context-dependent: imports = inbound at sea; logistics = export departed Karachi

## Suggested build order
1. Apply `postgres_schema.sql`; load each dataset into its table.
2. Load `business_dictionary.json` into the RAG store as structured grounding.
3. Chunk PDFs by H1/H2 with the doc code (01–10) as metadata.
4. Expose the SQL views as LangGraph tools so common questions skip free-form SQL.
5. Validate against the 12 questions in Document 09.
6. Close the gaps in Document 10, then re-ingest on each data refresh.

---

## AI Execution Layer (files 11–18)

Added on top of the knowledge package to make the assistant fast, accurate and production-grade. Core principle: **route first, retrieve only what's needed — never load all PDFs per query.**

- `11_SQL_Reasoning_Rules.pdf` — business question → exact tables/fields/joins/filters/aggregation, per department. Encodes the two value rules.
- `12_Department_Routing_Rules.pdf` — how the router picks one of six agents; the "sailing" imports-vs-logistics disambiguation; cross-functional order.
- `13_SYNONYM_MAPPING.json` — **735** employee-phrase → database-term mappings (min was 500), grounded in real Qadri vocabulary; each with meaning, db term, department, confidence.
- `14_Supply_Chain_KPI_Library.pdf` — canonical formulas (inventory, procurement, import, logistics, consumption) with tables, meaning, example.
- `15_Entity_Resolution_Rules.pdf` — item / supplier / branch resolution priority and ambiguity handling.
- `16_LANGGRAPH_TOOL_DEFINITIONS.md` — six-agent architecture; every tool's name, purpose, input/output schema, example.
- `17_QADRI_AI_TEST_CASES_100.xlsx` — **100** test questions (Stores 20 · Purchase 20 · Imports 15 · Logistics 15 · Issuance 15 · Cross-functional 15) with category, department, difficulty, required tables, expected logic, expected output. Includes a Summary sheet.
- `18_Architecture_and_Optimization.md` — four-tier RAG design (always-loaded / conditional RAG / DB tables / tools), LangGraph flow, retrieval strategy, latency optimization, risk register, go-live checklist.

### Four-tier information placement (the optimization)
1. **Always loaded** (tiny System Card): value rules, branch map, answer style, agent roster.
2. **Retrieved conditionally** (RAG, 1–3 chunks): domain docs 01–08 and the *single* matching rule from 11/14/15.
3. **Database tables** (`postgres_schema.sql`): all transactional truth.
4. **LangGraph tools** (Doc 16): deterministic, parameter-only operations over views.

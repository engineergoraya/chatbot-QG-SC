# 18 — Architecture, Retrieval Strategy & Optimization

**Qadri Group AI Supply Chain Assistant — production design notes**
React · FastAPI · LangGraph · GPT-4o-mini · PostgreSQL

This ties the execution layer (files 11–17) to a concrete, low-latency, low-token
architecture. The governing principle: **never load all documents into context.** Route
first, retrieve only what the resolved question needs, and let typed tools do the math.

---

## 1. Recommended RAG architecture

Four tiers, by how information is used — this is the core optimization.

### Tier 1 — Always loaded (tiny, static "System Card", ~400–600 tokens)
Baked into the master/agent system prompts, never retrieved:
- The two value rules (inventory = `SUM(available_amount)`; consumption = `SUM(total_pric)`).
- Branch code ↔ name map (small, high-value, used constantly).
- Answer style contract (professional, 3–4 lines, always state the data window on totals).
- The six-agent roster and the one-line "what each owns".

### Tier 2 — Retrieved conditionally (RAG, per question)
Chunked and embedded; pulled only when the intent class needs it:
- **Docs 01–08** (domain knowledge) → chunk by H1/H2, metadata `{doc, department, section}`.
- **Doc 11 (SQL rules)** → retrieve only the *one* rule matching the resolved question.
- **Doc 14 (KPI library)** → retrieve only the *one* KPI a derived-metric question needs.
- **Doc 15 (entity resolution)** → retrieve only on an ambiguous entity.
Typical retrieval: **1–3 chunks**, not ten PDFs.

### Tier 3 — Database tables (PostgreSQL, `postgres_schema.sql`)
All transactional truth. The LLM never sees raw rows in bulk — it sees tool outputs
(already filtered/aggregated). This is where "what is the number" is answered.

### Tier 4 — LangGraph tools (Doc 16)
Deterministic operations over Tier 3, wrapping pre-built views. The LLM supplies
parameters only. This is what keeps numbers correct and prompts short.

**What goes where — quick reference**

| Information | Tier | Why |
|---|---|---|
| Value rules, branch map, style | 1 Always | Small, universal, correctness-critical |
| Domain explanations (01–08) | 2 RAG | Large, only some relevant per question |
| SQL templates (11), KPIs (14) | 2 RAG | Retrieve the single matching rule |
| Synonyms (13) | code (in router) | Fast dict lookup, no embedding call |
| Stock/purchase/issuance/etc. | 3 DB | Transactional truth, queried not embedded |
| inventory_value(), supplier_delay()… | 4 Tools | Deterministic, param-only |

---

## 2. Recommended LangGraph flow

```
question
  → Router Node        (synonym-expand in code → classify intent → resolve dept + entities)
  → [if Definition]    answer from Business Dictionary, STOP        (no DB, no agent)
  → [if ambiguous]     ask ONE clarifying question, STOP
  → Entity Resolver    (item/supplier/branch → key)                 (Doc 15)
  → Department Agent    conditionally retrieve its 1 SQL/KPI chunk   (Docs 11/14)
  → Tool call(s)        typed, param-only, over views                (Doc 16 / schema)
  → [if cross-functional] run secondary agents in fixed order       (Doc 12 §4)
  → Composer Node       3–4 line answer, cite numbers + window
```

Single-agent path is the default. Cross-functional fan-out is the exception and uses a
fixed agent order so it stays predictable and cacheable.

---

## 3. Retrieval strategy (concrete rules)

1. **Classify before retrieving.** Definition questions skip retrieval and the DB entirely.
2. **Route before retrieving.** The department narrows the embedding search space (filter by
   `department` metadata), so a query pulls from ~1 domain, not all.
3. **Retrieve the rule, not the domain.** For a metric question, fetch the one SQL template
   (Doc 11) or KPI (Doc 14) by ID — not the whole PDF.
4. **Cap chunks.** Hard limit of 3 retrieved chunks per turn; if more seem needed, the
   question is probably cross-functional — handle via agent fan-out, not bigger context.
5. **Prefer views over generated SQL.** If a `postgres_schema.sql` view answers it
   (`v_inventory_value_by_branch`, `v_supplier_delay`, `v_critical_priority`, `v_on_water`,
   `v_consumption_by_department`), call it — zero SQL generation, faster and safer.
6. **Cache aggressively.** Cache (a) synonym → normalized intent, (b) entity resolutions,
   (c) tool results for common aggregates on a short TTL keyed to the data-refresh cycle.

---

## 4. Latency optimization recommendations

- **Do routing in code, not with an LLM.** Synonym expansion + keyword/entity scoring is a
  dictionary + arithmetic — saves a full model round-trip on every question.
- **Keep prompts small.** Tier-1 card + ≤3 chunks + one agent's tool schemas. Small prompts
  are the biggest lever on both latency and cost with GPT-4o-mini.
- **Parallelize cross-functional tool calls** where agents are independent (e.g. inventory
  and import checks) and merge in the Composer.
- **Pre-compute heavy KPIs.** Materialize supplier-delay, stock-health, and on-water rollups
  on each data refresh so tools read a view, not scan raw tables.
- **Index the join keys** (`item_code`, `supplier`, `branch`, `current_status`, `department`)
  — already in `postgres_schema.sql`.
- **Stream the answer** token-by-token to React so perceived latency is low even when a
  cross-functional query runs several tools.
- **Short-circuit definitions** (they need neither DB nor RAG) — a large share of employee
  questions are "what is X".

---

## 5. Remaining risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Entity mis-resolution** (item/supplier spelling) | Wrong answer that looks right | Mandatory resolver (Doc 15); ask-one-question on ambiguity; add supplier master + canonical IDs |
| **Branch code ambiguity** (QB2/QBL, QE-II, IOL) | Wrong branch totals | Confirm mapping with ERP before go-live (Doc 10); state assumption when used |
| **Stale data** (no snapshot timestamp on stock) | "Current" is as-of-last-export | Add export timestamp; show "as of" in answers; schedule refresh |
| **Windowed totals mistaken for all-time** | Misleading figures | Composer always prints the data window on any total |
| **"Sailing" ambiguity** | Wrong department | Router clarifies when context is missing (Doc 12 §3) |
| **LLM inventing SQL / math** | Incorrect numbers | Param-only tools + views; value rules in Tier-1 card, not left to the model |
| **Cross-functional prompt bloat** | Latency/cost spike | Agent fan-out with fixed order + parallel tools, not one mega-prompt |
| **Requisition→PO→Import not linked** | Can't trace demand to fulfilment | Carry requisition ref through PO and import file (Doc 10 gap) |
| **China concentration in imports** | Real supply risk (business, not system) | Surface as a KPI (country dependency) so decision-makers see it |

---

## 6. Go-live checklist

1. Apply `postgres_schema.sql`; load each dataset; build the indexes and views.
2. Load `13_SYNONYM_MAPPING.json` into the router; wire the code-side normalizer.
3. Embed Docs 01–08 + the *rules* in 11/14/15, chunked by H1/H2 with `{doc, department}` metadata.
4. Implement the six agents and their tools per Doc 16; back value tools with the views.
5. Bake the Tier-1 System Card into the prompts.
6. Run `17_QADRI_AI_TEST_CASES_100.xlsx` as the acceptance suite; require correct
   table/logic/output on every case.
7. Confirm the branch-code and supplier-master gaps from Doc 10, then enable production.

# 16 — LangGraph Tool Definitions & Agent Architecture

**Qadri Group AI Supply Chain Assistant**
Stack: React · FastAPI · LangGraph · OpenAI GPT-4o-mini · PostgreSQL

This document defines the multi-agent LangGraph design. It is deliberately **not** one
giant chatbot. A lightweight master agent routes each question to one specialist
department agent (occasionally a small ordered set), which calls narrow, typed tools that
run pre-shaped SQL. This keeps prompts small, latency low, and answers reproducible.

---

## 1. Graph topology

```
                         ┌─────────────────────────┐
   user (React) ───────▶ │  Master Agent (entry)    │
                         │  - loads System Card only │
                         └───────────┬──────────────┘
                                     │
                         ┌───────────▼──────────────┐
                         │  Router Node              │
                         │  - synonym expand (13)    │
                         │  - intent + entities      │
                         │  - pick agent(s) (12)     │
                         └───────────┬──────────────┘
                 ┌───────────┬───────┼───────┬───────────┬───────────┐
                 ▼           ▼       ▼       ▼           ▼           ▼
             Inventory   Purchase  Import Logistics  Issuance     Item
               Agent      Agent    Agent   Agent      Agent       Agent
                 │           │       │       │           │           │
                 ▼           ▼       ▼       ▼           ▼           ▼
             [tools]     [tools]  [tools] [tools]    [tools]   [entity resolver]
                 └───────────┴───────┴───┬───┴───────────┴───────────┘
                                         ▼
                            ┌────────────────────────┐
                            │ Composer Node           │
                            │ - 3–4 line answer       │
                            │ - cites numbers + window│
                            └────────────────────────┘
```

**Shared pre-node:** the **Entity Resolver** (Doc 15) runs before any agent SQL. The
Item Agent exposes it as a tool; other agents call it internally.

---

## 2. Node responsibilities

| Node | Job | Reads |
|---|---|---|
| Master Agent | Own the conversation, hold the System Card, call Router, hand to Composer | System Card (always-loaded) |
| Router | Normalize → classify intent → resolve department(s) | Synonym map (13), Routing rules (12) |
| Department Agents | Turn the resolved question into tool calls | SQL rules (11), KPI library (14) — *retrieved conditionally* |
| Entity Resolver | item/supplier/branch → key | item_master, branch map, supplier normaliser |
| Composer | Produce the final concise answer | tool outputs only |

---

## 3. Tool definitions

All tools are typed (Pydantic in FastAPI), parameterized, and return JSON. Money is PKR.
Each tool wraps a pre-shaped query or a view from `postgres_schema.sql` — the LLM supplies
**parameters only**, never raw SQL.

### 3.1 Inventory Agent

#### `inventory_lookup`
- **Purpose:** current stock position for a resolved item (optionally by branch).
- **Input:**
  ```json
  {"item_code": "19981-60", "branch": "QCL | null"}
  ```
- **Output:**
  ```json
  {"item_code":"19981-60","item":"MS Scrap",
   "rows":[{"branch":"Qadcast (Pvt) Ltd.","stock_qty":1200,"hold_qty":0,
            "available_qty":1200,"available_amount":195000.0}]}
  ```
- **Example:** “How much MS Scrap do we have at Qadcast?” → filter branch=QCL.

#### `inventory_value`
- **Purpose:** usable inventory value (PKR); optional group-by branch/category.
- **Input:** `{"group_by": "branch | category | null", "branch": null, "category": null}`
- **Output:** `{"total_value_pkr": 860385662.91, "breakdown":[{"key":"Qadcast (Pvt) Ltd.","value_pkr": ...}]}`
- **SQL concept:** `SUM(available_amount)` — **never** qty×price. Backed by `v_inventory_value_by_branch`.
- **Example:** “What is our inventory value by branch?”

#### `stock_health`
- **Purpose:** criticality / coverage for an item or the critical list.
- **Input:** `{"item_code": null, "mode": "item | critical_list | reorder_list"}`
- **Output:**
  ```json
  {"mode":"critical_list","count":43,
   "items":[{"item_code":"...","stock_health":0.19,"lead_time_days":45,
             "pending_demands":12,"stock_in_transit":0}]}
  ```
- **Example:** “Which items are critical?” → `mode=critical_list`. Backed by `v_critical_priority`.

### 3.2 Purchase Agent

#### `supplier_delay`
- **Purpose:** delay / reliability per supplier.
- **Input:** `{"supplier": "canonical name | null", "order": "worst | best", "limit": 10}`
- **Output:**
  ```json
  {"suppliers":[{"supplier":"Malik Iqbal and Co.","po_lines":42,
                 "avg_delay_days":6.1,"on_time_pct":58.3,"spend_pkr": ...}]}
  ```
- **SQL concept:** `AVG(purchase_date - required_date)`. Backed by `v_supplier_delay`.
- **Example:** “Which suppliers are most delayed?”

#### `purchase_analysis`
- **Purpose:** purchase value / spend breakdown.
- **Input:** `{"group_by":"supplier|category|branch|month|null","supplier":null,"date_from":null,"date_to":null}`
- **Output:** `{"total_pkr":330060843.95,"window":["2025-07-03","2026-07-09"],"breakdown":[...]}`
- **Example:** “What did we spend by category last quarter?” (always echo the window).

### 3.3 Import Agent

#### `shipment_status`
- **Purpose:** count/list inbound shipments by status, country, or item.
- **Input:** `{"status":"In Transit | Ready Awaiting Sailing | ... | null","country":null,"item_code":null}`
- **Output:**
  ```json
  {"filter":{"status":"In Transit"},"count":50,"value_pkr": ...,
   "shipments":[{"file_no":"...","supplier":"...","country":"China","eta":"2026-08-31"}]}
  ```
- **Example:** “How many items are on water?” → `status="In Transit"`. Backed by `v_on_water`.

#### `eta_analysis`
- **Purpose:** ETA slippage / upcoming arrivals.
- **Input:** `{"mode":"slippage | upcoming","horizon_days":30}`
- **Output:** `{"mode":"slippage","shipments":[{"file_no":"...","first_eta":"...","latest_eta":"...","slippage_days":22}]}`
- **SQL concept:** `eta - eta_1`.
- **Example:** “Which imports are slipping?”

### 3.4 Logistics Agent

#### `shipment_tracking`
- **Purpose:** status/delay of outbound export shipments.
- **Input:** `{"order_id":null,"customer":null,"stage":null,"delayed_only":false}`
- **Output:**
  ```json
  {"count":12,"shipments":[{"order_id":"...","customer":"...","shipment_stage":"...",
     "etd_karachi":"...","actual_arrival":"...","delay_days":4,"bl_status":"..."}]}
  ```
- **Example:** “Are any export shipments late?” → `delayed_only=true`.

#### `freight_analysis`
- **Purpose:** freight cost efficiency and transit performance.
- **Input:** `{"group_by":"shipping_line | country | null"}`
- **Output:** `{"freight_per_kg":123.4,"avg_transit_days":18.2,"breakdown":[...]}`
- **SQL concept:** `SUM(total_shipping_cost)/SUM(nw_kgs)`.
- **Example:** “What is our freight cost per kg by shipping line?”

### 3.5 Issuance Agent

#### `consumption_analysis`
- **Purpose:** consumption value by department / machine / job / category / item / month.
- **Input:**
  ```json
  {"group_by":"department|machine|job|category|item|month",
   "department":null,"item_code":null,"date_from":null,"date_to":null}
  ```
- **Output:** `{"total_pkr":3522817955.37,"window":["2025-07-28","2026-07-13"],"breakdown":[{"key":"Production","value_pkr": ...,"lines":10333}]}`
- **SQL concept:** `SUM(total_pric)` — **never** qty×unit_price. Backed by `v_consumption_by_department`.
- **Example:** “How much did Production consume this year?”

### 3.6 Item Agent

#### `item_lookup`
- **Purpose:** identity/classification + entity resolution for a fuzzy reference.
- **Input:** `{"query":"bearing","item_code":null,"specification":null,"category":null}`
- **Output:**
  ```json
  {"status":"ambiguous","match_count":1134,
   "sample":[{"item_code":"4347-60","item":"Ball Bearing","specification":"...",
              "item_sub_group":"Bearings"}],
   "clarifying_question":"There are many bearings — which code, spec, or branch?"}
  ```
- **Example:** “Show bearing stock” → returns `ambiguous`, upstream asks to narrow (Doc 15).

---

## 4. State object (passed along the graph)

```json
{
  "question": "how many items are on water?",
  "normalized": "how many import shipments in transit",
  "intent_class": "aggregate",
  "primary_agent": "import",
  "secondary_agents": [],
  "entities": {"item_code": null, "supplier": null, "branch": null},
  "tool_calls": [{"tool":"shipment_status","args":{"status":"In Transit"}}],
  "tool_results": [ ... ],
  "needs_clarification": false,
  "answer": null
}
```

---

## 5. Why this shape

- **Small prompts:** an agent only ever sees its own tool schemas + the one retrieved rule
  chunk it needs — not all ten PDFs.
- **Deterministic numbers:** value rules live in tools/views, so GPT-4o-mini cannot
  “creatively” compute inventory value or consumption.
- **Cheap routing:** synonym expansion + keyword scoring is done in code, not by an extra
  LLM call, wherever possible.
- **Scalable:** a new domain = one new agent + its tools, no change to the others.

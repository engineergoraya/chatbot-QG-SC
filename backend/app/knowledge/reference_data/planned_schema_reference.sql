-- =====================================================================
-- Qadri Group AI Supply Chain Assistant  --  PostgreSQL schema
-- Generated from the actual structure of the uploaded datasets.
-- Item Code is the join spine across domains.
-- Money is PKR. Keep raw authoritative value fields (total_pric, amount,
-- available_amount) and DO NOT recompute qty*price downstream.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS scm;
SET search_path TO scm;

-- ---- Reference: branches ------------------------------------------------
CREATE TABLE branch (
    branch_id     SERIAL PRIMARY KEY,
    full_name     TEXT UNIQUE NOT NULL,        -- e.g. 'Qadcast (Pvt) Ltd.'
    codes         TEXT[]                        -- e.g. {QCL}
);

-- ---- Item Master (26,695 rows) -----------------------------------------
CREATE TABLE item_master (
    item_code        TEXT PRIMARY KEY,          -- '19981-60'
    item             TEXT NOT NULL,
    specification    TEXT,
    group_name       TEXT,
    material_standard TEXT,
    item_sub_group   TEXT,                       -- category
    unit             TEXT
);
CREATE INDEX idx_item_subgroup ON item_master(item_sub_group);
CREATE INDEX idx_item_name ON item_master USING gin (to_tsvector('simple', item));

-- ---- Stock (per branch, ~6,070 rows) -----------------------------------
CREATE TABLE stock (
    stock_id         BIGSERIAL PRIMARY KEY,
    item_code        TEXT REFERENCES item_master(item_code),
    item             TEXT,
    category         TEXT,
    branch           TEXT,
    reorder_level    NUMERIC,
    hold_qty         NUMERIC,
    stock_qty        NUMERIC,                    -- physical total
    stock_qty_amount NUMERIC,                    -- value of physical total
    available_qty    NUMERIC,                    -- usable = stock_qty - hold_qty
    available_amount NUMERIC,                    -- AUTHORITATIVE inventory value (PKR)
    weight_kgs       NUMERIC
);
CREATE INDEX idx_stock_item ON stock(item_code);
CREATE INDEX idx_stock_branch ON stock(branch);

-- ---- Issuance / Consumption (~50,711 rows) -----------------------------
CREATE TABLE issuance (
    issuance_row_id  BIGSERIAL PRIMARY KEY,
    issuance_code    TEXT,
    branch           TEXT,
    department       TEXT,
    issue_to_others  TEXT,
    authorized_by    TEXT,
    issued_by        TEXT,
    received_by      TEXT,
    description      TEXT,
    ref_no           TEXT,
    demand_ref_no    TEXT,
    item             TEXT,
    specification    TEXT,
    group_name       TEXT,
    material_standard TEXT,
    uom              TEXT,
    item_code        TEXT REFERENCES item_master(item_code),
    material         TEXT,
    quantity         NUMERIC,
    weight           NUMERIC,
    status           TEXT,
    from_date        DATE,
    unit_price       NUMERIC,
    total_pric       NUMERIC,                    -- AUTHORITATIVE issued value (PKR)
    serial_no        TEXT,
    machine          TEXT,
    category         TEXT,
    job_number       TEXT
);
CREATE INDEX idx_iss_item ON issuance(item_code);
CREATE INDEX idx_iss_dept ON issuance(department);
CREATE INDEX idx_iss_date ON issuance(from_date);
CREATE INDEX idx_iss_job ON issuance(job_number);

-- ---- Purchase (~2,778 rows, 194 suppliers) -----------------------------
CREATE TABLE purchase (
    purchase_row_id  BIGSERIAL PRIMARY KEY,
    record_no        INTEGER,
    ref_no           TEXT,
    branch           TEXT,                       -- QE/QEN/QCL/QB2...
    item_code        TEXT REFERENCES item_master(item_code),
    item_name        TEXT,
    specification    TEXT,
    uom              TEXT,
    material         TEXT,
    standard         TEXT,
    qty              NUMERIC,
    weight           NUMERIC,
    price            NUMERIC,
    bill_no          TEXT,
    amount           NUMERIC,                    -- AUTHORITATIVE purchase value (PKR)
    supplier         TEXT,
    ppc_store        TEXT,
    required_date    DATE,
    lead_time        NUMERIC,
    lead_time2       NUMERIC,
    purchase_date    DATE,
    lead_time3       NUMERIC,
    mop              TEXT,                        -- On Credit / By Cash / On Advance
    dc_no            TEXT,
    pass_qty         TEXT,
    weight4          NUMERIC,
    po_number        TEXT,
    item_category    TEXT,
    sourcing_officer TEXT,
    po_date          DATE,
    -- derived (populate on load): supplier delay in days
    delay_days       INTEGER GENERATED ALWAYS AS ((purchase_date - required_date)) STORED
);
CREATE INDEX idx_pur_item ON purchase(item_code);
CREATE INDEX idx_pur_supplier ON purchase(supplier);
CREATE INDEX idx_pur_podate ON purchase(po_date);

-- ---- Store Requisition (~7,075 rows) -----------------------------------
CREATE TABLE requisition (
    requisition_row_id BIGSERIAL PRIMARY KEY,
    document_number  TEXT,
    branch           TEXT,
    department       TEXT,
    prepare_date     DATE,
    ref_hash         TEXT,
    item_name        TEXT,
    material         TEXT,
    urgent           TEXT,
    weight           NUMERIC,
    serial_no        TEXT,
    description      TEXT,
    dimension        TEXT,
    required_by      TEXT,
    req_quantity     NUMERIC,
    pur_quantity     NUMERIC,
    pending_quantity NUMERIC,                    -- >0 = open demand
    m_u              TEXT,
    last_purchase    DATE,
    previous_price   TEXT,
    required_date    DATE,
    status           TEXT,                        -- Issued/InStock/Procuring...
    sourced_by       TEXT,
    item_category    TEXT,                        -- 'Procurement' vs 'Imports'
    previous_supplier TEXT,
    original_required DATE,
    revised_counter  TEXT,
    item_code        TEXT REFERENCES item_master(item_code),
    stock_in_date    DATE
);
CREATE INDEX idx_req_item ON requisition(item_code);
CREATE INDEX idx_req_status ON requisition(status);
CREATE INDEX idx_req_pending ON requisition(pending_quantity);

-- ---- AB Items intelligence (Main 304 / Re-Order 32 / Critical 43) ------
CREATE TABLE ab_items (
    ab_row_id        BIGSERIAL PRIMARY KEY,
    branch_name      TEXT,
    item_code        TEXT REFERENCES item_master(item_code),
    item             TEXT,
    specification    TEXT,
    item_sub_group   TEXT,
    rank             TEXT,
    import_flag      TEXT,                        -- Y/N: replenished via import
    reorder_level    NUMERIC,
    safety_stock     NUMERIC,
    safety_days      NUMERIC,
    lead_time_days   NUMERIC,
    maximum_level    NUMERIC,
    stock_health     NUMERIC,                     -- stock-days coverage ratio
    stock_quantity   NUMERIC,
    issuance_12m     NUMERIC,
    issuance_3m      NUMERIC,
    stock_days_12m   NUMERIC,
    stock_days_3m    NUMERIC,
    upcoming_month_forecast NUMERIC,
    pending_demands  NUMERIC,
    stock_in_transit NUMERIC,
    stock_days_vs_forecast NUMERIC,
    last_stock_in_date DATE,
    stock_finish_date  DATE,
    incoming_stock_arrival_date DATE,
    variance_arrival_finish NUMERIC,
    list_type        TEXT                          -- 'main' | 'reorder' | 'critical'
);
CREATE INDEX idx_ab_item ON ab_items(item_code);
CREATE INDEX idx_ab_list ON ab_items(list_type);

-- ---- Imports (On Going ~197 shipments) ---------------------------------
CREATE TABLE import_shipment (
    import_row_id    BIGSERIAL PRIMARY KEY,
    works            TEXT,
    file_no          TEXT,
    category         TEXT,
    po_no            TEXT,
    item_code        TEXT,                         -- links to item_master where available
    item_category    TEXT,
    item_name        TEXT,
    specs_standard   TEXT,
    job_no           TEXT,
    customer         TEXT,
    qty              NUMERIC,
    uom              TEXT,
    total_wt_ton     NUMERIC,
    demand_date      DATE,
    required_date    DATE,
    lead_time        NUMERIC,
    supplier         TEXT,
    country          TEXT,                         -- China dominant
    city             TEXT,
    bank             TEXT,
    payment_mode     TEXT,
    currency         TEXT,
    exchange_rate    NUMERIC,
    total_value_fc   NUMERIC,
    total_value_pkr  NUMERIC,
    mode_of_shipment TEXT,
    bl_no            TEXT,
    pol              TEXT,
    pod              TEXT,
    etd              DATE,
    eta_1            DATE,
    eta_2            DATE,
    eta_3            DATE,
    eta_4            DATE,
    eta              DATE,                          -- latest/effective ETA
    eta_works        DATE,
    transit_time     NUMERIC,
    gd_no            TEXT,
    lc_status        TEXT,
    alc_status       TEXT,
    alc_date         DATE,
    current_status   TEXT,                          -- In Transit / Under Production / Ready Awaiting Sailing ...
    remarks          TEXT
);
CREATE INDEX idx_imp_status ON import_shipment(current_status);
CREATE INDEX idx_imp_country ON import_shipment(country);
CREATE INDEX idx_imp_item ON import_shipment(item_code);

-- ---- Logistics (Shipment Master ~165) ----------------------------------
CREATE TABLE logistics_shipment (
    logistics_row_id BIGSERIAL PRIMARY KEY,
    order_id         TEXT,
    exp_no           TEXT,
    batch_no         TEXT,
    primary_key      TEXT,
    customer         TEXT,
    job_no           TEXT,
    description      TEXT,
    qty              NUMERIC,
    pkgs             NUMERIC,
    nw_kgs           NUMERIC,
    gw_kgs           NUMERIC,
    country          TEXT,
    pod              TEXT,
    incoterm         TEXT,
    shipping_term    TEXT,
    containers_desc  TEXT,                          -- 20'/40', OT, FR, OOG, LCL, AIR
    shipping_agent   TEXT,
    clearing_agent   TEXT,
    shipping_line    TEXT,
    cro_no           TEXT,
    etd_karachi      DATE,
    sailing_date     DATE,                          -- export vessel departure
    actual_arrival   DATE,
    transit_time     NUMERIC,
    vessel_voyage    TEXT,
    shipment_stage   TEXT,
    shipment_status  TEXT,
    delay_status     TEXT,
    total_shipping_cost NUMERIC,
    payment_term     TEXT,
    doc_status_customs  TEXT,
    doc_status_customer TEXT,
    doc_status_bank     TEXT,
    bl_status        TEXT,
    docs_overall     TEXT,
    transporter      TEXT,
    vehicle_no       TEXT,
    truck_status     TEXT,
    -- derived: logistics delay
    delay_days       INTEGER GENERATED ALWAYS AS ((actual_arrival - eta_karachi_placeholder())) STORED
);
-- NOTE: replace the generated expr above with (actual_arrival - planned_arrival)
-- once a planned-arrival column is loaded; kept explicit to document the rule.

-- =====================================================================
-- Convenience views the LangGraph tools can call directly
-- =====================================================================

-- Current usable inventory value by branch
CREATE VIEW v_inventory_value_by_branch AS
SELECT branch, SUM(available_amount) AS available_value_pkr,
       SUM(available_qty) AS available_qty
FROM stock GROUP BY branch;

-- Items below reorder level (basic reorder signal)
CREATE VIEW v_below_reorder AS
SELECT s.item_code, s.item, s.branch, s.category,
       s.available_qty, s.reorder_level
FROM stock s
WHERE s.reorder_level > 0 AND s.available_qty < s.reorder_level;

-- Critical items with demand & lead time (procurement priority)
CREATE VIEW v_critical_priority AS
SELECT a.item_code, a.item, a.branch_name, a.stock_health,
       a.lead_time_days, a.pending_demands, a.stock_in_transit,
       a.stock_finish_date
FROM ab_items a
WHERE a.list_type = 'critical'
ORDER BY a.stock_health ASC, a.lead_time_days DESC;

-- Supplier delay summary
CREATE VIEW v_supplier_delay AS
SELECT supplier,
       COUNT(*) AS po_lines,
       ROUND(AVG(delay_days)::numeric, 1) AS avg_delay_days,
       SUM(amount) AS total_value_pkr
FROM purchase
WHERE supplier IS NOT NULL
GROUP BY supplier
ORDER BY avg_delay_days DESC;

-- Consumption by department (authoritative value)
CREATE VIEW v_consumption_by_department AS
SELECT department, SUM(total_pric) AS consumed_value_pkr, COUNT(*) AS lines
FROM issuance GROUP BY department ORDER BY consumed_value_pkr DESC;

-- Imports on water
CREATE VIEW v_on_water AS
SELECT COUNT(*) AS shipments, SUM(total_value_pkr) AS value_pkr
FROM import_shipment WHERE current_status = 'In Transit';

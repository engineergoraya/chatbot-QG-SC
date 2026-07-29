# database/schemas/imports_schemas.py
#
# This file defines the IMPORTS module tables AND the shared master tables
# (items, suppliers, purchase_order), per the team decision to keep the
# masters inside the imports schema.
#
# IMPORTANT: this schema file must be run BEFORE Stores and Purchases,
# because those modules reference the masters (items, suppliers, purchase_order)
# created here.
#
# Style follows logistics_schemas.py:
#   - CREATE TABLE IF NOT EXISTS
#   - BIGSERIAL surrogate primary keys
#   - the original business ID kept as a separate TEXT column
#   - NUMERIC(18,2) for money, NUMERIC(14,3) for weights/qty, DATE for dates
#   - FKs via BIGINT REFERENCES ... (ON DELETE CASCADE for child import rows)


# ============================================================
#  MASTER TABLES  (shared across all modules)
# ============================================================

# ---------------------- ITEMS --------------------------------------------
# item_code is the natural business key and is referenced directly by
# every transaction table (simpler for the loader than a generated id).
items_table_query = '''CREATE TABLE IF NOT EXISTS items(
    item_code           TEXT PRIMARY KEY,
    item                TEXT,
    group_name          TEXT,
    material_standard   TEXT,
    uom                 TEXT,
    item_category       TEXT,
    specs               TEXT
);'''

# ---------------------- PURCHASE ORDER -----------------------------------
# Referenced by import_details and purchases. po_number is the natural key.
purchase_order_table_query = '''CREATE TABLE IF NOT EXISTS purchase_order(
    po_number       TEXT PRIMARY KEY,
    po_date         DATE
);'''

# ============================================================
#  IMPORTS MODULE
# ============================================================

# ---------------------- IMPORT DETAILS (one row per import) --------------
import_details_query = '''CREATE TABLE IF NOT EXISTS import_details(
    import_id                       BIGSERIAL PRIMARY KEY,
    import_ref                      TEXT,           -- original "Import id" from source
    branch                          TEXT,
    tab_status                      TEXT,
    file_no                         TEXT,           -- File No (category label)
    category                        TEXT,
    job_no                          TEXT,
    mo_no                           TEXT,
    customer                        TEXT,
    total_wt_ton                    NUMERIC(14,3),
    demand_date                     DATE,
    protocol_approval_date          DATE,
    req_date                        DATE,
    lead_time                       TEXT,
    supplier                        TEXT,
    supplier_country                TEXT,
    supplier_city                   TEXT,
    gin_status                      TEXT,
    gin_date                        DATE,
    total_value_fc                  NUMERIC(18,2),
    total_value_pkr                 NUMERIC(18,2),
    docs_status                     TEXT,
    account_approval                TEXT,
    bank_approval                   TEXT,
    qfl_charges                     NUMERIC(18,2),
    ca_bill_status                  TEXT,           -- C/A Bill Status
    ca_bill_received_date           DATE,
    bill_submission_date_to_ac      DATE,
    current_status                  TEXT,
    remarks                         TEXT,
    currency                        TEXT,
    po_number                       TEXT REFERENCES purchase_order(po_number)
);'''

# ---------------------- IMPORT ITEM (lines of an import) -----------------
import_item_query = '''CREATE TABLE IF NOT EXISTS import_item(
    import_item_id      BIGSERIAL PRIMARY KEY,
    import_id           BIGINT NOT NULL REFERENCES import_details(import_id) ON DELETE CASCADE,
    item_code           TEXT REFERENCES items(item_code),
    qty                 NUMERIC(14,3),
    uom                 TEXT,
    wt_per_pc_t         NUMERIC(14,3),          -- Wt./Pc T
    unit_price          NUMERIC(18,2),
    elc_amount_per_unit NUMERIC(18,2),
    alc_amount_per_unit NUMERIC(18,2),
    alc_status          TEXT,
    alc_date            DATE,
    alc_lead_time       TEXT
);'''

# ---------------------- SHIPMENT DETAILS (one row per batch / B/L) --------
shipment_details_query = '''CREATE TABLE IF NOT EXISTS shipment_details(
    shipment_id                         BIGSERIAL PRIMARY KEY,
    import_id                           BIGINT NOT NULL REFERENCES import_details(import_id) ON DELETE CASCADE,
    batch_no                            TEXT,
    total_value_fc_batch_wise           NUMERIC(18,2),
    total_value_pkr_batch_wise          NUMERIC(18,2),
    hs_code                             TEXT,
    efs                                 TEXT,
    s_terms                             TEXT,
    qe_s_agent                          TEXT,
    target_vessel                       TEXT,
    s_line                              TEXT,
    local_agent                         TEXT,
    mode_of_shipment                    TEXT,
    bl_no                               TEXT,           -- B/L #
    free_days                           INTEGER,
    pol                                 TEXT,
    pod                                 TEXT,
    readiness_date                      DATE,
    etd                                 DATE,
    eta_1st                             DATE,
    eta_2nd                             DATE,
    eta_3rd                             DATE,
    eta_4th                             DATE,
    eta_final                           DATE,
    transit_time                        TEXT,
    c_agent                             TEXT,
    gd_file                             TEXT,
    gd_no                               TEXT,
    clearance_mode                      TEXT,
    gate_out                            DATE,
    last_free_day                       DATE,
    clearance_time                      TEXT,
    eta_works                           DATE,
    actual_lead_time                    TEXT,
    protocol_approval_variance_with_idm TEXT,
    total_variance                      TEXT,
    invoice_value                       NUMERIC(18,2),
    imp_value                           NUMERIC(18,2),
    tariff                              NUMERIC(18,2),
    clearance_charges                   NUMERIC(18,2)
);'''

# ---------------------- PAYMENT HISTORY (one row per payment event) -------
payment_history_query = '''CREATE TABLE IF NOT EXISTS payment_history(
    payment_id          BIGSERIAL PRIMARY KEY,
    import_id           BIGINT NOT NULL REFERENCES import_details(import_id) ON DELETE CASCADE,
    shipment_id         BIGINT REFERENCES shipment_details(shipment_id),  -- optional, per-batch payment
    payment_ref_no      TEXT,
    payment_ref_date    DATE,
    bank                TEXT,
    payment_mode        TEXT,
    adv_pay_date        DATE,
    retire_date         DATE,
    td_payment_date     DATE,           -- T&D payment date
    value_adv_payment   NUMERIC(18,2),  -- Value (ADV payment)
    value_lc            NUMERIC(18,2),  -- Value2 (LC value)
    value_td_payment    NUMERIC(18,2),  -- Value3 (T&D payment)
    invoice_value       NUMERIC(18,2),
    imp_value           NUMERIC(18,2),
    lc_payment_status   TEXT,
    td_payment_status   TEXT,
    ex_rate             NUMERIC(14,4)

);'''


# Order matters: masters first, then import parent, then its children.
imports_schemas_queries = [
    items_table_query,
    purchase_order_table_query,
    import_details_query,
    import_item_query,
    shipment_details_query,
    payment_history_query,
]

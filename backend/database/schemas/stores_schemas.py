# database/schemas/stores_schemas.py
#
# STORES module tables: stock, issuance, store_requisition.
#
# IMPORTANT: run this AFTER imports_schemas, because these tables reference
# items(item_code), which is created in imports_schemas.
#
# Note: store_requisition.ref_no logically references purchases(ref_no), but
# purchases is not owned here, so ref_no is left as a plain TEXT column for now.
# Whoever owns the purchases table can add that FK later if required.
#
# Style follows logistics_schemas.py.


# ---------------------- STOCK (current snapshot, one row per item+branch) -
stock_query = '''CREATE TABLE IF NOT EXISTS stock(
    stock_id            BIGSERIAL PRIMARY KEY,
    item_code           TEXT REFERENCES items(item_code),
    branch              TEXT,
    hold_qty            NUMERIC(14,3),
    stock_qty           NUMERIC(14,3),
    stock_qty_amount    NUMERIC(18,2),
    available_qty       NUMERIC(14,3),
    available_amount    NUMERIC(18,2)
);'''


# ---------------------- AB_ITEMS ----------------------------------------
ab_items_query = '''CREATE TABLE IF NOT EXISTS ab_items(
    item_code           TEXT REFERENCES items(item_code),
    branch_name         TEXT,
    rank                TEXT,
    safety_days         INT,
    lead_time_days      INT
);'''


# ---------------------- ISSUANCE (consumption transactions) --------------
issuance_query = '''CREATE TABLE IF NOT EXISTS issuance(
    serial_no           BIGSERIAL PRIMARY KEY,
    issuance_code       TEXT,   -- IssuanceCode from source
    item_code           TEXT REFERENCES items(item_code),
    department          TEXT,
    branch              TEXT,
    issue_to_others     TEXT,
    authorized_by       TEXT,
    issued_by           TEXT,
    received_by         TEXT,
    description         TEXT,
    ref_no              TEXT,
    demand_ref_no       TEXT,
    quantity            NUMERIC(14,3),
    status              TEXT,
    from_date           DATE,
    unit_price          NUMERIC(14,3),
    total_price         NUMERIC(18,2),
    job_number          TEXT
);'''

# ---------------------- STORE REQUISITION --------------------------------
store_requisition_query = '''CREATE TABLE IF NOT EXISTS store_requisition(
    req_id              BIGSERIAL PRIMARY KEY,
    item_code           TEXT REFERENCES items(item_code),
    ref_no              TEXT,               -- logically references purchases(ref_no)
    department          TEXT,
    branch              TEXT,
    prepare_date        DATE,
    description         TEXT,
    required_by         TEXT,
    req_quantity        NUMERIC(14,3),
    pur_quantity        NUMERIC(14,3),
    pending_quantity    NUMERIC(14,3),
    last_purchase       DATE,
    previous_price      TEXT,
    required_date       DATE,
    status              TEXT,
    sourced_by          TEXT,
    previous_supplier   TEXT,
    original_required_date   DATE,
    stock_in_date       DATE
);'''

#------------------------- PURCHASES DATA ------------------------------
purchases_query = '''CREATE TABLE IF NOT EXISTS purchases_data(
    purchase_id BIGSERIAL PRIMARY KEY,
    ref_no      TEXT, 		
    qty         INT,	
    branch      TEXT,
    amount      NUMERIC(14,3),	
    ppc_store   DATE,	
    required_d  DATE,		
    purchase    DATE, 		
    mop	        TEXT,
    dc_no       TEXT,
    bill_no     TEXT,	
    sourcing_o  TEXT,
    supplier    TEXT,
    item_code   TEXT REFERENCES items(item_code),
    po_number TEXT REFERENCES purchase_order(po_number)
);
'''


stores_schemas_queries = [stock_query, purchases_query, issuance_query, store_requisition_query, ab_items_query]

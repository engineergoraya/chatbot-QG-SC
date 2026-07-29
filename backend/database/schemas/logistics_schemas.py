#---------------------- EXPORTS SHEET -------------------------------------------
exports_table_query = '''CREATE TABLE IF NOT EXISTS exports(
    export_id       BIGSERIAL PRIMARY KEY,
    exp_no          TEXT NOT NULL,
    batch_no        TEXT NOT NULL DEFAULT '', 
    exp_batch_raw   TEXT,
    customer        TEXT,
    shipping_agent  TEXT,
    bank            TEXT,
    payment_term    TEXT,
    bl_type         TEXT,
    gate_out_date   DATE,
    cut_Off_date    DATE,
    sailing_date    DATE,
    handed_over_to  TEXT,

    UNIQUE (exp_no, batch_no)
);'''

export_documents_query = '''CREATE TABLE IF NOT EXISTS export_documents(
    document_id     BIGSERIAL PRIMARY KEY,
    export_id       BIGINT NOT NULL references exports(export_id) ON DELETE CASCADE,
    party           TEXT,
    document_type   TEXT,
    Status          TEXT
);
'''

#------------------------- SHIPMENTS SHEET ----------------------------------
export_shipments_query = '''CREATE TABLE IF NOT EXISTS export_shipments(
    shipment_id         BIGSERIAL PRIMARY KEY,
    export_id           BIGINT references exports(export_id), 
    exp_batch_raw       TEXT,
    shipment_stage      TEXT,
    shipment_status     TEXT,
    shipment_terms      TEXT,
    efs                 TEXT,
    description         TEXT,
    job_no              TEXT,
    country             TEXT,
    pod                 TEXT,
    color_code          TEXT,
    pkgs                INT,
    net_weight_kgs      NUMERIC(14, 3),
    gross_weight_kgs    NUMERIC(14, 3),
    s_agent             TEXT,
    c_agent             TEXT,
    s_line              TEXT,
    cro_no              TEXT,
    lcl                 TEXT,
    air                 TEXT,
    qfl_arrival_date    DATE,
    port_in_date        DATE,
    cut_off_date        DATE,
    etd_karachi         DATE,
    cro_arrival_date    DATE,
    actual_arrival_date DATE,
    stuffing_location   TEXT,
    pick_up_time        TEXT,
    loading_t        TEXT,
    v_and_v_no          TEXT, 
    quoted_sea_freight  NUMERIC(18,2),
    actual_sea_freight  NUMERIC(18,2),
    lhr_khi_cost        NUMERIC(18,2),
    fumigation_cost     NUMERIC(18,2),
    lashing_cost        NUMERIC(18,2),
    qfl_port_cost       NUMERIC(18,2),
    qfl_cost            NUMERIC(18,2),
    clearance_cost      NUMERIC(18,2),
    dhl_charges         NUMERIC(18,2),
    insurance           NUMERIC(18,2),
    wharfage            NUMERIC(18,2)
)
'''

shipment_containers_query = '''CREATE TABLE IF NOT EXISTS shipment_containers (
    container_row_id    BIGSERIAL PRIMARY KEY,
    shipment_id         BIGINT NOT NULL REFERENCES export_shipments(shipment_id) ON DELETE CASCADE,
    size                SMALLINT NOT NULL CHECK (size IN (20, 40)),
    type                TEXT NOT NULL,
    qty                 INT NOT NULL CHECK (qty > 0)
);
'''

#------------------------ PACKING SHEET -----------------------------------
packing_details_query = '''CREATE TABLE IF NOT EXISTS packing_details (
    packing_id          BIGSERIAL PRIMARY KEY,
    export_id           BIGINT REFERENCES exports(export_id),
    exp_batch_raw       TEXT,
    business_type       TEXT,
    product_category    TEXT,
    customer_type       TEXT,
    customer            TEXT,            
    jobs_no             TEXT,
    description         TEXT,
    works               TEXT,
    color_code          TEXT,
    qty                 NUMERIC(12,2),
    qty_uom             TEXT,           
    pkgs                NUMERIC(12,2),
    net_weight_kgs      NUMERIC(14,3),
    gross_weight_kgs    NUMERIC(14,3),
    target_packing_material_mfg_date DATE,
    actual_packing_material_mfg_date DATE,
    target_packing_date DATE,
    actual_packing_date DATE,
    packing_status      TEXT,
    gate_out_date       DATE,
    target_rfd          DATE,
    actual_rfd_date     DATE,
    quoted_packing_cost NUMERIC(18,2),
    actual_packing_cost NUMERIC(18,2),
    overall_status      TEXT
);'''

#----------------------- SHIFTING MOVEMENTS(INBOUND/OUTBOUND) SHEET ---------------------- 
shifting_movements_query = '''CREATE TABLE IF NOT EXISTS shifting_movements (
    shifting_id             BIGSERIAL PRIMARY KEY,
    export_id               BIGINT REFERENCES exports(export_id),
    exp_batch_raw           TEXT,
    movement_type           TEXT,     
    execution_date          DATE,
    shipment_ref_idm        TEXT,     
    requester               TEXT,
    sender                  TEXT,
    customer                TEXT,
    item_name               TEXT,
    qty_packages            TEXT,     
    pickup_point            TEXT,
    destination             TEXT,
    province                TEXT,
    transporter             TEXT,
    gross_weight_kgs        NUMERIC(14,3),
    total_gross_weight_kgs  NUMERIC(14,3),
    eta_destination         DATE,
    dispatch_note_date      DATE,
    cut_off_date            DATE,
    remarks                 TEXT,
    driver_no               TEXT,
    vehicle_number          TEXT,
    vehicle_type            TEXT,
    no_of_vehicles          INTEGER,
    container_number        TEXT,
    containers_20ft         INTEGER,
    containers_40ft         INTEGER,
    actual_freight_rs       NUMERIC(18,2),
    quoted_freight_rs       NUMERIC(18,2),
    detention_amount        NUMERIC(18,2),
    payment_status          TEXT,
    tracking_status         TEXT,
    bill_builty_status      TEXT,
    ledger_entry_status     TEXT,
    bill_clearance_status   TEXT,
    shipment_status         TEXT,
    operational_status      TEXT
);'''

logistics_schemas_queries = [exports_table_query, export_documents_query, export_shipments_query, shipment_containers_query, packing_details_query, shifting_movements_query]



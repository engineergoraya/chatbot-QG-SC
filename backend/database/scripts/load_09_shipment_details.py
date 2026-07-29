"""
load_09_shipment_details.py — loads SHIPMENT DETAILS (batch / B/L level) from
the Import Status Sheet into shipment_details.

With one import per real row, each row contributes at most one shipment row —
inserted only when the row actually carries shipment data (a batch, B/L, GD,
ETA, port, cost, etc.). Rows with no shipment fields are skipped so we don't
create empty shipment rows.

FK:  import_id via load_import_map() keyed on the row-position ref written by
load_07.

Run AFTER load_06_import_masters.py and load_07_import_details.py.  Plain
INSERT — TRUNCATE shipment_details CASCADE before reloading.

Usage:  python -m database.scripts.load_09_shipment_details
"""

from database.scripts.etl_stores_imports import (
    read_import_rows, import_ref_for, load_import_map, bulk_insert,
    clean_text, clean_number, clean_int, clean_date,
)

# (db column, excel column, cleaner) for the batch-level fields.
SHIP_MAP = [
    ("total_value_fc_batch_wise",           "Total Value(FC) Batch_Wise", clean_number),
    ("total_value_pkr_batch_wise",          "Total Value(PKR) Batch_Wise", clean_number),
    ("hs_code",                             "H.S. Code",                  clean_text),
    ("efs",                                 "EFS",                        clean_text),
    ("s_terms",                             "S/Terms",                    clean_text),
    ("qe_s_agent",                          "QE S/Agent",                 clean_text),
    ("target_vessel",                       "Target Vessel",              clean_text),
    ("s_line",                              "S. Line",                    clean_text),
    ("local_agent",                         "Local Agent",                clean_text),
    ("mode_of_shipment",                    "Mode of Shipment",           clean_text),
    ("bl_no",                               "B/L #",                      clean_text),
    ("free_days",                           "Free Days",                  clean_int),
    ("pol",                                 "POL",                        clean_text),
    ("pod",                                 "POD",                        clean_text),
    ("readiness_date",                      "Rediness Dt.",               clean_date),
    ("etd",                                 "ETD",                        clean_date),
    ("eta_1st",                             "1st ETA",                    clean_date),
    ("eta_2nd",                             "2nd ETA",                    clean_date),
    ("eta_3rd",                             "3rd ETA",                    clean_date),
    ("eta_4th",                             "4th ETA",                    clean_date),
    ("eta_final",                           "ETA",                        clean_date),
    ("transit_time",                        "Transit Time",               clean_text),
    ("c_agent",                             "C/Agent",                    clean_text),
    ("gd_file",                             "GD File",                    clean_text),
    ("gd_no",                               "GD No",                      clean_text),
    ("clearance_mode",                      "Clearance Mode",             clean_text),
    ("gate_out",                            "Gate-out",                   clean_date),
    ("last_free_day",                       "Last Free Day",              clean_date),
    ("clearance_time",                      "Clearance Time",             clean_text),
    ("eta_works",                           "ETA Works",                  clean_date),
    ("actual_lead_time",                    "Actual Lead Time",           clean_text),
    ("protocol_approval_variance_with_idm", "Protocol Approval Variance with IDM", clean_text),
    ("total_variance",                      "Total Variance",             clean_text),
    ("invoice_value",                       "Invoice Value",              clean_number),
    ("imp_value",                           "Imp Value",                  clean_number),
    ("tariff",                              "Tariff",                     clean_number),
    ("clearance_charges",                   "Clearance",                  clean_number),
]

SHIP_COLUMNS = ["import_id", "batch_no"] + [db for db, _, _ in SHIP_MAP]


def load_shipment_details(conn):
    df = read_import_rows()
    import_map = load_import_map(conn)

    rows = []
    unmatched = 0
    for idx, r in df.iterrows():
        import_id = import_map.get(import_ref_for(idx))
        if import_id is None:
            unmatched += 1
            continue
        batch_no = clean_text(r.get("Batch No"))
        ship = tuple(fn(r.get(excel)) for _, excel, fn in SHIP_MAP)
        if batch_no is None and all(v is None for v in ship):
            continue   # nothing shipment-related on this row
        rows.append((import_id, batch_no) + ship)

    if unmatched:
        print(f"WARNING: {unmatched} shipment rows had no parent in "
              f"import_details — run load_07_import_details.py first")
    bulk_insert(conn, "shipment_details", SHIP_COLUMNS, rows)

"""
load_07_import_details.py — loads the parent IMPORT DETAILS table from the
Import Status Sheet.

This export has NO natural import id, and import_id is a DB-generated surrogate
(BIGSERIAL). Each real data row is therefore loaded as one import, and its
Excel row position is written to import_details.import_ref (via import_ref_for)
purely so the child loaders can link back to the correct parent.

FKs:
  * supplier_id — resolved from the supplier NAME via ensure_suppliers()'s map.
  * po_number   — the PO text (empty in the current export -> NULL).

'Branch' has no column in this sheet, so it stays NULL.

Run AFTER load_06_import_masters.py.  Plain INSERT, so TRUNCATE import_details
CASCADE before reloading.

Usage:  python -m database.scripts.load_07_import_details
"""
from database.scripts.etl_stores_imports import (
    read_import_rows, import_ref_for, bulk_insert,
    clean_text, clean_number, clean_date,
    resolve_import_currencies, get_pkr_rates,
)

# (db column, excel column, cleaner) for the header-level fields.
DETAIL_MAP = [
    ("branch",                     "Works",                      clean_text),
    ("tab_status",                 "Tab Status",                  clean_text),
    ("file_no",                    "File No",                     clean_text),
    ("category",                   "Category",                    clean_text),
    ("job_no",                     "Job No",                      clean_text),
    ("mo_no",                      "MO No",                       clean_text),
    ("customer",                   "Customer",                    clean_text),
    ("total_wt_ton",               "Total Wt.\nTon",              clean_number),
    ("demand_date",                "Demand Dt.",                  clean_date),
    ("protocol_approval_date",     "Protocol Approval Date",      clean_date),
    ("req_date",                   "Req. Dt.",                    clean_date),
    ("lead_time",                  "Lead Time",                   clean_text),
    ("supplier",                   "Supplier",                    clean_text),
    ("supplier_country",           "Country",                     clean_text),
    ("supplier_city",              "City",                        clean_text),
    ("gin_status",                 "GIN Status",                  clean_text),
    ("gin_date",                   "GIN Date",                    clean_date),
    ("total_value_fc",             "Total Value(FC)",             clean_number),
    ("docs_status",                "Docs Status",                 clean_text),
    ("account_approval",           "Account Approval",            clean_text),
    ("bank_approval",              "Bank Approval",               clean_text),
    ("qfl_charges",                "QFL Charges",                 clean_number),
    ("ca_bill_status",             "C/A Bill Status",             clean_text),
    ("ca_bill_received_date",      "C/A Bill Received Date",      clean_date),
    ("bill_submission_date_to_ac", "Bill Submission Date to A/C", clean_date),
    ("current_status",             "Current Status",              clean_text),
    ("remarks",                    "Remarks",                     clean_text),
    
]

DETAIL_COLUMNS = (
    ["import_ref"] + [db for db, _, _ in DETAIL_MAP]
    + ["po_number", "total_value_pkr", "currency"]
)


def load_import_details(conn):
    df = read_import_rows()
    currencies = resolve_import_currencies()
    rates = get_pkr_rates(set(currencies.values()))
    computed = 0

    rows = []
    for idx, r in df.iterrows():
        detail = tuple(fn(r.get(excel)) for _, excel, fn in DETAIL_MAP)
        po_number = clean_text(r.get("PO No"))
        fc = clean_number(r.get("Total Value(FC)"))
        existing_pkr = clean_number(r.get("Total Value(PKR)"))
        iso = currencies.get(idx)
        if existing_pkr not in (None, 0):
            pkr = existing_pkr                        # keep the recorded value
        elif fc not in (None, 0) and rates.get(iso):
            pkr = round(fc * rates[iso], 2)           # compute missing at live rate
            computed += 1
        else:
            pkr = existing_pkr                        # can't compute -> leave as-is
        rows.append((import_ref_for(idx),) + detail + (po_number, pkr, iso))

    print(f"Imports (one per real row): {len(rows)}; "
          f"computed PKR for {computed} previously-missing")
    bulk_insert(conn, "import_details", DETAIL_COLUMNS, rows)

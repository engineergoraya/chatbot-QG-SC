"""
load_10_payment_history.py — loads PAYMENT HISTORY from the Import Status Sheet
into payment_history.

With one import per real row, each row contributes at most one payment row —
inserted only when the row carries payment data (a payment ref, an
advance/retire/T&D date, or an LC / advance / T&D value). Rows with none of
these are skipped so we don't create empty payment rows.

FKs:
  * import_id  — via load_import_map() keyed on the row-position ref (load_07).
  * shipment_id — via load_shipment_by_import(); NULL when the import has no
    shipment row.

Run AFTER load_07_import_details.py and load_09_shipment_details.py.  Plain
INSERT — TRUNCATE payment_history before reloading.

Usage:  python -m database.scripts.load_10_payment_history
"""

from database.scripts.etl_stores_imports import (
    read_import_rows, import_ref_for, load_import_map, load_shipment_by_import,
    bulk_insert, clean_text, clean_number, clean_date,
)

# (db column, excel column, cleaner) for the payment fields.
PAY_MAP = [
    ("payment_ref_no",    "Payment Ref No",     clean_text),
    ("payment_ref_date",  "Payment Ref Date",   clean_date),
    ("bank",              "Bank",               clean_text),
    ("payment_mode",      "Payment Mode",       clean_text),
    ("adv_pay_date",      "Adv Pay Dt.",        clean_date),
    ("retire_date",       " Retire. Dt.",       clean_date),   # note leading space in source header
    ("td_payment_date",   "T&D Payment Date",   clean_date),
    ("value_adv_payment", "Value",              clean_number),
    ("value_lc",          "Value2",             clean_number),
    ("value_td_payment",  "Value3",             clean_number),
    ("invoice_value",     "Invoice Value",      clean_number),
    ("imp_value",         "Imp Value",          clean_number),
    ("lc_payment_status", "LC Payment Status",  clean_text),
    ("td_payment_status", "T&D Payment Status", clean_text),
    ("ex_rate",           "Ex. Rate",           clean_number),
]

PAY_COLUMNS = ["import_id", "shipment_id"] + [db for db, _, _ in PAY_MAP]

# If any of these is present, the row is a real payment event.
_EVENT_FIELDS = (
    "payment_ref_no", "adv_pay_date", "retire_date", "td_payment_date",
    "value_adv_payment", "value_lc", "value_td_payment",
)


def load_payment_history(conn):
    df = read_import_rows()
    import_map = load_import_map(conn)
    shipment_by_import = load_shipment_by_import(conn)

    rows = []
    unmatched = 0
    for idx, r in df.iterrows():
        import_id = import_map.get(import_ref_for(idx))
        if import_id is None:
            unmatched += 1
            continue
        rec = {db: fn(r.get(excel)) for db, excel, fn in PAY_MAP}
        if not any(rec[f] is not None for f in _EVENT_FIELDS):
            continue   # not a payment row
        shipment_id = shipment_by_import.get(import_id)
        rows.append((import_id, shipment_id) + tuple(rec[db] for db, _, _ in PAY_MAP))

    if unmatched:
        print(f"WARNING: {unmatched} payment rows had no parent in "
              f"import_details — run load_07_import_details.py first")
    bulk_insert(conn, "payment_history", PAY_COLUMNS, rows)

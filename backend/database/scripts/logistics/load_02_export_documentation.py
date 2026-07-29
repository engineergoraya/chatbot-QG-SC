"""
load_02_export_documentation.py — loads the 'Export Documentation Database'
sheet into the user's schema:

  1. UPDATEs the exports parent with the documentation fields, which are
     merged into the exports table in this schema
     (shipping_term, shipping_agent, bank, payment_term, bl_type,
      gate_out_date, cut_off_date, sailing_date, handed_over_to)
  2. INSERTs export_documents rows (LONG format: one row per
     party + document type, melted from the ~21 status columns)

For idempotency (safe re-runs): existing export_documents rows for each
export are DELETEd before re-inserting, since this schema has no unique
constraint on (export_id, party, document_type).

Computed columns (Total/Completed/Pending Documents, Completion %s,
Ready-for flags, Missing-documents lists, Days Pending, Delay Status)
are intentionally NOT loaded — recreate them with the completion view.

Run AFTER load_01_exports.py.
Usage:  python load_02_export_documentation.py
"""

from psycopg2.extras import execute_values
from database.scripts.etl_common import (
    read_sheet, make_export_key, clean_text, clean_status, clean_date,
    load_export_map,
)
from pathlib import Path

current_dir = Path(__file__).resolve().parent
directory = Path(current_dir.parents[2] / "data" / "logistics")

files = list(directory.iterdir())

EXCEL_FILE = files[0]

# Excel status column -> (party, document_type)
STATUS_MAP = {
    "Customs Invoice Status":            ("Customs",  "Invoice"),
    "Customs Packing List Status":       ("Customs",  "Packing List"),
    "Customs FI Status":                 ("Customs",  "FI"),
    "Customs Bank Documents Status":     ("Customs",  "Bank Documents"),
    "Customer Invoice Status":           ("Customer", "Invoice"),
    "Customer Packing List Status":      ("Customer", "Packing List"),
    "Bank Invoice Status":               ("Bank",     "Invoice"),
    "Bank Packing List Status":          ("Bank",     "Packing List"),
    "Bank FI Status":                    ("Bank",     "FI"),
    "Bank GD Status":                    ("Bank",     "GD"),
    "Bank BL Status":                    ("Bank",     "BL"),
    "Bank Certificate of Origin Status": ("Bank",     "Certificate of Origin"),
    "GD Status":                         ("Other",    "GD"),
    "Undertaking Status":                ("Other",    "Undertaking"),
    "EFS Status":                        ("Other",    "EFS"),
    "BL Status":                         ("Other",    "BL"),
    "Certificate of Origin Status":      ("Other",    "Certificate of Origin"),
    "Fumigation Certificate Status":     ("Other",    "Fumigation Certificate"),
    "Insurance Certificate Status":      ("Other",    "Insurance Certificate"),
    "DHL Documents Status":              ("Other",    "DHL Documents"),
    "Insurance Bill Status":             ("Other",    "Insurance Bill"),
}

# Excel column -> exports table column (documentation fields merged into exports)
HEADER_MAP = [
    ("Shipping Agent",          "shipping_agent", clean_text),
    ("Bank Name",               "bank",           clean_text),   # renamed in schema
    ("Payment Term",            "payment_term",   clean_text),
    ("BL Type",                 "bl_type",        clean_text),
    ("Gate-Out Date",           "gate_out_date",  clean_date),
    ("Cut Off Date",            "cut_off_date",   clean_date),
    ("Sailing Date",            "sailing_date",   clean_date),
    ("Handed Over To Mr.Umar",  "handed_over_to", clean_text),   # person name -> data
]


def load_export_documenttion(conn):
    df = read_sheet("Export Documentation Database", EXCEL_FILE)
    export_map = load_export_map(conn)
    updates = []        # (shipping_term, ..., handed_over_to, export_id)
    document_rows = []  # (export_id, party, document_type, status)
    touched_ids = []
    unmatched = 0
    for _, r in df.iterrows():
        key = make_export_key(r.get("Exp. #"), r.get("Batch #"))
        export_id = export_map.get(key)
        if export_id is None:
            unmatched += 1
            continue    # load_01 should have created every key
        updates.append(
            tuple(fn(r.get(col)) for col, _, fn in HEADER_MAP) + (export_id,)
        )
        touched_ids.append(export_id)
        for col, (party, doc_type) in STATUS_MAP.items():
            status = clean_status(r.get(col))
            if status is None:
                continue
            document_rows.append((export_id, party, doc_type, status))
    if unmatched:
        print(f"WARNING: {unmatched} rows had keys not found in exports "
              f"— run load_01_exports.py first / check key cleaning")
    set_clause = ", ".join(f"{dbcol} = %s" for _, dbcol, _ in HEADER_MAP)
    with conn.cursor() as cur:
        # 1. write the merged documentation fields onto exports
        for values in updates:
            cur.execute(
                f"UPDATE exports SET {set_clause} WHERE export_id = %s",
                values,
            )
        # 2. replace this sheet's document rows (idempotent re-runs)
        if touched_ids:
            cur.execute(
                "DELETE FROM export_documents WHERE export_id = ANY(%s)",
                (touched_ids,),
            )
        execute_values(
            cur,
            "INSERT INTO export_documents "
            "(export_id, party, document_type, status) VALUES %s",
            document_rows,
            page_size=500,
        )
    conn.commit()
    print(f"  exports: updated documentation fields on {len(updates)} rows")
    print(f"  export_documents: inserted {len(document_rows)} rows")
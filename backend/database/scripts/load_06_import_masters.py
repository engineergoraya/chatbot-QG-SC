"""
load_06_import_masters.py — builds the shared master tables from the Import
Status Sheet:  suppliers, items, purchase_order.

Run this FIRST for the imports/stores domain (analogous to load_01_exports for
logistics). The transaction tables (import_details, import_item,
shipment_details, stock, issuance, store_requisition) all carry FKs into these
masters, so the masters must exist before those children load.

The import sheet is the richest master source (it carries item, supplier and
PO together on one row), which is why the schema keeps the masters inside the
imports module. Stock / issuance / requisition loaders additionally "top up"
`items` with any codes the import sheet didn't have, via ensure_items().

All three upserts are idempotent (ON CONFLICT DO NOTHING), so re-running is safe.

Usage:  python -m database.scripts.load_06_import_masters
"""

from database.scripts.etl_stores_imports import (
    IMPORT_FILE, IMPORT_HEADER_ROW, read_report,
    ensure_items, ensure_purchase_orders,
    clean_text,
)


def load_import_masters(conn):
    df = read_report(IMPORT_FILE, header=IMPORT_HEADER_ROW)

    item_records, po_records = [], []
    for _, r in df.iterrows():
        item_records.append({
            "item_code":     clean_text(r.get("Item Code")),
            "item":          clean_text(r.get("Item Name")),
            "uom":           clean_text(r.get("UOM")),
            "item_category": clean_text(r.get("Item Cateogry")),   # source misspelling
            "specs":         clean_text(r.get("Specs/Standard")),
        })
       
        po_records.append({
            "po_number": clean_text(r.get("PO No")),
            "po_date":   None,   # the import sheet has no PO date column
        })

    ensure_purchase_orders(conn, po_records)
    ensure_items(conn, item_records)

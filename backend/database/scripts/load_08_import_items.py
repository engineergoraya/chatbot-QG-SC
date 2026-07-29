"""
load_08_import_items.py — loads IMPORT ITEM lines from the Import Status Sheet
into import_item.

With one import per real row, each row contributes at most one item line (the
rows that carry an Item Code). Rows without an Item Code are skipped.

FKs:  import_id via load_import_map() keyed on the row-position ref written by
load_07; item_code -> items(item_code) (ensured defensively here too).

Note: the source has no explicit per-line FC value, so `line_value_fc` stays
NULL — populate it from a "Line Value(FC)" column if one is added.

Run AFTER load_06_import_masters.py and load_07_import_details.py.
Usage:  python -m database.scripts.load_08_import_items
"""

from database.scripts.etl_stores_imports import (
    read_import_rows, import_ref_for, ensure_items, load_import_map,
    bulk_insert, clean_text, clean_number, clean_date,
)

# (db column, excel column, cleaner) for the line-level fields.
LINE_MAP = [
    ("qty",                 "Qty.",             clean_number),
    ("uom",                 "UOM",              clean_text),
    ("wt_per_pc_t",         "Wt./Pc T",         clean_number),
    ("unit_price",          "Unit Price",       clean_number),
    ("elc_amount_per_unit", "ELC Amount/Unit",  clean_number),
    ("alc_amount_per_unit", "ALC Amount/Unit2", clean_number),
    ("alc_status",          "ALC Status",       clean_text),
    ("alc_date",            "ALC Date",         clean_date),
    ("alc_lead_time",       "ALC Lead Time",    clean_text),
]

LINE_COLUMNS = ["import_id", "item_code"] + [db for db, _, _ in LINE_MAP]


def load_import_items(conn):
    df = read_import_rows()
    df.columns = df.columns.str.strip()

    # Top up items so every line's FK resolves.
    ensure_items(conn, [
        {"item_code":     clean_text(r.get("Item Code")),
         "item":          clean_text(r.get("Item Name")),
         "uom":           clean_text(r.get("UOM")),
         "item_category": clean_text(r.get("Item Cateogry")),
         "specs":         clean_text(r.get("Specs/Standard"))}
        for _, r in df.iterrows()
            
    ])

    import_map = load_import_map(conn)
    rows = []
    unmatched = 0
    for idx, r in df.iterrows():
        item_code = clean_text(r.get("Item Code"))
        if not item_code:
            continue
        import_id = import_map.get(import_ref_for(idx))
        if import_id is None:
            unmatched += 1
            continue
        line = tuple(fn(r.get(excel)) for _, excel, fn in LINE_MAP)
        rows.append((import_id, item_code) + line)

    if unmatched:
        print(f"WARNING: {unmatched} item rows had no parent in import_details "
              f"— run load_07_import_details.py first")
    bulk_insert(conn, "import_item", LINE_COLUMNS, rows)

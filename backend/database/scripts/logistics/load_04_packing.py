"""
load_04_packing.py — loads the 'Master Packing Database' sheet into
packing_details. RFD and packing-cost fields are merged into the same
table (no separate 1:1 tables).

export_id is NULL for rows without an Exp # (Local business etc.) —
the FK is intentionally nullable, matching the data (only ~668 of
~1375 rows have an export key).

Computed columns (all Delay columns, On-Time Packing, Packing
Efficiency %, Days Pending Packing, cost variances) are NOT loaded —
recreated by the SQL view v_packing_metrics.

Run AFTER load_01_exports.py.
Usage:  python load_04_packing.py
"""

from database.scripts.etl_common import (
    read_sheet, make_export_key, clean_text, clean_date, clean_number,
    parse_qty_uom, load_export_map, bulk_insert,
)
from pathlib import Path

current_dir = Path(__file__).resolve().parent
directory = Path(current_dir.parents[2] / "data" / "logistics")

files = list(directory.iterdir())

EXCEL_FILE = files[0]

COLUMNS = [
    "export_id", "exp_batch_raw", "business_type", "product_category",
    "customer_type", "customer", "jobs_no", "description", "works",
    "color_code", "qty", "qty_uom", "pkgs", "net_weight_kgs",
    "gross_weight_kgs", "target_packing_material_mfg_date",
    "actual_packing_material_mfg_date", "target_packing_date",
    "actual_packing_date", "packing_status", "gate_out_date",
    "target_rfd", "actual_rfd_date", "quoted_packing_cost",
    "actual_packing_cost", "overall_status",
]


def load_packing(conn):
    df = read_sheet("Master Packing Database", EXCEL_FILE)
    export_map = load_export_map(conn)
    rows = []
    no_key = 0
    for _, r in df.iterrows():
        key = make_export_key(r.get("Exp #"), r.get("Batch #"))
        export_id = export_map.get(key) if key else None
        if export_id is None:
            no_key += 1
        qty, qty_uom = parse_qty_uom(r.get("Qty."))
        rows.append((
            export_id,
            clean_text(r.get("Primary Key")),
            clean_text(r.get("Business Type")),
            clean_text(r.get("Product Category")),
            clean_text(r.get("Customer Type")),
            clean_text(r.get("Customer")),
            clean_text(r.get("Jobs #")),
            clean_text(r.get("Description")),
            clean_text(r.get("Works")),
            clean_text(r.get("Color Code")),
            qty,
            qty_uom,
            clean_number(r.get("Pkgs.")),
            clean_number(r.get("Net Weight (Kgs)")),
            clean_number(r.get("Gross Weight (Kgs)")),
            clean_date(r.get("Target Packing Material Mfg Date")),
            clean_date(r.get("Actual Packing Material Mfg Date")),
            clean_date(r.get("Target Packing Date")),
            clean_date(r.get("Actual Packing Date")),
            clean_text(r.get("Packing Status")),
            clean_date(r.get("Gate Out Date")),
            clean_date(r.get("Target RFD")),
            clean_date(r.get("Actual RFD Date")),
            clean_number(r.get("Quoted Packing Cost")),
            clean_number(r.get("Actual Packing Cost")),
            clean_text(r.get("Overall Status")),
        ))
    print(f"NOTE: {no_key} packing rows have no export link "
          f"(Local business / unmatched key) — loaded with export_id = NULL")
    bulk_insert(conn, "packing_details", COLUMNS, rows)
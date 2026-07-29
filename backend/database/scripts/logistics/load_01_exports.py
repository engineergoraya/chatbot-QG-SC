"""
load_01_exports.py — builds the EXPORTS parent table.

Run this FIRST. It collects every (Exp #, Batch #) key from all four sheets,
cleans them with the shared clean_key() function, deduplicates, and inserts
one row per consignment. Customer/Country/POD are taken from the first sheet
that provides them (Shipment Master preferred, then Export Documentation).

Usage:  python load_01_exports.py
"""

import pandas as pd
from database.scripts.etl_common import (
    read_sheet, make_export_key, clean_key, clean_text,
    bulk_insert,
)
from pathlib import Path
current_dir = Path(__file__).resolve().parent
directory = Path(current_dir.parents[2] / "data" / "logistics")
print(directory)
files = list(directory.iterdir())

EXCEL_FILE = files[0]

def load_exports(connection):
    consignments = {}   # (exp_no, batch_no) -> dict of attributes

    def register(key, raw, customer=None):
        if key is None:
            return
        rec = consignments.setdefault(
            key, {"raw": raw, "customer": None}
        )
        if customer and not rec["customer"]:
            rec["customer"] = customer

    # --- Export Documentation (has customer) ------------------------------
    exp = read_sheet("Export Documentation Database", EXCEL_FILE)
    for _, r in exp.iterrows():
        key = make_export_key(r.get("Exp. #"), r.get("Batch #"))
        register(
            key, clean_text(r.get("Primary Key")),
            customer=clean_text(r.get("Customer")),
        )

    # --- Master Packing ----------------------------------------------------
    # pack = read_sheet("Master Packing Database")
    # for _, r in pack.iterrows():
    #     key = make_export_key(r.get("Exp #"), r.get("Batch #"))
    #     register(
    #         key, clean_text(r.get("Primary Key")),
    #         customer=clean_text(r.get("Customer")),
    #     )

    # --- Inbound & Outbound Shifting ---------------------------------------
    # shift = read_sheet("Inbound & Outbound Shifting")
    # for _, r in shift.iterrows():
    #     key = make_export_key(r.get("Exp # or Key"), r.get("Batch #"))
    #     register(key, clean_text(r.get("Primary Key")),
    #              customer=clean_text(r.get("Customer")))

    # print(f"\nDistinct consignments found: {len(consignments)}")

    rows = [
        (exp_no, batch_no, rec["raw"], rec["customer"])
        for (exp_no, batch_no), rec in sorted(consignments.items())
    ]


    bulk_insert(
        connection,
        "exports",
        ["exp_no", "batch_no", "exp_batch_raw", "customer"],
        rows,
        conflict_clause="ON CONFLICT (exp_no, batch_no) DO NOTHING",
    )

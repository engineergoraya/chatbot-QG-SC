"""
load_05_shifting.py — loads the 'Inbound & Outbound Shifting' sheet into
shifting_movements. Vehicle, cost, and status columns are merged into the
same table (one row per movement).

The sheet's own 'Primary Key' column is junk ('--' in ~93% of rows), so
the table uses a surrogate shifting_id; export_id is a nullable FK matched
via (Exp # or Key, Batch #). 'Shipment Ref. / IDM #' is preserved as the
future bridge to the imports database for inbound movements.

Computed columns (Savings, Savings %, Freight Variance, Rate Per Kg,
Rate Per Km, Cost Per KG, Transit Days, Delay Days) are NOT loaded —
recreated by the SQL view v_shifting_metrics.

Run AFTER load_01_exports.py.
Usage:  python load_05_shifting.py
"""

from database.scripts.etl_common import (
    read_sheet, make_export_key, clean_text, clean_date, clean_number,
    clean_int, load_export_map, bulk_insert,
)
from pathlib import Path

current_dir = Path(__file__).resolve().parent
directory = Path(current_dir.parents[2] / "data" / "logistics")

files = list(directory.iterdir())

EXCEL_FILE = files[0]

COLUMNS = [
    "export_id", "exp_batch_raw", "movement_type", "execution_date",
    "shipment_ref_idm", "requester", "sender", "customer", "item_name",
    "qty_packages", "pickup_point", "destination", "province", "transporter",
    "gross_weight_kgs", "total_gross_weight_kgs", "eta_destination",
    "dispatch_note_date", "cut_off_date", "remarks", "driver_no",
    "vehicle_number", "vehicle_type", "no_of_vehicles", "container_number",
    "containers_20ft", "containers_40ft", "actual_freight_rs",
    "quoted_freight_rs", "detention_amount", "payment_status",
    "tracking_status", "bill_builty_status", "ledger_entry_status",
    "bill_clearance_status", "shipment_status", "operational_status",
]


def load_shifting(conn):
    df = read_sheet("Inbound & Outbound Shifting", EXCEL_FILE)
    export_map = load_export_map(conn)
    rows = []
    linked = 0
    for _, r in df.iterrows():
        key = make_export_key(r.get("Exp # or Key"), r.get("Batch #"))
        export_id = export_map.get(key) if key else None
        if export_id is not None:
            linked += 1
        rows.append((
            export_id,
            clean_text(r.get("Primary Key")),
            clean_text(r.get("Movement Type")),
            clean_date(r.get("Execution Date")),
            clean_text(r.get("Shipment Ref. / IDM #")),
            clean_text(r.get("Requester")),
            clean_text(r.get("Sender")),
            clean_text(r.get("Customer")),
            clean_text(r.get("Item Name")),
            clean_text(r.get("Qty. / No. of Packages")),
            clean_text(r.get("Pickup Point")),
            clean_text(r.get("Destination")),
            clean_text(r.get("Province")),
            clean_text(r.get("Transporter")),
            clean_number(r.get("Gross Weight (Kgs)")),
            clean_number(r.get("Total Gross Weight (Kgs)")),
            clean_date(r.get("ETA Works / ETA Destination")),
            clean_date(r.get("Dispatch Note Date")),
            clean_date(r.get("Cut Off Date")),
            clean_text(r.get("Remarks")),
            clean_text(r.get("Driver #")),
            clean_text(r.get("Vehicle Number")),
            clean_text(r.get("Vehicle Type")),
            clean_int(r.get("No. of Vehicles")),
            clean_text(r.get("Container Number")),
            clean_int(r.get("20 FT Containers")),
            clean_int(r.get("40 FT Containers")),
            clean_number(r.get("Actual Freight (Rs.)")),
            clean_number(r.get("Quoted Freight (Rs.)")),
            clean_number(r.get("Detention Amount")),
            clean_text(r.get("Payment Terms")),
            clean_text(r.get("Tracking Status")),
            clean_text(r.get("Bill/Builty Status")),
            clean_text(r.get("Ledger Entry Status")),
            clean_text(r.get("Bill Clearance Status")),
            clean_text(r.get("Shipment Status")),
            clean_text(r.get("Operational Status")),
        ))
    print(f"NOTE: {linked} of {len(rows)} movements linked to an export")
    bulk_insert(conn, "shifting_movements", COLUMNS, rows)

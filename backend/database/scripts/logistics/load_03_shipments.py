"""
load_03_shipments.py — loads the 'Shipment Master Database' sheet into:

  1. export_shipments      (one row per shipment; cost columns merged in)
  2. shipment_containers   (LONG format, melted from the 8 container columns)

Computed columns (Transit Time, Days Delayed, QFL Stayed Time, Freight
Variance, Total Shipping Cost, Total Logistics Cost, Cost Per KG) are NOT
loaded — recreated by the SQL view v_shipment_metrics.
'Packing Cost' is skipped here: packing costs live in packing_details.

Rows whose (Exp #, B. #) key doesn't match exports load anyway with
export_id = NULL and the raw key preserved for later fixing.

Run AFTER load_01_exports.py.
Usage:  python load_03_shipments.py
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

# Container column -> (size, type)
CONTAINER_MAP = {
    "Standard Containers 20'":     (20, "Standard"),
    "Open Top Containers 20'":     (20, "Open Top"),
    "Flat Rack Containers 20'":    (20, "Flat Rack"),
    "Out of Gauge Containers 20'": (20, "Out of Gauge"),
    "Standard Containers 40'":     (40, "Standard"),
    "Open Top Containers 40'":     (40, "Open Top"),
    "Flat Rack Containers 40'":    (40, "Flat Rack"),
    "Out of Gauge Containers 40'": (40, "Out of Gauge"),
}

SHIPMENT_COLUMNS = [
    "export_id", "exp_batch_raw", "shipment_stage", "shipment_status",
    "shipment_terms", "efs", "description", "job_no", "color_code", "pkgs",
    "net_weight_kgs", "gross_weight_kgs", "s_agent", "c_agent", "s_line",
    "cro_no", "lcl", "air", "qfl_arrival_date", "port_in_date",
    "cut_off_date", "etd_karachi", "cro_arrival_date", "actual_arrival_date",
    "stuffing_location", "pick_up_time", "loading_t", "v_and_v_no",
    "quoted_sea_freight", "actual_sea_freight", "lhr_khi_cost",
    "fumigation_cost", "lashing_cost", "qfl_port_cost", "qfl_cost",
    "clearance_cost", "dhl_charges", "insurance", "wharfage","country", "pod"
]


def load_export_shipments(conn):
    df = read_sheet("Shipment Master Database", EXCEL_FILE)
    export_map = load_export_map(conn)
    shipment_rows = []
    containers_per_row = []   # container tuples for each shipment, in order
    unmatched = 0
    for _, r in df.iterrows():
        key = make_export_key(r.get("Exp #"), r.get("B. #"))
        export_id = export_map.get(key)
        if export_id is None:
            unmatched += 1
        shipment_rows.append((
            export_id,
            clean_text(r.get("Primary Key")),
            clean_text(r.get("Shipment Stage")),
            clean_text(r.get("Shipment Status")),
            clean_text(r.get("Shipment Terms")),
            clean_text(r.get("EFS")),
            clean_text(r.get("Description")),
            clean_text(r.get("Job #")),
            clean_text(r.get("Color Code")),
            clean_int(r.get("Pkgs.")),
            clean_number(r.get("N.W. - Kgs")),
            clean_number(r.get("G.W. - Kgs")),
            clean_text(r.get("S/Agent")),
            clean_text(r.get("C/Agent")),
            clean_text(r.get("S/Line")),
            clean_text(r.get("CRO #")),
            clean_text(r.get("LCL")),
            clean_text(r.get("AIR")),
            clean_date(r.get("QFL Arrival Dt.")),
            clean_date(r.get("Port-In Dt.")),
            clean_date(r.get("Cut Off Dt.")),
            clean_date(r.get("ETD Karachi")),
            clean_date(r.get("CRO Arrival Dt.")),
            clean_date(r.get("Actual Arrival Dt.")),
            clean_text(r.get("Stuffing Location")),
            clean_text(r.get("Pick-up T.")),
            clean_text(r.get("Loading T.")),
            clean_text(r.get("V. & V. #")),
            clean_number(r.get("Quoted S/freight")),
            clean_number(r.get("Actual S/freight")),
            clean_number(r.get("LHR ~ KHI")),
            clean_number(r.get("Fumigation")),
            clean_number(r.get("Lashing")),
            clean_number(r.get("QFL ~ Port")),
            clean_number(r.get("QFL Cost")),
            clean_number(r.get("Clearance Cost")),
            clean_number(r.get("DHL Charges")),
            clean_number(r.get("Insurance")),
            clean_number(r.get("Wharfage")),
            clean_text(r.get("Country")),
            clean_text(r.get("POD")),
        ))
        row_containers = []
        for col, (size, ctype) in CONTAINER_MAP.items():
            qty = clean_int(r.get(col))
            if qty and qty > 0:
                row_containers.append((size, ctype, qty))
        containers_per_row.append(row_containers)
    if unmatched:
        print(f"NOTE: {unmatched} shipments have no matching export key "
              f"(loaded with export_id = NULL, raw key preserved)")
    # Insert shipments one-by-one so we get shipment_ids back for containers
    container_rows = []
    with conn.cursor() as cur:
        for values, row_containers in zip(shipment_rows, containers_per_row):
            cur.execute(
                f"INSERT INTO export_shipments ({', '.join(SHIPMENT_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(SHIPMENT_COLUMNS))}) "
                f"RETURNING shipment_id",
                values,
            )
            shipment_id = cur.fetchone()[0]
            for size, ctype, qty in row_containers:
                container_rows.append((shipment_id, size, ctype, qty))
    conn.commit()
    print(f"  export_shipments: inserted {len(shipment_rows)} rows")
    bulk_insert(
        conn, "shipment_containers",
        ["shipment_id", "size", "type", "qty"],
        container_rows,
    )
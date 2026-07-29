"""Load the Purchase order table which is the helper
   Table and creates a link between imports
   and purchases"""

import pandas as pd
from database.scripts.etl_common import (
    read_sheet, clean_text, clean_date
)
from pathlib import Path
current_dir = Path(__file__).resolve().parent
directory = Path(current_dir.parents[2] / "data" / "purchases")

files = list(directory.iterdir())

EXCEL_FILE = files[0]

#Order of columns matters here (must be same as order of columns in sheet)
PURCHASES_COLUMNS = [
   "po_number",
   "po_date"
]

def load_purchase_orders(conn):
    df = read_sheet("Sheet1", EXCEL_FILE)
    purchase_order_rows = []
    po_number_history = []

    with conn.cursor() as cur:
            cur.execute(
                "SELECT po_number from purchase_order" #--> getting already existing purchase orders
            )
            po_number_history = [row[0] for row in cur.fetchall()]

    for _, row in df.iterrows():
        if(clean_text(row.get("PO Numbe")) not in po_number_history):
            po_number_history.append(clean_text(row.get("PO Numbe")))
            purchase_order_rows.append((
                clean_text(row.get("PO Numbe")),
                clean_date(row.get("PO Date")),
            ))

    with conn.cursor() as cur:
        for row in purchase_order_rows:
            cur.execute(
                f"INSERT INTO purchase_order ({', '.join(PURCHASES_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(PURCHASES_COLUMNS))}) "
                f"RETURNING po_number",
                row
            )

    conn.commit()
    print(f"Purchases : inserted {len(purchase_order_rows)} rows")

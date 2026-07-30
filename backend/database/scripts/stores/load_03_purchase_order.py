"""Load the Purchase order table which is the helper
   Table and creates a link between imports
   and purchases"""

from backend.database.scripts.etl_common import (
    clean_text, clean_date, pick, data_files, read_first_sheet,
)
# Every workbook in data/purchases/ is read (not just one) — the 2023-2026
# export sits alongside the older report, and both carry PO numbers.
EXCEL_FILES = data_files("purchases")

#Order of columns matters here (must be same as order of columns in sheet)
PURCHASES_COLUMNS = [
   "po_number",
   "po_date"
]

def load_purchase_orders(conn):
    purchase_order_rows = []

    with conn.cursor() as cur:
            cur.execute(
                "SELECT po_number from purchase_order" #--> getting already existing purchase orders
            )
            po_number_history = {row[0] for row in cur.fetchall()}

    for f in EXCEL_FILES:
        df, sheet = read_first_sheet(f)
        print(f"  {f.name} [{sheet}]: {len(df)} rows")
        for _, row in df.iterrows():
            po_number = clean_text(pick(row, "PO Numbe", "PO Number"))
            if po_number is not None and po_number not in po_number_history:
                po_number_history.add(po_number)
                purchase_order_rows.append((
                    po_number,
                    clean_date(pick(row, "PO Date")),
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
    print(f"Purchase orders : inserted {len(purchase_order_rows)} rows")

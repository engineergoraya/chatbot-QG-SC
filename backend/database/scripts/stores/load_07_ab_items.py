"""Load AB items table"""

import pandas as pd
from database.scripts.etl_common import (
    read_sheet, clean_text, clean_date, clean_int
)
from pathlib import Path
current_dir = Path(__file__).resolve().parent
directory = Path(current_dir.parents[2] / "data" / "ab_items")

files = list(directory.iterdir())

EXCEL_FILE = files[0]

#Order of columns matters here (must be same as order of ROWS list)
AB_ITEMS_COLUMNS = ["item_code", "branch_name", "rank", "safety_days",  "lead_time_days"]

#--> Order must be same as columns order
AB_ITEMS_HEADERS = [
    ("Item Code", clean_text),	("Branch Name", clean_text), ("Rank", clean_text), ("Safety Days", clean_int), ("Lead Time Days", clean_int)
]

def load_ab_items(conn):
    df = read_sheet("Main", EXCEL_FILE)
    ab_items_rows = []

    for _, row in df.iterrows():
        row_tuple = ()
        for header, cleaning_function in AB_ITEMS_HEADERS:
            row_tuple = row_tuple + (cleaning_function(row.get(header)), )
        ab_items_rows.append(row_tuple)
    
    with conn.cursor() as cur:
        for row in ab_items_rows:
            cur.execute(
                f"INSERT INTO ab_items ({', '.join(AB_ITEMS_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(AB_ITEMS_COLUMNS))}) "
                f"RETURNING item_code",
                row
            )

    conn.commit()
    print(f"AB Items : inserted {len(ab_items_rows)} rows")

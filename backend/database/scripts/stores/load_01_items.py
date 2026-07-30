"""Load the Main Items table"""

from backend.database.scripts.etl_common import (
    read_sheet, clean_text, bulk_insert,
    data_files,
)
import pandas as pd
EXCEL_FILES = data_files("items_database")

#Order of columns matters here (must be same as order of ROWS list)
ITEM_COLUMNS = ["item_code", "item", "specs", "uom", "item_category"]

#--> Order must be same as columns order
ITEM_HEADERS = [
    ("ItemCode", clean_text),	("Item", clean_text), ("Specification", clean_text), ("Unit", clean_text),	("Item Sub Group", clean_text) 
]

def load_items(conn):
    dataframes = []
    item_rows = []
    
    for file in EXCEL_FILES:
        dataframes.append(read_sheet("Sheet1", file))
    
    df = pd.concat(dataframes, ignore_index=True)
    df = df.drop_duplicates(subset=["ItemCode"], keep="first")

    for _, row in df.iterrows():
        row_tuple = ()
        for header, cleaning_function in ITEM_HEADERS:
            row_tuple = row_tuple + (cleaning_function(row.get(header)), )
        item_rows.append(row_tuple)
    
    bulk_insert(conn, "items", ITEM_COLUMNS, item_rows)
    print(f"Items : inserted {len(item_rows)} rows")
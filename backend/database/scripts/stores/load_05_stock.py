"""Load the Main Stocks table - resilient (see etl_common.resilient_load)."""


from backend.database.scripts.etl_common import (
    clean_text, clean_number, read_first_sheet, resilient_load,
    data_files,
)

STOCK_COLUMNS = [
    "item_code", "branch", "hold_qty", "stock_qty", "stock_qty_amount",
    "available_qty", "available_amount",
]

STOCK_HEADERS = [
    ("ItemCode", clean_text), ("Branch", clean_text), ("Hold Qty", clean_number),
    ("StockQty", clean_number), ("Stock Qty Amou", clean_number),
    ("Available Qty", clean_number), ("Available Amoun", clean_number),
]

_ITEM_CODE_IDX = STOCK_COLUMNS.index("item_code")


def load_stock(conn):
    files = data_files("stocks")
    print(f"Reading stock files from {files[0].parent} ...")

    rows = []
    for f in files:
        try:
            df, sheet = read_first_sheet(f)
        except Exception as exc:
            print(f"  SKIP {f.name}: {exc}")
            continue
        before = len(rows)
        for _, row in df.iterrows():
            rows.append(tuple(fn(row.get(h)) for h, fn in STOCK_HEADERS))
        print(f"  {f.name} [{sheet}]: {len(rows) - before} rows")

    if not rows:
        print("Stock: nothing to load")
        return

    resilient_load(
        conn, "stock", STOCK_COLUMNS, rows, truncate=True,
        fk_stubs=[(_ITEM_CODE_IDX, "items", "item_code")],
    )


if __name__ == "__main__":
    from backend.database.connection.database_connection import connection

    load_stock(connection)

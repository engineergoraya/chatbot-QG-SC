"""Load the Store Requisitions table - resilient (see etl_common.resilient_load)."""


from backend.database.scripts.etl_common import (
    clean_text, clean_number, clean_date, read_first_sheet, resilient_load,
    data_files,
)

STORE_REQUISITION_COLUMNS = [
    "item_code", "ref_no", "department", "branch", "prepare_date", "description",
    "required_by", "req_quantity", "pur_quantity", "pending_quantity",
    "last_purchase", "previous_price", "required_date", "status", "sourced_by",
    "previous_supplier", "original_required_date", "stock_in_date",
]

STORE_REQUISITION_HEADERS = [
    ("Item Code", clean_text), ("Ref #", clean_text), ("Department", clean_text),
    ("Branch", clean_text), ("Prepare Date", clean_date), ("Description", clean_text),
    ("RequiredBy", clean_text), ("Req.Quantity", clean_number),
    ("Pur.Quantity", clean_number), ("Pending Quantity", clean_number),
    ("LastPurchase", clean_date), ("PreviousPrice", clean_text),
    ("RequiredDate", clean_date), ("Status", clean_text), ("SourcedBy", clean_text),
    ("PreviousSupplier", clean_text), ("Original Required", clean_date),
    ("Stock In Dat", clean_date),
]

_ITEM_CODE_IDX = STORE_REQUISITION_COLUMNS.index("item_code")


def load_store_requisitions(conn):
    files = data_files("store_requisitions")
    print(f"Reading store-requisition files from {files[0].parent} ...")

    rows = []
    for f in files:
        try:
            df, sheet = read_first_sheet(f)
        except Exception as exc:
            print(f"  SKIP {f.name}: {exc}")
            continue
        before = len(rows)
        for _, row in df.iterrows():
            rows.append(tuple(fn(row.get(h)) for h, fn in STORE_REQUISITION_HEADERS))
        print(f"  {f.name} [{sheet}]: {len(rows) - before} rows")

    if not rows:
        print("Store Requisitions: nothing to load")
        return

    resilient_load(
        conn, "store_requisition", STORE_REQUISITION_COLUMNS, rows, truncate=True,
        fk_stubs=[(_ITEM_CODE_IDX, "items", "item_code")],
    )


if __name__ == "__main__":
    from backend.database.connection.database_connection import connection

    load_store_requisitions(connection)

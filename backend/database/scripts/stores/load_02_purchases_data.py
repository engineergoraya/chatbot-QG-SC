"""Load the Main Purchases table - resilient (see etl_common.resilient_load)."""

from pathlib import Path

from database.scripts.etl_common import (
    clean_text, clean_int, clean_date, clean_number,
    read_first_sheet, pick, resilient_load,
)

PURCHASES_COLUMNS = [
    "ref_no", "qty", "branch", "amount", "ppc_store", "required_d", "purchase",
    "mop", "dc_no", "bill_no", "sourcing_o", "item_code", "supplier", "po_number",
]

# (target column, candidate source headers, cleaner). Handles header variants
# across the old and the new 2023-2026 export (e.g. PPC vs PPC/Store).
PURCHASES_FIELDS = [
    ("Ref No",), ("Qty",), ("Branch",), ("Amount",), ("PPC/Store", "PPC"),
    ("Required D",), ("Purchase",), ("MOP",), ("DC No",), ("Bill No",),
    ("Sourcing O",), ("Item Code",), ("Supplier",), ("PO Numbe", "PO Number"),
]
PURCHASES_CLEANERS = [
    clean_text, clean_int, clean_text, clean_number, clean_date, clean_date,
    clean_date, clean_text, clean_text, clean_text, clean_text, clean_text,
    clean_text, clean_text,
]

_ITEM_CODE_IDX = PURCHASES_COLUMNS.index("item_code")
_PO_NUMBER_IDX = PURCHASES_COLUMNS.index("po_number")


def load_purchases(conn):
    directory = Path(__file__).resolve().parents[3] / "data" / "purchases"
    files = sorted(p for p in directory.iterdir() if p.suffix.lower() in (".xls", ".xlsx"))
    print(f"Reading purchases files from {directory} ...")

    rows = []
    for f in files:
        try:
            df, sheet = read_first_sheet(f)
        except Exception as exc:
            print(f"  SKIP {f.name}: {exc}")
            continue
        before = len(rows)
        for _, row in df.iterrows():
            rows.append(tuple(
                clean(pick(row, *headers))
                for headers, clean in zip(PURCHASES_FIELDS, PURCHASES_CLEANERS)
            ))
        print(f"  {f.name} [{sheet}]: {len(rows) - before} rows")

    if not rows:
        print("Purchases: nothing to load")
        return

    resilient_load(
        conn, "purchases_data", PURCHASES_COLUMNS, rows, truncate=True,
        fk_stubs=[
            (_ITEM_CODE_IDX, "items", "item_code"),
            (_PO_NUMBER_IDX, "purchase_order", "po_number"),
        ],
    )


if __name__ == "__main__":
    from database.connection.database_connection import connection

    load_purchases(connection)

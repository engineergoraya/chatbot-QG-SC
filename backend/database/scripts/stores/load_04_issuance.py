"""
Load the Main Issuances table - resilient loader.

Why this was rewritten: the old version inserted every row in ONE transaction
and only committed at the end, so a SINGLE bad row (most often an item_code that
is not in the items master - issuance.item_code has a FOREIGN KEY to items)
aborted the whole transaction and left the table EMPTY. With three years of new
files that referenced many new item codes, nothing loaded at all.

This version:
  * reads each workbook's sheet defensively (falls back to the first sheet if
    'Sheet1' is absent) and warns when a file's columns do not look like an
    issuance export;
  * TRUNCATEs issuance first so a re-run is a clean reload, not a duplicate;
  * satisfies the item_code foreign key by inserting STUB rows into items for any
    code not already there - so every issuance row loads and keeps its real
    item_code (the item name simply joins as NULL until that item is catalogued);
  * bulk-inserts with execute_values (fast), and if a batch still fails, falls
    back to row-by-row with savepoints so one bad row is skipped, not fatal.

Run directly:  python -m database.scripts.stores.load_04_issuance
"""

from psycopg2.extras import execute_values

from backend.database.scripts.etl_common import (
    clean_text, clean_int, clean_date, clean_number, data_files, read_first_sheet,
)

# Order of columns must match the order of ISSUANCE_HEADERS below.
ISSUANCE_COLUMNS = [
    "issuance_code", "item_code", "department", "branch", "issue_to_others",
    "authorized_by", "issued_by", "received_by", "description", "ref_no",
    "demand_ref_no", "quantity", "status", "from_date", "unit_price",
    "total_price", "job_number",
]

ISSUANCE_HEADERS = [
    ("IssuanceCode", clean_text), ("ItemCode", clean_text), ("Department", clean_text),
    ("Branch", clean_text), ("IssueToOthers", clean_text), ("AuthorizedBy", clean_text),
    ("IssuedBy", clean_text), ("ReceivedBy", clean_text), ("Description", clean_text),
    ("RefNo", clean_text), ("Demand RefN", clean_text), ("Quantity", clean_int),
    ("Status", clean_text), ("FromDate", clean_date), ("UnitPrice", clean_number),
    ("TotalPric", clean_number), ("JobNumber", clean_text),
]

_ITEM_CODE_IDX = ISSUANCE_COLUMNS.index("item_code")
# Headers that, if all missing, mean the file is not an issuance export.
_KEY_HEADERS = {"ItemCode", "Quantity", "FromDate"}


def _parse_files(files):
    rows = []
    for f in files:
        try:
            df, sheet = read_first_sheet(f)
        except Exception as exc:
            print(f"  SKIP {f.name}: could not read ({exc})")
            continue

        present = {h for h, _ in ISSUANCE_HEADERS if h in df.columns}
        if not (_KEY_HEADERS & present):
            print(
                f"  SKIP {f.name} [{sheet}]: columns do not look like an issuance "
                f"export (found {list(df.columns)[:6]}...)"
            )
            continue

        before = len(rows)
        for _, row in df.iterrows():
            rows.append(tuple(fn(row.get(header)) for header, fn in ISSUANCE_HEADERS))
        print(f"  {f.name} [{sheet}]: {len(rows) - before} rows")

    # Files overlap (older extracts are subsets of the newer per-branch ones),
    # so drop rows that are identical across every column - a true duplicate,
    # not extra data. dict.fromkeys preserves order and dedups on the tuple.
    deduped = list(dict.fromkeys(rows))
    if len(deduped) < len(rows):
        print(f"  removed {len(rows) - len(deduped)} exact-duplicate row(s) across files")
    return deduped


def _ensure_item_codes(cur, rows):
    """Insert stub items rows for any item_code not already in the master."""
    cur.execute("SELECT item_code FROM items")
    existing = {r[0] for r in cur.fetchall()}
    missing = sorted(
        {r[_ITEM_CODE_IDX] for r in rows if r[_ITEM_CODE_IDX] and r[_ITEM_CODE_IDX] not in existing}
    )
    if missing:
        execute_values(
            cur,
            "INSERT INTO items (item_code) VALUES %s ON CONFLICT (item_code) DO NOTHING",
            [(code,) for code in missing],
            page_size=1000,
        )
        print(f"  added {len(missing)} stub item_code(s) to items (kept issuance FK valid)")


def _insert_rows(conn, cur, rows):
    """Bulk insert; fall back to per-row (skipping bad rows) if a batch fails."""
    cols = ", ".join(ISSUANCE_COLUMNS)
    placeholders = "VALUES %s"
    try:
        execute_values(cur, f"INSERT INTO issuance ({cols}) {placeholders}", rows, page_size=1000)
        conn.commit()
        print(f"Issuances: inserted {len(rows)} rows")
        return
    except Exception as exc:
        conn.rollback()
        print(f"  bulk insert failed ({str(exc)[:120]}); retrying row-by-row, skipping bad rows...")

    # Re-do the prerequisites the rollback undid, then insert with savepoints.
    _ensure_item_codes(cur, rows)
    conn.commit()

    single = (
        f"INSERT INTO issuance ({cols}) VALUES ({', '.join(['%s'] * len(ISSUANCE_COLUMNS))})"
    )
    good = bad = 0
    for r in rows:
        try:
            cur.execute("SAVEPOINT sp")
            cur.execute(single, r)
            cur.execute("RELEASE SAVEPOINT sp")
            good += 1
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT sp")
            bad += 1
    conn.commit()
    print(f"Issuances: inserted {good} rows, skipped {bad} bad row(s)")


def load_issuances(conn):
    files = data_files("issuances")
    print(f"Reading issuance files from {files[0].parent} ...")
    rows = _parse_files(files)
    if not rows:
        print("Issuances: nothing to load")
        return

    print(f"Parsed {len(rows)} issuance rows total. Loading ...")
    with conn.cursor() as cur:
        cur.execute("TRUNCATE issuance RESTART IDENTITY")
        _ensure_item_codes(cur, rows)
        conn.commit()  # keep truncate + stub items even if the insert needs a retry
        _insert_rows(conn, cur, rows)


if __name__ == "__main__":
    from backend.database.connection.database_connection import connection

    load_issuances(connection)

"""
etl_common.py — shared utilities for the logistics ETL scripts.

All sheet scripts import from this module so that key cleaning, value
cleaning, and database access behave identically everywhere.

Connection settings come from environment variables (with defaults):
    PGHOST (localhost), PGPORT (5432), PGDATABASE (logistics),
    PGUSER (postgres), PGPASSWORD ()

Requires:  pip install pandas openpyxl psycopg2-binary
"""

import re
import pandas as pd
from psycopg2.extras import execute_values

# Values that mean "no data" in the workbook
PLACEHOLDERS = {"-", "--", "---", "", "n/a", "na", "nil", "none", "tbd"}


# ---------------------------------------------------------------------------
# Key cleaning
# ---------------------------------------------------------------------------

_ORDINAL = re.compile(r"(?<=\d)(st|nd|rd|th)\b", re.IGNORECASE)

def clean_key(value) -> str | None:
    """Normalize an Exp # / Batch # value.
    '2360th' -> '2360' ; ' 25-018TH ' -> '25-018' ; '-' -> None
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if s.lower() in PLACEHOLDERS:
        return None
    s = _ORDINAL.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s.upper() if s else None


def make_export_key(exp_no, batch_no):
    """(exp_no, batch_no) tuple used to look up export_id. batch '' if none."""
    e = clean_key(exp_no)
    b = clean_key(batch_no) or ""
    return (e, b) if e else None


# ---------------------------------------------------------------------------
# Value cleaning
# ---------------------------------------------------------------------------

def clean_text(value):
    """Trim text; placeholders -> None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    return None if s.lower() in PLACEHOLDERS else s


def clean_status(value):
    """Like clean_text but keeps 'Pending' (a real status value)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if s.lower() in PLACEHOLDERS:
        return None
    return s


def clean_number(value):
    """Extract a numeric value. '1 Nos.' -> 1.0 ; 'Rs. 12,500' -> 12500 ; '-' -> None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").strip()
    if s.lower() in PLACEHOLDERS:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def clean_int(value):
    n = clean_number(value)
    return int(n) if n is not None else None


def clean_date(value):
    """Coerce Excel datetimes / date strings to date; anything else -> None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        if value.strip().lower() in PLACEHOLDERS:
            return None

        # Remove any HTML that follows the date
        value = value.split("<", 1)[0].strip()

    ts = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return None if pd.isna(ts) else ts.date()


def parse_qty_uom(value):
    """'1 Nos.' -> (1.0, 'Nos.') ; 5 -> (5.0, None)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    s = str(value).strip()
    if s.lower() in PLACEHOLDERS:
        return None, None
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*(.*)", s)
    if not m:
        return None, s or None
    qty = float(m.group(1))
    uom = m.group(2).strip() or None
    return qty, uom


def load_export_map(conn) -> dict:
    """Return {(exp_no, batch_no): export_id} for FK resolution."""
    with conn.cursor() as cur:
        cur.execute("SELECT exp_no, batch_no, export_id FROM exports")
        return {(e, b): i for e, b, i in cur.fetchall()}


def bulk_insert(conn, table, columns, rows, conflict_clause=""):
    """Insert many rows with execute_values. rows = list of tuples."""
    if not rows:
        print(f"  {table}: nothing to insert")
        return
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s {conflict_clause}"
    )
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=500)
    conn.commit()
    print(f"  {table}: inserted {len(rows)} rows")


def read_sheet(sheet_name, file_path) -> pd.DataFrame:
    """Read one worksheet with the header on the first row."""
    df = pd.read_excel(file_path, sheet_name=sheet_name)
    df.columns = df.columns.str.strip()
    df.columns = df.columns.str.replace("\n", " ", regex=False)
    df.columns = df.columns.str.replace(r"\s+", " ", regex=True)
    df = df.dropna(how="all")
    print(f"Read '{sheet_name}': {len(df)} rows, {len(df.columns)} columns")
    return df


def read_first_sheet(file_path, prefer="Sheet1") -> pd.DataFrame:
    """
    Read a workbook's sheet defensively: `prefer` if present, else the first.

    Cleans column names the same way as read_sheet. Used by the resilient
    loaders so a file whose sheet is not literally named 'Sheet1' still loads.
    """
    xl = pd.ExcelFile(file_path)
    sheet = prefer if prefer in xl.sheet_names else xl.sheet_names[0]
    df = xl.parse(sheet)
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.replace("\n", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True)
    )
    return df.dropna(how="all"), sheet


def pick(row, *candidates):
    """First present, non-empty candidate column value in a DataFrame row."""
    for name in candidates:
        if name in row.index:
            val = row.get(name)
            if not (isinstance(val, float) and pd.isna(val)):
                return val
    return row.get(candidates[0])


def ensure_fk_stubs(cur, ref_table, ref_column, values) -> int:
    """
    Insert STUB rows into ref_table for any value not already present.

    This is what stops a foreign key from aborting a whole load: if an
    issuance/purchase references an item_code (or po_number) not yet in the
    master, we add a minimal stub row so the FK passes and NO data row is lost.
    Returns how many stubs were added.
    """
    vals = {v for v in values if v is not None}
    if not vals:
        return 0
    cur.execute(f"SELECT {ref_column} FROM {ref_table}")
    existing = {r[0] for r in cur.fetchall()}
    missing = sorted(vals - existing)
    if missing:
        execute_values(
            cur,
            f"INSERT INTO {ref_table} ({ref_column}) VALUES %s "
            f"ON CONFLICT ({ref_column}) DO NOTHING",
            [(m,) for m in missing],
            page_size=1000,
        )
    return len(missing)


def resilient_load(conn, table, columns, rows, truncate=True, fk_stubs=None):
    """
    Load rows into `table` without the single-bad-row-kills-everything failure.

      * de-duplicates exact-duplicate rows (overlapping source files),
      * TRUNCATEs first for a clean reload (leaf tables only - pass truncate=False
        for a table other rows reference),
      * `fk_stubs` = list of (row_index, ref_table, ref_column): stubs those
        references so FKs never abort the load,
      * bulk-inserts fast, and if a batch still fails, retries row-by-row with
        savepoints so only the genuinely bad rows are skipped.

    Returns (inserted, skipped).
    """
    rows = list(dict.fromkeys(rows))  # drop exact-duplicate tuples
    fk_stubs = fk_stubs or []
    cols = ", ".join(columns)

    def _stub_all(cur):
        for idx, ref_table, ref_col in fk_stubs:
            n = ensure_fk_stubs(cur, ref_table, ref_col, {r[idx] for r in rows})
            if n:
                print(f"  stubbed {n} missing {ref_table}.{ref_col}")

    with conn.cursor() as cur:
        if truncate:
            cur.execute(f"TRUNCATE {table} RESTART IDENTITY")
        _stub_all(cur)
        conn.commit()

        try:
            execute_values(cur, f"INSERT INTO {table} ({cols}) VALUES %s", rows, page_size=1000)
            conn.commit()
            print(f"  {table}: inserted {len(rows)} rows")
            return len(rows), 0
        except Exception as exc:
            conn.rollback()
            print(f"  {table}: bulk insert failed ({str(exc)[:100]}); row-by-row, skipping bad rows...")

        _stub_all(cur)
        conn.commit()
        single = f"INSERT INTO {table} ({cols}) VALUES ({', '.join(['%s'] * len(columns))})"
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
        print(f"  {table}: inserted {good} rows, skipped {bad} bad row(s)")
        return good, bad

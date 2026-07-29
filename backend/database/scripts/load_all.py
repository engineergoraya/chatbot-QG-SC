from database.scripts.logistics.load_01_exports import load_exports
from database.scripts.logistics.load_02_export_documentation import load_export_documenttion
from database.scripts.logistics.load_03_shipments import load_export_shipments
from database.scripts.logistics.load_04_packing import load_packing
from database.scripts.logistics.load_05_shifting import load_shifting
from database.scripts.stores.load_01_items import load_items
from database.scripts.stores.load_03_purchase_order import load_purchase_orders
from database.scripts.stores.load_02_purchases_data import load_purchases
from database.scripts.stores.load_04_issuance import load_issuances
from database.scripts.stores.load_06_store_requisitions import load_store_requisitions
from database.scripts.stores.load_05_stock import load_stock
from database.scripts.stores.load_07_ab_items import load_ab_items
from database.schemas.logistics_schemas import logistics_schemas_queries
from database.schemas.logistics_views import logistics_views_queries
from database.schemas.imports_schemas import imports_schemas_queries
from database.schemas.stores_schemas import stores_schemas_queries
from database.schemas.create_schemas import execute_queries
from database.connection.database_connection import cursor

"""
load_all.py — one-shot loader for the whole database (logistics + imports).

It performs a CLEAN reload: the transaction tables are truncated first, then
repopulated from the source workbooks in the `Project Files/` folder. The
master tables (items / suppliers / purchase_order) are upserted idempotently,
so they are not truncated.

Source paths are resolved here (from `Project Files/`) and injected into the
loader modules, so the modules keep their own placeholder paths untouched.

Usage:  python -m database.scripts.load_all
"""

from pathlib import Path

# Loader modules (imported as modules so we can point them at the real files).
import database.scripts.etl_common as etl_common
import database.scripts.etl_stores_imports as etl_si
import database.scripts.load_06_import_masters as load_06

# Imports module
from database.scripts.load_06_import_masters import load_import_masters
from database.scripts.load_07_import_details import load_import_details
from database.scripts.load_08_import_items import load_import_items
from database.scripts.load_09_shipment_details import load_shipment_details
from database.scripts.load_10_payment_history import load_payment_history
from database.connection.database_connection import connection
from pathlib import Path

# ---------------------------------------------------------------------------
# Deleting old data
# ---------------------------------------------------------------------------
print("Deleting old data...\n")

cursor.execute('DROP TABLE IF EXISTS export_documents,export_shipments,exports,import_details,import_item,issuance,items,suppliers,store_requisition,stock,shipment_details,shipment_containers,shifting_movements,purchase_order,payment_history,packing_details, purchases_data, ab_items CASCADE;')

connection.commit()

print("Old data deleted succcessfully...\n")
# ---------------------------------------------------------------------------
# Creating schemas
# ---------------------------------------------------------------------------

execute_queries(logistics_schemas_queries, "Logistics", "schemas")

execute_queries(logistics_views_queries, "Logistics", "views")
# imports MUST run before stores — it creates the shared masters (items, suppliers, purchase_order)
execute_queries(imports_schemas_queries, "Imports", "schemas")
execute_queries(stores_schemas_queries, "Stores", "schemas")

# ---------------------------------------------------------------------------
# Point every loader at the real source workbooks, in THIS project's own
# data/ folder - never a sibling project directory. A hardcoded path outside
# the project breaks the load entirely on any other machine/checkout, and
# silently loads stale data if the two ever drift.
# ---------------------------------------------------------------------------

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parents[1]
directory = project_root / "data" / "logistics"

files = list(directory.iterdir())
LOGISTICS_FILE = files[0]
etl_common.EXCEL_FILE = LOGISTICS_FILE

directory = project_root / "data" / "imports"
files = list(directory.iterdir())
IMPORTS_FILE = files[0]

_import_xlsx = str(IMPORTS_FILE)
etl_si.IMPORT_FILE = _import_xlsx      # read_import_rows() reads this global
load_06.IMPORT_FILE = _import_xlsx     # load_06 reads its own module global


def truncate_transaction_tables():
    """Clean slate for a repeatable full load. CASCADE clears the child tables;
    masters are left intact (they are upserted idempotently)."""
    print("Truncating transaction tables for a clean reload....")
    with connection.cursor() as cur:
        cur.execute("TRUNCATE exports CASCADE")          # logistics + its children
        cur.execute("TRUNCATE import_details CASCADE")   # imports + its children
    connection.commit()


def load_data(table_name, load_function):
    print("Populating " + table_name + "....")
    try:
        load_function(connection)
        print(table_name + " populated successfully....")
    except Exception as exc:
        connection.rollback()
        print(f"!! {table_name} FAILED — {type(exc).__name__}: {exc}")


#--> Better not to change loading order

truncate_transaction_tables()

# --- Logistics ---
load_data("Exports", load_exports)
load_data("Exports Documentation", load_export_documenttion)
load_data("Shipments from logistics", load_export_shipments)
load_data("Packing", load_packing)
load_data("Shifting", load_shifting)
load_data("Items", load_items)
load_data("Purchase Orders", load_purchase_orders)
load_data("Purchases", load_purchases)
load_data("Issuances", load_issuances)
load_data("Stocks", load_stock)
load_data("Store Requisitions", load_store_requisitions)
load_data("AB Items", load_ab_items)

# --- Imports (masters first: items / suppliers / purchase_order) ---
load_data("Import Masters", load_import_masters)
load_data("Import Details", load_import_details)
load_data("Import Items", load_import_items)
load_data("Shipment Details", load_shipment_details)
load_data("Payment History", load_payment_history)

print("\nAll load steps complete.")

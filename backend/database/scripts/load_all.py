"""
load_all.py — one-shot loader for the whole database (logistics + imports).

It performs a CLEAN reload: the transaction tables are truncated first, then
repopulated from the source workbooks under `backend/database/data/`. The
master tables (items / purchase_order) are upserted idempotently,
so they are not truncated.

Source workbooks are located by etl_common.data_file() / data_files(), which
each loader calls for itself — nothing is injected from here any more.

Usage (from anywhere):  python backend/database/scripts/load_all.py
        or, from the repo root:  python -m backend.database.scripts.load_all
"""

# --- import bootstrap -------------------------------------------------------
# The loaders import as `backend.database.*` (repo root on sys.path) while
# database_connection imports `app.config` (backend/ on sys.path). Put BOTH on
# the path so this runs as a plain script or with -m, from any working
# directory, without the caller having to set PYTHONPATH.
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]          # .../backend
for _root in (_BACKEND_DIR, _BACKEND_DIR.parent):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from backend.database.scripts.logistics.load_01_exports import load_exports
from backend.database.scripts.logistics.load_02_export_documentation import load_export_documenttion
from backend.database.scripts.logistics.load_03_shipments import load_export_shipments
from backend.database.scripts.logistics.load_04_packing import load_packing
from backend.database.scripts.logistics.load_05_shifting import load_shifting
from backend.database.scripts.stores.load_01_items import load_items
from backend.database.scripts.stores.load_03_purchase_order import load_purchase_orders
from backend.database.scripts.stores.load_02_purchases_data import load_purchases
from backend.database.scripts.stores.load_04_issuance import load_issuances
from backend.database.scripts.stores.load_06_store_requisitions import load_store_requisitions
from backend.database.scripts.stores.load_05_stock import load_stock
from backend.database.scripts.stores.load_07_ab_items import load_ab_items
from backend.database.schemas.logistics_schemas import logistics_schemas_queries
from backend.database.schemas.logistics_views import logistics_views_queries
from backend.database.schemas.imports_schemas import imports_schemas_queries
from backend.database.schemas.stores_schemas import stores_schemas_queries
from backend.database.schemas.create_schemas import execute_queries
from backend.database.connection.database_connection import cursor

import backend.database.scripts.etl_common as etl_common

# Imports module
from backend.database.scripts.load_06_import_masters import load_import_masters
from backend.database.scripts.load_07_import_details import load_import_details
from backend.database.scripts.load_08_import_items import load_import_items
from backend.database.scripts.load_09_shipment_details import load_shipment_details
from backend.database.scripts.load_10_payment_history import load_payment_history
from backend.database.connection.database_connection import connection

# ---------------------------------------------------------------------------
# Source workbooks. Every loader resolves its own file through
# etl_common.data_file()/data_files(), which searches this project's own
# data/ folder — never a sibling project directory. Resolved up here as well,
# so a missing workbook fails loudly BEFORE anything is dropped.
# ---------------------------------------------------------------------------

LOGISTICS_FILE = etl_common.data_file("logistics")
IMPORTS_FILE = etl_common.data_file("imports")
print(f"Logistics source: {LOGISTICS_FILE}")
print(f"Imports source:   {IMPORTS_FILE}\n")

# ---------------------------------------------------------------------------
# Deleting old data
# ---------------------------------------------------------------------------
print("Deleting old data...\n")

cursor.execute('DROP TABLE IF EXISTS export_documents,export_shipments,exports,import_details,import_item,issuance,items,store_requisition,stock,shipment_details,shipment_containers,shifting_movements,purchase_order,payment_history,packing_details, purchases_data, ab_items CASCADE;')

connection.commit()

print("Old data deleted succcessfully...\n")
# ---------------------------------------------------------------------------
# Creating schemas
# ---------------------------------------------------------------------------

execute_queries(logistics_schemas_queries, "Logistics", "schemas")

execute_queries(logistics_views_queries, "Logistics", "views")
# imports MUST run before stores — it creates the shared masters (items, purchase_order)
execute_queries(imports_schemas_queries, "Imports", "schemas")
execute_queries(stores_schemas_queries, "Stores", "schemas")


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

# --- Imports (masters first: items / purchase_order) ---
load_data("Import Masters", load_import_masters)
load_data("Import Details", load_import_details)
load_data("Import Items", load_import_items)
load_data("Shipment Details", load_shipment_details)
load_data("Payment History", load_payment_history)

print("\nAll load steps complete.")

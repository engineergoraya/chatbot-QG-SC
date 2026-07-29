# Database ETL scripts

Ported from a teammate's separate repo (`Saitama-a/Supply-Chain-Chatbot-V2`) — a
fuller set of loaders than what this repo started with, covering all 7 data
domains (items, purchases, purchase orders, issuance, stock, store
requisitions, ab_items, plus the full logistics/imports tables).

These scripts populate the SAME Postgres database the chatbot reads from
(`app/config.py`'s `DATABASE_URL`) — they use the owner-capable role
(`write_dsn()`), never the chatbot's read-only role, and they are completely
separate from the chatbot's own runtime code (`app/`). Nothing here is
imported by the FastAPI app.

## 1. Provide the source workbooks

Drop your own Excel exports into `backend/data/<domain>/` (this folder is
gitignored — the workbooks themselves are never committed):

| Folder                         | Expected content                                    |
|---------------------------------|-----------------------------------------------------|
| `data/items_database/`          | Item master workbook(s) (e.g. `Item_Database.xlsx`) |
| `data/purchases/`                | Purchases report + purchase order source            |
| `data/issuances/`                 | Issuance/consumption workbook(s)                    |
| `data/stocks/`                     | Branch inventory/stock workbook(s)                  |
| `data/store_requisitions/`         | Store requisition workbook(s)                        |
| `data/ab_items/`                    | AB_Items (critical/reorder/safety-stock) workbook   |
| `data/logistics/`                    | Logistics master workbook (exports/shipments/etc.) |
| `data/imports/`                       | Import Status Sheet                                 |

Each loader reads every file in its folder (`Path.iterdir()`), so a folder
must contain at least one file before that loader runs, or it fails with a
clear `IndexError`/`FileNotFoundError` rather than silently skipping.

## 2. Run the load

```bash
cd backend
source venv/bin/activate
python -m database.scripts.load_all
```

**WARNING — this is destructive.** `load_all.py` starts with:

```sql
DROP TABLE IF EXISTS export_documents, export_shipments, exports,
  import_details, import_item, issuance, items, suppliers,
  store_requisition, stock, shipment_details, shipment_containers,
  shifting_movements, purchase_order, payment_history,
  packing_details, purchases_data, ab_items CASCADE;
```

then recreates every schema and reloads from the workbooks above. It DROPS
and REBUILDS the whole database this chatbot reads from — do not run it
against a live/shared database without a backup, and never as part of normal
chatbot operation. This was intentionally not wired into any automated
process; run it by hand, deliberately, when you actually intend a full
reload.

## 3. After loading

Nothing else needs to change — `app/db/introspect.py` reads the schema live
from Postgres on every chatbot startup, so newly-loaded tables/columns are
picked up automatically.

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

Drop your own Excel exports into `backend/database/data/<domain>/` (this
folder is gitignored — the workbooks themselves are never committed). For
backwards compatibility `backend/data/<domain>/` is also searched, and the
first location that actually contains a workbook wins.

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

Every loader resolves its folder through `etl_common.data_file()` /
`data_files()`, which returns the `.xls`/`.xlsx`/`.xlsm` files in name order
and ignores `.DS_Store` and Excel lock files (`~$...`). A folder with no
workbook raises a `FileNotFoundError` naming the folder and the paths that
were searched, rather than an anonymous `IndexError`.

## 2. Run the load

```bash
source backend/venv/bin/activate
python backend/database/scripts/load_all.py
```

`load_all.py` puts both the repo root and `backend/` on `sys.path` itself, so
it runs from any working directory and needs no `PYTHONPATH`. Running it as a
module (`python -m backend.database.scripts.load_all`, from the repo root)
works too.

**WARNING — this is destructive.** `load_all.py` starts with:

```sql
DROP TABLE IF EXISTS export_documents, export_shipments, exports,
  import_details, import_item, issuance, items,
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

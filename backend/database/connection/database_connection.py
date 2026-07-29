"""
PostgreSQL connection for the ETL / schema scripts.

Reuses app.config.write_dsn() — the SAME owner-capable DSN (DATABASE_URL) the
rest of this backend already resolves from .env — rather than reading its own
separate PG* environment variables. The ETL scripts need CREATE/INSERT/DROP,
so this deliberately does NOT use config.readonly_dsn() (the chatbot's
SELECT-only role); see app/config.py's own docstring on the two roles.

This module opens a single shared connection and cursor at import time
(`connection` and `cursor`), which the schema/loader scripts import directly —
same interface the loaders already expect.
"""

import psycopg2

from app.config import write_dsn

connection = psycopg2.connect(**write_dsn())
cursor = connection.cursor()
print("Database connected successfully (owner-capable role, for ETL/schema scripts)")

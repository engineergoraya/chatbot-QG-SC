"""
executor.py — runs a guard-approved query against the read-only connection.

Responsibilities:
  * open a connection using the SELECT-only role;
  * set a per-statement timeout so a runaway query is killed instead of
    hanging the request;
  * force a read-only transaction (a second backstop under the read-only
    role);
  * return rows as a list of dicts plus the column order.

It never runs raw model SQL — only the `safe_sql` string produced by
graph.guard.validate(). Callers must guard first.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg2
import psycopg2.extras

from app import config


@dataclass
class QueryOutcome:
    ok: bool
    columns: list[str] | None = None
    rows: list[dict] | None = None
    error: str | None = None
    row_count: int = 0
    truncated: bool = False


def run_safe_query(safe_sql: str, max_rows: int | None = None) -> QueryOutcome:
    if max_rows is None:
        max_rows = config.MAX_ROWS

    conn = None
    try:
        conn = psycopg2.connect(**config.readonly_dsn())
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SET LOCAL statement_timeout = {config.STATEMENT_TIMEOUT_MS}")
            cur.execute(safe_sql)
            fetched = cur.fetchall()
            columns = [d.name for d in cur.description] if cur.description else []
        conn.rollback()

        rows = [dict(r) for r in fetched]
        truncated = len(rows) >= max_rows
        return QueryOutcome(
            ok=True,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
        )
    except psycopg2.errors.QueryCanceled:
        return QueryOutcome(
            ok=False,
            error=(
                f"Query exceeded the {config.STATEMENT_TIMEOUT_MS} ms time limit "
                "and was cancelled."
            ),
        )
    except psycopg2.Error as e:
        msg = str(e.pgerror or e).strip().splitlines()[0] if str(e).strip() else "Database error."
        return QueryOutcome(ok=False, error=msg)
    finally:
        if conn is not None:
            conn.close()

"""
guard.py — the safety gate between generated SQL and the database.

Every query the model produces must pass `validate()` before execution. The
guard is deliberately strict and fails closed: anything it cannot prove is a
single read-only SELECT is rejected.

Checks, in order:
  1. Exactly one statement (no stacked `...; DROP ...`).
  2. The statement is a SELECT or a WITH...SELECT (CTE) — nothing else.
  3. No blocklisted keyword appears anywhere (INSERT/UPDATE/DELETE/DROP/
     ALTER/TRUNCATE/GRANT/REVOKE/CREATE/COPY/CALL/DO/MERGE, etc.), even in a
     position the parser might treat as harmless.
  4. No multiple-statement smuggling via comments or semicolons.
  5. Every table the query references exists in the introspected schema.
  6. A row LIMIT is present; if absent, one is injected. If present but larger
     than MAX_ROWS, it is capped.
  7. Any `ORDER BY ... DESC` gets an explicit NULLS LAST.

The result carries the (possibly rewritten) safe SQL to run.

This is layer two. Layer one is the read-only Postgres role (see
scripts/setup_readonly_role.sql): even a guard bypass cannot write, because
the DB connection itself lacks write permission.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlparse
from sqlparse.sql import Comment, Function, Parenthesis
from sqlparse.tokens import Keyword, DML, DDL, Comment as CommentToken

from app import config
from app.db.introspect import Schema


_BLOCKLIST = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "CREATE", "COPY", "CALL", "DO", "MERGE",
    "REINDEX", "VACUUM", "ANALYZE", "CLUSTER", "REFRESH", "LOCK",
    "SET", "RESET", "PREPARE", "EXECUTE", "DEALLOCATE",
    "COMMIT", "ROLLBACK", "SAVEPOINT", "BEGIN", "START",
    "LISTEN", "NOTIFY", "DISCARD", "SECURITY", "IMPORT",
}

_NOT_A_TABLE = {"lateral", "unnest", "generate_series", "values", "json_to_recordset"}


@dataclass
class GuardResult:
    ok: bool
    safe_sql: str | None = None
    reason: str | None = None


def _strip_comments(sql: str) -> str:
    return sqlparse.format(sql, strip_comments=True).strip()


def _statement_count(sql: str) -> int:
    parts = [s for s in sqlparse.split(sql) if s.strip()]
    return len(parts)


def _first_keyword(stmt) -> str | None:
    for tok in stmt.tokens:
        if tok.is_whitespace:
            continue
        if isinstance(tok, Comment) or tok.ttype in (CommentToken,):
            continue
        if tok.ttype in (DML, DDL, Keyword):
            return tok.value.upper()
        return tok.value.upper() if tok.value.strip() else None
    return None


def _contains_blocklisted(stmt) -> str | None:
    for tok in stmt.flatten():
        if tok.ttype in (Keyword, DML, DDL):
            word = tok.value.upper()
            if word in _BLOCKLIST:
                return word
    return None


def _collect_cte_names(sql: str) -> set[str]:
    if not re.match(r"^\s*WITH\b", sql, re.IGNORECASE):
        return set()
    names: set[str] = set()
    for cte in re.finditer(r"([a-zA-Z_]\w*)\s+AS\s*\(", sql, re.IGNORECASE):
        names.add(cte.group(1).lower())
    return names


def _mask_function_args(token) -> str:
    if isinstance(token, Function):
        parts = []
        for child in token.tokens:
            if isinstance(child, Parenthesis):
                parts.append(" " * len(str(child)))
            else:
                parts.append(_mask_function_args(child))
        return "".join(parts)
    if hasattr(token, "tokens") and token.tokens:
        return "".join(_mask_function_args(t) for t in token.tokens)
    return str(token)


def _collect_table_refs(sql: str) -> set[str]:
    parsed = sqlparse.parse(sql)
    scan_text = _mask_function_args(parsed[0]) if parsed else sql

    tables: set[str] = set()
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(scan_text):
        name = m.group(1)
        if "." in name:
            name = name.split(".")[-1]
        if name.lower() not in _NOT_A_TABLE:
            tables.add(name.lower())
    return tables


def _apply_limit(sql: str, max_rows: int) -> str:
    sql = sql.rstrip().rstrip(";").rstrip()
    m = re.search(r"\bLIMIT\s+(\d+)\s*$", sql, re.IGNORECASE)
    if m:
        existing = int(m.group(1))
        if existing > max_rows:
            sql = re.sub(r"\bLIMIT\s+\d+\s*$", f"LIMIT {max_rows}", sql, flags=re.IGNORECASE)
        return sql
    return f"{sql} LIMIT {max_rows}"


def _ensure_nulls_last(sql: str) -> str:
    """Append NULLS LAST to any ORDER BY ... DESC lacking an explicit NULLS
    clause. Postgres defaults DESC to NULLS FIRST, which silently ranks a
    NULL as the largest value in a "top N" ranking unless this is added."""
    return re.sub(
        r"\bDESC\b(?!\s+NULLS\s+(?:FIRST|LAST))",
        "DESC NULLS LAST",
        sql,
        flags=re.IGNORECASE,
    )


def validate(sql: str, schema: Schema, max_rows: int | None = None) -> GuardResult:
    if max_rows is None:
        max_rows = config.MAX_ROWS

    if not sql or not sql.strip():
        return GuardResult(ok=False, reason="Empty query.")

    if _statement_count(sql) != 1:
        return GuardResult(ok=False, reason="Only a single SQL statement is allowed.")

    cleaned = _strip_comments(sql)
    if not cleaned:
        return GuardResult(ok=False, reason="Query reduces to nothing after stripping comments.")

    if _statement_count(cleaned) != 1:
        return GuardResult(ok=False, reason="Multiple statements detected after removing comments.")

    parsed = sqlparse.parse(cleaned)
    if len(parsed) != 1:
        return GuardResult(ok=False, reason="Could not parse as a single statement.")
    stmt = parsed[0]

    first_kw = _first_keyword(stmt)
    if first_kw not in ("SELECT", "WITH"):
        return GuardResult(
            ok=False,
            reason=f"Only SELECT/WITH queries are allowed (got {first_kw or 'unknown'}).",
        )

    if first_kw == "WITH":
        top_level_dml = [t.value.upper() for t in stmt.tokens if t.ttype is DML]
        if any(kw != "SELECT" for kw in top_level_dml):
            return GuardResult(ok=False, reason="CTE (WITH) queries must resolve to a SELECT.")

    bad = _contains_blocklisted(stmt)
    if bad:
        return GuardResult(ok=False, reason=f"Disallowed keyword: {bad}.")

    cte_names = _collect_cte_names(cleaned)
    refs = _collect_table_refs(cleaned) - cte_names
    unknown = [t for t in refs if not schema.has_table(t)]
    if unknown:
        return GuardResult(
            ok=False,
            reason=f"Query references unknown table(s): {', '.join(sorted(unknown))}.",
        )

    safe = _apply_limit(cleaned, max_rows)
    safe = _ensure_nulls_last(safe)

    return GuardResult(ok=True, safe_sql=safe)

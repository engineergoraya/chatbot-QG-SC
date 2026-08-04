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
  5. Every table the query references exists in the introspected schema —
     EXCEPT a `FROM/JOIN <fn>(...)` call to a function on the verified
     registry (app/knowledge/functions.py), which is allowed instead when
     the call's argument count matches that function's registered arity
     exactly. Any other function name, or a registered name called with the
     wrong number of arguments, is still rejected — see
     `_collect_from_join_functions`.
  6. A row LIMIT is present; if absent, one is injected. If present but larger
     than MAX_ROWS, it is capped.
  7. Any `ORDER BY ... DESC` gets an explicit NULLS LAST.

The result carries the (possibly rewritten) safe SQL to run.

This is layer two. Layer one is the read-only Postgres role (see
scripts/setup_readonly_role.sql): even a guard bypass cannot write, because
the DB connection itself lacks write permission. The verified functions are
GRANTed to that same read-only role, are read-only themselves, and are only
ever invoked as `SELECT ... FROM fn(...)` — never CALL/procedure syntax,
which stays blocklisted like any other write-shaped keyword.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlparse
from sqlparse.sql import Comment, Function, Identifier, IdentifierList, Parenthesis
from sqlparse.tokens import Keyword, DML, DDL, Punctuation, Comment as CommentToken

from app import config
from app.db.introspect import Schema
from app.knowledge import coverage
from app.knowledge import functions as function_registry


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


def _is_join_or_from_keyword(tok) -> bool:
    if tok.ttype is not Keyword:
        return False
    value = tok.value.upper()
    return value == "FROM" or value.endswith("JOIN")


def _next_significant_token(tokens, start_idx: int):
    for tok in tokens[start_idx + 1 :]:
        if tok.is_whitespace or isinstance(tok, Comment) or tok.ttype in (CommentToken,):
            continue
        return tok
    return None


def _function_call_source(tok) -> Function | None:
    """If `tok` is a FROM/JOIN source item that is a function call (bare or
    aliased, e.g. `fn('x')` or `fn('x') AS t`), return the Function token."""
    if isinstance(tok, Function):
        return tok
    if isinstance(tok, Identifier) and tok.tokens:
        first = tok.tokens[0]
        if isinstance(first, Function):
            return first
    return None


def _function_arg_count(paren: Parenthesis) -> int:
    inner = [t for t in paren.tokens if not t.is_whitespace]
    inner = inner[1:-1] if len(inner) >= 2 else []  # drop the surrounding ( and )
    if not inner:
        return 0
    for t in inner:
        if isinstance(t, IdentifierList):
            return len(list(t.get_identifiers()))
    comma_count = sum(1 for t in inner if t.ttype is Punctuation and t.value == ",")
    return comma_count + 1


def _collect_from_join_functions(token, found: dict[str, list[int]]) -> None:
    """Recursively walk the parsed statement (any nesting depth — subqueries,
    CTEs) and record (name -> [arg_count, ...]) for every function called
    directly as a FROM/JOIN source. A function used elsewhere (e.g. COUNT(*)
    in the SELECT list, or inside a WHERE clause) is NOT collected here —
    only a genuine table-position call is a candidate for the registry
    bypass."""
    if not (hasattr(token, "tokens") and token.tokens):
        return
    toks = token.tokens
    for i, tok in enumerate(toks):
        if _is_join_or_from_keyword(tok):
            nxt = _next_significant_token(toks, i)
            fn = _function_call_source(nxt) if nxt is not None else None
            if fn is not None:
                name = (fn.get_real_name() or "").lower()
                paren = next(
                    (c for c in fn.tokens if isinstance(c, Parenthesis)), None
                )
                arg_count = _function_arg_count(paren) if paren is not None else 0
                found.setdefault(name, []).append(arg_count)
        _collect_from_join_functions(tok, found)


def _collect_table_refs(sql: str) -> tuple[set[str], dict[str, list[int]]]:
    parsed = sqlparse.parse(sql)
    stmt = parsed[0] if parsed else None

    function_calls: dict[str, list[int]] = {}
    if stmt is not None:
        _collect_from_join_functions(stmt, function_calls)

    scan_text = _mask_function_args(stmt) if stmt else sql

    tables: set[str] = set()
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(scan_text):
        name = m.group(1)
        if "." in name:
            name = name.split(".")[-1]
        lname = name.lower()
        if lname in _NOT_A_TABLE or lname in function_calls:
            continue
        tables.add(lname)
    return tables, function_calls


def _validate_function_calls(function_calls: dict[str, list[int]]) -> str | None:
    """Return an error reason if any FROM/JOIN function call is not on the
    verified registry, or is called with the wrong number of arguments;
    None if every call is approved."""
    for name, arg_counts in function_calls.items():
        fn = function_registry.get(name)
        if fn is None:
            return f"Query calls an unapproved function: {name}."
        for count in arg_counts:
            if count != fn.arg_count:
                return (
                    f"Function {name} must be called with exactly "
                    f"{fn.arg_count} argument(s); got {count}."
                )
    return None


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


def validate(
    sql: str,
    schema: Schema,
    max_rows: int | None = None,
    question: str | None = None,
) -> GuardResult:
    """Validate a generated query.

    `question` is optional and used only for the coverage-gap check (see
    app/knowledge/coverage.py): a query is rejected when it reads a domain
    that is verified to hold NO rows for the entity the question is about,
    because such a query can only return a misleading zero or — if the
    entity filter is dropped to avoid that zero — a table-wide total
    presented as the entity's. Callers that omit it keep the previous
    behaviour exactly.
    """
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
    refs, function_calls = _collect_table_refs(cleaned)
    refs -= cte_names

    fn_error = _validate_function_calls(function_calls)
    if fn_error:
        return GuardResult(ok=False, reason=fn_error)

    unknown = [t for t in refs if not schema.has_table(t)]
    if unknown:
        return GuardResult(
            ok=False,
            reason=f"Query references unknown table(s): {', '.join(sorted(unknown))}.",
        )

    if question:
        gap = coverage.find_violation(question, refs)
        if gap:
            return GuardResult(ok=False, reason=gap.explanation)

    safe = _apply_limit(cleaned, max_rows)
    safe = _ensure_nulls_last(safe)

    return GuardResult(ok=True, safe_sql=safe)

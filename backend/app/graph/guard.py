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


# --- unknown qualified columns ---------------------------------------------
#
# The guard validated TABLE names but never COLUMN names, even though
# introspect() already carries every column and Schema.has_column() was
# sitting unused. So a query naming a column that doesn't exist sailed through,
# hit the database, and came back as `column c.eta does not exist` — burning
# one of only two repair attempts on an error the guard could have named
# instantly, with the right table's real columns attached.
#
# That is not hypothetical: "how many export shipments are on water?" failed
# outright after three attempts on `c.etd`, and "which export shipments are
# delayed?" wasted its first attempt on `c.eta`. Both happen because
# `logistics_consignments` has neither column while the import table
# `consignments` has both, so the model reaches for the wrong domain's date.
#
# CONSERVATIVE BY DESIGN — it only ever rejects a reference it can prove
# wrong, so it cannot break a working query:
#   * only QUALIFIED references (`alias.column`) are examined;
#   * only aliases bound by `FROM/JOIN <real table> [AS] <alias>` are resolved
#     — CTE names, derived tables and LATERAL subquery aliases are unbound and
#     therefore skipped entirely;
#   * an alias bound to more than one table (rare, malformed) is skipped;
#   * `*` (as in `c.*`) is skipped.
_ALIAS_BINDING_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:\bAS\s+)?([A-Za-z_][A-Za-z0-9_]*)?",
    re.IGNORECASE,
)
_QUALIFIED_REF_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*|\*)")

# Words that can follow a table name without being an alias.
_NOT_AN_ALIAS = frozenset(
    {
        "on", "where", "group", "order", "having", "limit", "offset", "join",
        "inner", "left", "right", "full", "outer", "cross", "lateral", "using",
        "as", "and", "or", "union", "except", "intersect", "select", "with",
    }
)


def _alias_to_table(sql: str, schema: Schema, cte_names: set[str]) -> dict[str, str]:
    """Map each alias (and bare table name) to the real table it refers to.

    Aliases that resolve to a CTE, a derived table, or anything not present in
    the live schema are deliberately LEFT OUT — an absent alias means "don't
    check", which is what keeps this conservative.
    """
    bindings: dict[str, set[str]] = {}
    for table, alias in _ALIAS_BINDING_RE.findall(sql):
        tl = table.lower()
        if tl in cte_names or not schema.has_table(tl):
            continue
        # No known columns means "columns unknown", not "table has none" — a
        # real introspected table always has some. Skip it rather than reject
        # every reference to it (this also keeps the guard safe if
        # introspection ever returns tables without column detail).
        if not schema.tables[tl].column_names:
            continue
        keys = {tl}
        if alias and alias.lower() not in _NOT_AN_ALIAS:
            keys.add(alias.lower())
        for key in keys:
            bindings.setdefault(key, set()).add(tl)
    # Drop ambiguous bindings rather than risk a false rejection.
    return {alias: next(iter(tables)) for alias, tables in bindings.items() if len(tables) == 1}


def _unknown_columns(sql: str, schema: Schema, cte_names: set[str]) -> str | None:
    aliases = _alias_to_table(sql, schema, cte_names)
    if not aliases:
        return None

    bad: list[tuple[str, str, str]] = []  # (alias, column, real table)
    seen: set[tuple[str, str]] = set()
    for alias, column in _QUALIFIED_REF_RE.findall(sql):
        al, col = alias.lower(), column.lower()
        if col == "*" or al not in aliases or (al, col) in seen:
            continue
        seen.add((al, col))
        table = aliases[al]
        if not schema.has_column(table, col):
            bad.append((alias, column, table))

    if not bad:
        return None

    parts = []
    for alias, column, table in bad[:3]:
        real = schema.tables[table]
        available = ", ".join(sorted(real.column_names))
        parts.append(
            f"`{alias}.{column}` does not exist — {real.name} has no '{column}' "
            f"column. Its real columns are: {available}."
        )
    return (
        "Unknown column(s). "
        + " ".join(parts)
        + " Use only columns that exist on the table you aliased; do not carry a "
        "column name over from a similarly-named table in another domain."
    )


# --- import GRAND TOTAL that forgot its outer SUM (rule 9, in code) --------
#
# Import PKR value is derived per consignment (line value x that consignment's
# own exchange_rate), so a grand total REQUIRES two stages: GROUP BY c.id in a
# CTE, then SUM over it. Rule 9 spells that out and warns that the one-stage
# form "understates it by roughly 13x and is a WRONG ANSWER".
#
# It still happens intermittently: "what are our imports worth?" returned 173
# rows — one per consignment — and the answer presented individual consignment
# values ("PKR 2,059,840 ... PKR 44,726,080") as the finding. The correct total
# is PKR 987,749,718.61. Rule 9 alone did not hold, so the shape is rejected
# here when the question clearly asks for ONE total.
#
# NARROW: fires only when the question asks for a total/aggregate value with no
# per-shipment intent, the query reads the import tables, it groups by a
# consignment id, and there is no enclosing SUM over that grouped result (i.e.
# no CTE wrapping it). A genuine per-consignment breakdown request is untouched.
_IMPORT_VALUE_TOTAL_RE = re.compile(
    r"(?=.*\b(import|imports|consignment|consignments|shipment|shipments)\b)"
    r"(?=.*\b(worth|value|total|sum|how\s+much)\b)",
    re.IGNORECASE,
)
_PER_SHIPMENT_INTENT_RE = re.compile(
    r"\b(each|per|breakdown|by\s+consignment|by\s+shipment|individual|"
    r"list|which|show\s+me\s+the)\b",
    re.IGNORECASE,
)
_GROUP_BY_CONSIGNMENT_RE = re.compile(
    r"GROUP\s+BY\s+[\w.]*\bid\b|GROUP\s+BY\s+[\w.]*consignment_id\b", re.IGNORECASE
)
_IMPORT_TOTAL_REASON = (
    "Rule 9: import PKR value converts PER CONSIGNMENT (line value x that "
    "consignment's own exchange_rate), so this one-stage `GROUP BY` returns one "
    "row PER CONSIGNMENT, not a total — reporting those rows as what our "
    "imports are worth understates the answer by roughly 13x. Wrap it and sum:\n"
    "  WITH per_consignment AS (\n"
    "    SELECT c.id,\n"
    "           SUM(ci.quantity * ci.unit_price) * MAX(c.exchange_rate) AS pkr_value\n"
    "    FROM consignments c\n"
    "    JOIN consignment_items ci ON ci.consignment_id = c.id\n"
    "    WHERE ci.unit_price IS NOT NULL\n"
    "    GROUP BY c.id\n"
    "  )\n"
    "  SELECT SUM(pkr_value) AS total_import_value_pkr,\n"
    "         COUNT(*) AS consignments_priced\n"
    "  FROM per_consignment\n"
    "This returns ONE row (verified: PKR 987,749,718.61 across 173 priced "
    "consignments)."
)


def _import_total_missing_outer_sum(
    sql: str, question: str | None, refs: set[str], cte_names: set[str]
) -> bool:
    if not question or not _IMPORT_VALUE_TOTAL_RE.search(question):
        return False
    if _PER_SHIPMENT_INTENT_RE.search(question):
        return False  # the user genuinely wants the per-shipment rows
    lowered = {t.lower() for t in refs}
    if not {"consignments", "consignment_items"} & lowered:
        return False
    if not _GROUP_BY_CONSIGNMENT_RE.search(sql):
        return False
    # A CTE means the grouped result is being wrapped — the correct two-stage
    # form. Only the bare one-stage query is wrong.
    return not cte_names


# --- fabricated export "delay" (rule 19, enforced in code) ----------------
#
# `logistics_consignments` has NO planned/expected arrival date — only
# `etd_sailing_date` (departure) and `actual_arrival_date`. So export lateness
# is not computable, and rule 19 says to state that rather than approximate it.
#
# The model kept approximating it anyway, in the same shape every time:
# "already sailed AND not in a finished status" =>
#   WHERE etd_sailing_date < CURRENT_DATE AND current_status NOT IN (...)
# That describes nearly every in-flight shipment. OBSERVED FAILURE: "which
# export shipments are delayed?" returned 88 rows narrated as "88 export
# shipments are currently delayed", including ones whose status was 'On Water'
# (i.e. sailing normally). Restating rule 19 more loudly did not stop it, so
# the ban is enforced here where the model cannot weigh it away.
#
# Deliberately NARROW — it fires only when all three hold:
#   1. the QUESTION asks about lateness (delay/late/overdue/behind schedule),
#   2. the SQL touches the export/logistics header table, and
#   3. the SQL compares a DEPARTURE date against CURRENT_DATE/NOW().
# A transit-time query (etd -> actual_arrival) is untouched, as is any import
# delay question (those have a real `eta` to compare against).
_LATENESS_QUESTION_RE = re.compile(
    r"\b(delay(s|ed|ing)?|late|lateness|overdue|behind\s+schedule)\b", re.IGNORECASE
)
_EXPORT_DEPARTURE_VS_TODAY_RE = re.compile(
    r"etd_sailing_date\s*[<>]=?\s*(CURRENT_DATE|NOW\s*\(\s*\))"
    r"|(CURRENT_DATE|NOW\s*\(\s*\))\s*[-<>]=?\s*[\w.]*etd_sailing_date",
    re.IGNORECASE,
)
# Kept SHORT and purely about the SQL. An earlier version bundled in the
# answer-wording guidance (RFD is not a delivery date, item lines are not
# shipments) and the repair loop then failed all three attempts on a query it
# had previously fixed on the first try — the instruction to rewrite got lost
# in the prose. That wording guidance now lives where it belongs, in
# app/knowledge/coverage.py's export_arrival_lateness_not_recorded caveat,
# which reaches the ANSWER stage instead.
_FABRICATED_EXPORT_DELAY_REASON = (
    "Rule 19: `etd_sailing_date` compared against CURRENT_DATE is not a delay "
    "— it only means the shipment has already sailed, which is true of nearly "
    "every shipment in transit. Export shipments have NO planned arrival date, "
    "so arrival lateness cannot be computed at all. "
    "REWRITE using ready-for-dispatch delay, which IS a real planned-vs-actual "
    "pair:\n"
    "  SELECT li.consignment_id, li.item_detail,\n"
    "         li.planned_rfd_date, li.actual_rfd_date,\n"
    "         li.actual_rfd_date - li.planned_rfd_date AS rfd_delay_days,\n"
    "         COUNT(*) OVER () AS total_matching_rows\n"
    "  FROM logistics_items li\n"
    "  WHERE li.planned_rfd_date IS NOT NULL AND li.actual_rfd_date IS NOT NULL\n"
    "    AND li.actual_rfd_date > li.planned_rfd_date\n"
    "  ORDER BY rfd_delay_days DESC\n"
    "Do not reintroduce any comparison of a departure date against today."
)


def _fabricates_export_delay(sql: str, question: str | None, refs: set[str]) -> bool:
    if not question or not _LATENESS_QUESTION_RE.search(question):
        return False
    if "logistics_consignments" not in {t.lower() for t in refs}:
        return False
    return bool(_EXPORT_DEPARTURE_VS_TODAY_RE.search(sql))


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

    col_error = _unknown_columns(cleaned, schema, cte_names)
    if col_error:
        return GuardResult(ok=False, reason=col_error)

    if question:
        gap = coverage.find_violation(question, refs)
        if gap:
            return GuardResult(ok=False, reason=gap.explanation)

        if _fabricates_export_delay(cleaned, question, refs):
            return GuardResult(ok=False, reason=_FABRICATED_EXPORT_DELAY_REASON)

        if _import_total_missing_outer_sum(cleaned, question, refs, cte_names):
            return GuardResult(ok=False, reason=_IMPORT_TOTAL_REASON)

    safe = _apply_limit(cleaned, max_rows)
    safe = _ensure_nulls_last(safe)

    return GuardResult(ok=True, safe_sql=safe)

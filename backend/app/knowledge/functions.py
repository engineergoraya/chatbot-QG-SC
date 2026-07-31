"""
functions.py — registry of verified read-only PostgreSQL functions the
model may CALL (as `SELECT ... FROM fn('x')`) instead of writing raw SQL
for the question types they cover.

Each entry here is a real function that already exists in the
`supplychain_automation` database, already GRANTed to the read-only
`chatbot_ro` role. This module is the single source of truth for:

  * `app/graph/guard.py` — which allows a `FROM <name>(...)` reference to
    bypass the known-table check ONLY when `<name>` matches an entry here
    AND the call's argument count matches `arg_count` exactly. Every other
    guard rule (single statement, SELECT/WITH only, keyword blocklist,
    LIMIT, NULLS LAST) still applies unchanged to a query using one of
    these functions.
  * `app/knowledge/business_rules.py` — which renders this registry into
    the system prompt as routing guidance, so the model calls the function
    for the question shapes it covers instead of writing equivalent SQL
    by hand.

Adding a new verified function here (plus the matching GRANT in the
database) is the only step needed to make the guard accept it and the
model route to it — no other file needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SqlFunction:
    name: str
    arg_count: int
    args: str  # human-readable signature, for the prompt
    returns: str  # what the function's result table contains
    when_to_use: str  # one-line routing guidance for the model


_FUNCTIONS: list[SqlFunction] = [
    SqlFunction(
        name="current_stock_of",
        arg_count=1,
        args="search_item text",
        returns="current stock for the matched item(s)",
        when_to_use="For 'current stock of X' / 'how much X do we have' questions.",
    ),
    SqlFunction(
        name="supplier_delay",
        arg_count=1,
        args="search_item text",
        returns="late suppliers and their delay in days, for the matched item",
        when_to_use="For supplier lateness/delay questions about a specific item.",
    ),
    SqlFunction(
        name="reorder_recommendation",
        arg_count=1,
        args="search_item text",
        returns="reorder timing and recommendation for the matched item",
        when_to_use="For 'when should we reorder / buy X' questions.",
    ),
]

FUNCTION_REGISTRY: dict[str, SqlFunction] = {f.name.lower(): f for f in _FUNCTIONS}


def get(name: str) -> SqlFunction | None:
    return FUNCTION_REGISTRY.get(name.lower())


def all_functions() -> list[SqlFunction]:
    return list(_FUNCTIONS)

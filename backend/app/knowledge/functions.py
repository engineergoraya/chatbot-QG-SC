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


# EMPTY AS OF 2026-08-03. The database this app now points at defines NO
# user functions at all — `\df` in `supplychain_automation` returns zero
# rows. The three that used to live here (current_stock_of, supplier_delay,
# reorder_recommendation) belonged to the previous, flat data load and were
# dropped along with its schema; every one of them also referenced columns
# (items.item, items.specs, ab_items) that no longer exist.
#
# Leaving them registered would be actively harmful: the guard would happily
# pass `SELECT * FROM current_stock_of('resin')` — the registry is what
# authorizes a function call past the known-table check — and the query would
# then fail at execution with "function does not exist", turning a perfectly
# answerable question into an error. With the list empty, the guard rejects
# such a call up front and business_rules.build_system_prompt() omits the
# function-catalog block entirely, so the model never learns about functions
# that aren't there and simply writes normal SQL.
#
# To re-introduce one: create it in the database, GRANT EXECUTE to
# chatbot_ro, then add an entry here — no other file needs to change.
_FUNCTIONS: list[SqlFunction] = []

FUNCTION_REGISTRY: dict[str, SqlFunction] = {f.name.lower(): f for f in _FUNCTIONS}


def get(name: str) -> SqlFunction | None:
    return FUNCTION_REGISTRY.get(name.lower())


def all_functions() -> list[SqlFunction]:
    return list(_FUNCTIONS)

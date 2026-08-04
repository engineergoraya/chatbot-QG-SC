"""
test_guard.py — unit tests for the SQL safety guard, focused on the
approved-function allow-list added on top of the existing table-existence
check (app/graph/guard.py, app/knowledge/functions.py).

Uses a small in-memory Schema fake instead of a real database connection —
the guard only ever calls `schema.has_table()`.
"""

from __future__ import annotations

import re

import pytest

from app.db.introspect import Schema, Table
from app.graph import guard
from app.knowledge import coverage
from app.knowledge import functions as function_registry


def _schema(*table_names: str) -> Schema:
    return Schema(tables={name.lower(): Table(name=name, kind="BASE TABLE") for name in table_names})


# The live registry is EMPTY (the current database defines no functions — see
# app/knowledge/functions.py). These tests cover the guard's allow-list
# LOGIC, which must keep working whenever a function is registered again, so
# they register a fake entry rather than naming a function that happens to
# exist today. Tests that assert the empty-registry behaviour use the real
# registry untouched.
@pytest.fixture
def registered_fn(monkeypatch):
    fn = function_registry.SqlFunction(
        name="current_stock_of",
        arg_count=1,
        args="search_item text",
        returns="current stock for the matched item(s)",
        when_to_use="test fixture only",
    )
    monkeypatch.setitem(function_registry.FUNCTION_REGISTRY, fn.name, fn)
    return fn


def test_approved_function_call_passes(registered_fn):
    result = guard.validate("SELECT * FROM current_stock_of('resin')", _schema("stock"))
    assert result.ok, result.reason
    assert "current_stock_of" in result.safe_sql
    assert "LIMIT" in result.safe_sql


def test_function_call_rejected_when_registry_is_empty():
    """With no functions registered — today's real state — a function call
    must be rejected by the guard rather than reaching the database and
    failing there with 'function does not exist'."""
    assert function_registry.all_functions() == []
    result = guard.validate("SELECT * FROM current_stock_of('resin')", _schema("stock"))
    assert not result.ok
    assert "unapproved function" in result.reason


def test_unknown_function_name_is_rejected():
    result = guard.validate("SELECT * FROM totally_unapproved_fn('resin')", _schema("stock"))
    assert not result.ok
    assert "unapproved function" in result.reason
    assert "totally_unapproved_fn" in result.reason


def test_registered_function_with_wrong_arity_is_rejected(registered_fn):
    result = guard.validate(
        "SELECT * FROM current_stock_of('resin', 'extra_arg')", _schema("stock")
    )
    assert not result.ok
    assert "current_stock_of" in result.reason
    assert "argument" in result.reason


def test_registered_function_with_zero_args_is_rejected(registered_fn):
    result = guard.validate("SELECT * FROM current_stock_of()", _schema("stock"))
    assert not result.ok
    assert "current_stock_of" in result.reason


def test_normal_table_select_still_works():
    result = guard.validate("SELECT * FROM stock", _schema("stock"))
    assert result.ok, result.reason
    assert "stock" in result.safe_sql


def test_unknown_table_still_rejected():
    result = guard.validate("SELECT * FROM nonexistent_table", _schema("stock"))
    assert not result.ok
    assert "unknown table" in result.reason


def test_blocklisted_keyword_still_rejected():
    result = guard.validate("SELECT * FROM current_stock_of('resin'); DROP TABLE stock", _schema("stock"))
    assert not result.ok


def test_call_syntax_on_approved_function_name_still_rejected():
    # CALL is blocklisted outright regardless of the function registry -
    # the registry only ever permits SELECT ... FROM fn(...) usage.
    result = guard.validate("CALL current_stock_of('resin')", _schema("stock"))
    assert not result.ok
    assert "CALL" in result.reason or "SELECT/WITH" in result.reason


def test_multiple_statements_still_rejected():
    result = guard.validate(
        "SELECT * FROM current_stock_of('resin'); SELECT * FROM current_stock_of('other')",
        _schema("stock"),
    )
    assert not result.ok
    assert "single SQL statement" in result.reason


def test_approved_function_joined_with_real_table(registered_fn):
    sql = (
        "SELECT s.branch, c.* FROM stock s "
        "JOIN current_stock_of('resin') AS c ON true"
    )
    result = guard.validate(sql, _schema("stock"))
    assert result.ok, result.reason


def test_approved_function_inside_cte_still_validated(registered_fn):
    sql = (
        "WITH r AS (SELECT * FROM current_stock_of('resin', 'extra'))"
        " SELECT * FROM r"
    )
    result = guard.validate(sql, _schema("stock"))
    assert not result.ok
    assert "current_stock_of" in result.reason

# --- coverage-gap check (app/knowledge/coverage.py) -------------------------
# The registry is intentionally EMPTY. A shaft/imports gap was registered on
# 2026-08-03 and removed the next day: the next data load added 79 imported
# 'Forged Steel Round Bar' lines, so the "shafts are never imported" premise
# was false and the check would have BLOCKED the correct query. These tests
# pin the behaviour that matters — an empty registry must never reject.

def test_no_gaps_registered_by_default():
    assert coverage.all_gaps() == []


def test_shaft_import_query_is_allowed_now_that_shafts_are_imported():
    """Regression: shafts ARE imported (79 'Forged Steel Round Bar' lines).
    A shaft + imports query must pass the guard, not be rejected."""
    sql = (
        "SELECT ci.item_name, count(*) FROM consignment_items ci "
        "JOIN consignments c ON c.id = ci.consignment_id "
        "WHERE ci.item_name ILIKE '%forged steel round bar%' "
        "AND c.current_status = 'In Transit' GROUP BY ci.item_name"
    )
    result = guard.validate(
        sql,
        _schema("items", "consignments", "consignment_items"),
        question="how many types of shafts are there, and how many are in transit",
    )
    assert result.ok, result.reason


def test_coverage_check_is_inert_with_an_empty_registry():
    sql = "SELECT count(*) FROM consignments WHERE current_status = 'In Transit'"
    schema = _schema("consignments")
    assert guard.validate(sql, schema, question="anything at all").ok
    assert guard.validate(sql, schema).ok


def test_registered_gap_blocks_and_reports(monkeypatch):
    """The mechanism still works when a gap IS registered."""
    gap = coverage.DomainGap(
        name="test-gap",
        question_pattern=re.compile(r"\bwidgets?\b", re.IGNORECASE),
        forbidden_tables=frozenset({"consignments"}),
        verified="test fixture",
        explanation="widgets are not imported; rewrite without consignments",
        answer_note="widgets do not appear in import records",
    )
    monkeypatch.setattr(coverage, "_GAPS", [gap])
    sql = "SELECT count(*) FROM consignments"
    result = guard.validate(sql, _schema("consignments"), question="how many widgets are in transit")
    assert not result.ok
    assert "widgets are not imported" in result.reason
    assert coverage.note_for_question("how many widgets") == "widgets do not appear in import records"

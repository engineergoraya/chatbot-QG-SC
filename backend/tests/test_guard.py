"""
test_guard.py — unit tests for the SQL safety guard, focused on the
approved-function allow-list added on top of the existing table-existence
check (app/graph/guard.py, app/knowledge/functions.py).

Uses a small in-memory Schema fake instead of a real database connection —
the guard only ever calls `schema.has_table()`.
"""

from __future__ import annotations

from app.db.introspect import Schema, Table
from app.graph import guard


def _schema(*table_names: str) -> Schema:
    return Schema(tables={name.lower(): Table(name=name, kind="BASE TABLE") for name in table_names})


def test_approved_function_call_passes():
    result = guard.validate("SELECT * FROM current_stock_of('resin')", _schema("stock"))
    assert result.ok, result.reason
    assert "current_stock_of" in result.safe_sql
    assert "LIMIT" in result.safe_sql


def test_all_three_registered_functions_pass():
    for call in [
        "SELECT * FROM current_stock_of('resin')",
        "SELECT * FROM supplier_delay('resin')",
        "SELECT * FROM reorder_recommendation('resin')",
    ]:
        result = guard.validate(call, _schema("stock"))
        assert result.ok, f"{call} -> {result.reason}"


def test_unknown_function_name_is_rejected():
    result = guard.validate("SELECT * FROM totally_unapproved_fn('resin')", _schema("stock"))
    assert not result.ok
    assert "unapproved function" in result.reason
    assert "totally_unapproved_fn" in result.reason


def test_registered_function_with_wrong_arity_is_rejected():
    result = guard.validate(
        "SELECT * FROM current_stock_of('resin', 'extra_arg')", _schema("stock")
    )
    assert not result.ok
    assert "current_stock_of" in result.reason
    assert "argument" in result.reason


def test_registered_function_with_zero_args_is_rejected():
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


def test_approved_function_joined_with_real_table():
    sql = (
        "SELECT s.branch, c.* FROM stock s "
        "JOIN current_stock_of('resin') AS c ON true"
    )
    result = guard.validate(sql, _schema("stock"))
    assert result.ok, result.reason


def test_approved_function_inside_cte_still_validated():
    sql = (
        "WITH r AS (SELECT * FROM current_stock_of('resin', 'extra'))"
        " SELECT * FROM r"
    )
    result = guard.validate(sql, _schema("stock"))
    assert not result.ok
    assert "current_stock_of" in result.reason

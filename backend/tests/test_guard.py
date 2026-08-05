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

from app.db.introspect import Column, Schema, Table
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


# --- entity resolver + zero-count detection ---------------------------------
# Regression cover for a repeated production failure: a name the user typed
# not matching the stored spelling was reported as absence.
#   "sourcing officer hamza Ahmed" -> stored 'Hamza Ahmad' (65 orders),
#   answered "no orders are recorded under this sourcing officer".
# The model cannot know the stored spelling, so resolution is deterministic.

from app.graph.nodes import _is_all_zero_count
from app.knowledge import entity_resolver


class _Outcome:
    def __init__(self, rows, row_count=None):
        self.rows = rows
        self.row_count = row_count if row_count is not None else len(rows)


def test_zero_count_row_is_treated_as_empty():
    """SELECT COUNT(*) matching nothing returns ONE row containing 0 — not an
    empty set — so it used to be narrated as a real finding."""
    assert _is_all_zero_count(_Outcome([{"orders": 0}]))
    assert _is_all_zero_count(_Outcome([{"orders": 0, "amount_pkr": None}]))


def test_real_rows_are_not_treated_as_empty():
    assert not _is_all_zero_count(_Outcome([{"orders": 13}]))
    # A GROUP BY row naming a supplier with a zero measure is a real finding.
    assert not _is_all_zero_count(_Outcome([{"supplier": "Ayyan Traders", "orders": 0}]))
    # Multiple rows are never this case.
    assert not _is_all_zero_count(_Outcome([{"orders": 0}, {"orders": 0}]))
    assert not _is_all_zero_count(_Outcome([]))
    # A zero-valued boolean/flag column is not a count.
    assert not _is_all_zero_count(_Outcome([{"is_active": False}]))


def test_resolver_finds_misspelled_name(monkeypatch):
    monkeypatch.setattr(
        entity_resolver, "_cache",
        {("purchases_data", "sourcing_o"): ["Hamza Ahmad", "Adnan Shami"]},
    )
    names = [c.value for c in entity_resolver.find_candidates(
        "how many orders are under the sourcing officer hamza Ahmed in purchases"
    )]
    assert "Hamza Ahmad" in names


def test_resolver_stays_quiet_when_no_name_is_named(monkeypatch):
    monkeypatch.setattr(
        entity_resolver, "_cache",
        {("purchases_data", "sourcing_o"): ["Hamza Ahmad", "Adnan Shami"]},
    )
    assert entity_resolver.find_candidates("what is our current available inventory value?") == []
    assert entity_resolver.describe_candidates("") is None


# --- fabricated export "delay" (rule 19, enforced in code) -----------------
#
# `logistics_consignments` has no planned/expected arrival date, so export
# lateness is not computable. The model repeatedly approximated it with
# "already sailed AND not finished" and narrated the result as "88 export
# shipments are currently delayed" — including shipments sailing normally.
# Prompt text did not stop it; these tests pin the code-level ban.

def _export_schema() -> Schema:
    """Real columns, because the guard now validates column names too — a
    column-less fake would make every qualified reference look unknown."""
    return _schema_with_columns(
        logistics_consignments=[
            "id", "customer_name", "origin_country", "etd_sailing_date",
            "actual_arrival_date", "current_status",
        ],
        logistics_items=[
            "consignment_id", "item_detail", "planned_rfd_date", "actual_rfd_date",
        ],
        consignments=["id", "eta", "etd", "current_status"],
    )


def test_fabricated_export_delay_is_rejected():
    sql = (
        "SELECT c.id, c.customer_name, c.etd_sailing_date "
        "FROM logistics_consignments c "
        "WHERE c.current_status NOT IN ('Delivered', 'At QFL') "
        "AND c.etd_sailing_date < CURRENT_DATE"
    )
    result = guard.validate(
        sql, _export_schema(), question="which export shipments are delayed?"
    )
    assert not result.ok
    assert "no planned arrival date" in result.reason.lower()
    # The repair prompt must hand over a concrete, runnable rewrite — an
    # earlier prose-heavy version lost the instruction and burned all three
    # repair attempts on a query the model had previously fixed first try.
    assert "actual_rfd_date - li.planned_rfd_date" in result.reason
    assert "total_matching_rows" in result.reason


def test_export_transit_time_is_not_blocked():
    """The ban is on the fabricated delay, not on the table — transit time is
    a legitimate measure over the same two date columns."""
    sql = (
        "SELECT AVG(actual_arrival_date - etd_sailing_date) AS transit_days "
        "FROM logistics_consignments WHERE actual_arrival_date IS NOT NULL"
    )
    result = guard.validate(
        sql, _export_schema(),
        question="how long do our export shipments take in transit?",
    )
    assert result.ok, result.reason


def test_rfd_delay_query_is_not_blocked():
    """The substitute the rejection steers toward must itself pass."""
    sql = (
        "SELECT li.consignment_id, li.actual_rfd_date - li.planned_rfd_date AS rfd_delay_days "
        "FROM logistics_items li WHERE li.actual_rfd_date IS NOT NULL"
    )
    result = guard.validate(
        sql, _export_schema(), question="which export shipments are delayed?"
    )
    assert result.ok, result.reason


def test_import_overdue_query_is_not_blocked():
    """Imports DO have a planned date (`eta`), so their delay is real and the
    check must not touch them."""
    sql = (
        "SELECT c.id, c.eta, CURRENT_DATE - c.eta AS days_overdue FROM consignments c "
        "WHERE c.eta < CURRENT_DATE AND c.current_status <> 'Arrived at Works'"
    )
    result = guard.validate(
        sql, _export_schema(), question="which imports are overdue?"
    )
    assert result.ok, result.reason


def test_export_departure_filter_allowed_when_question_is_not_about_lateness():
    """A plain "which exports sailed this year" legitimately filters on the
    departure date — the check keys on the QUESTION as well as the SQL."""
    sql = (
        "SELECT c.id, c.etd_sailing_date FROM logistics_consignments c "
        "WHERE c.etd_sailing_date < CURRENT_DATE"
    )
    result = guard.validate(
        sql, _export_schema(), question="which export shipments have sailed?"
    )
    assert result.ok, result.reason


# --- unknown qualified columns ---------------------------------------------
#
# The guard checked table names but not column names, so `column c.eta does
# not exist` came back from the DATABASE and consumed one of only two repair
# attempts. "how many export shipments are on water?" failed outright after
# three attempts on `c.etd`. The check is alias-aware and deliberately only
# rejects references it can prove wrong.

def _schema_with_columns(**tables: list[str]) -> Schema:
    return Schema(
        tables={
            name.lower(): Table(
                name=name,
                kind="BASE TABLE",
                columns=[Column(name=c, data_type="text", nullable=True) for c in cols],
            )
            for name, cols in tables.items()
        }
    )


def test_unknown_qualified_column_is_rejected_with_the_real_columns():
    schema = _schema_with_columns(
        logistics_consignments=["id", "customer_name", "etd_sailing_date", "current_status"],
    )
    sql = "SELECT c.id, c.eta FROM logistics_consignments c WHERE c.eta < CURRENT_DATE"

    result = guard.validate(sql, schema, question="which exports are late?")

    assert not result.ok
    assert "c.eta" in result.reason
    # The repair prompt needs the real columns, or it just guesses again.
    assert "etd_sailing_date" in result.reason


def test_existing_columns_pass():
    schema = _schema_with_columns(
        logistics_consignments=["id", "customer_name", "etd_sailing_date", "actual_arrival_date"],
    )
    sql = (
        "SELECT c.id, c.customer_name, c.etd_sailing_date, c.actual_arrival_date "
        "FROM logistics_consignments c"
    )
    assert guard.validate(sql, schema, question="list exports").ok


def test_cte_and_derived_table_aliases_are_not_column_checked():
    """A CTE's columns are defined by the CTE itself, not by any base table —
    checking them would reject perfectly valid queries, so unbound aliases are
    skipped entirely."""
    schema = _schema_with_columns(
        store_requisition=["item_code", "branch", "req_quantity", "prepare_date"],
        stock=["item_code", "branch", "available_qty"],
    )
    sql = (
        "WITH demand AS ("
        "  SELECT item_code, branch, SUM(req_quantity) / 180.0 AS avg_daily"
        "  FROM store_requisition GROUP BY item_code, branch"
        ") "
        "SELECT s.item_code, d.avg_daily FROM stock s "
        "LEFT JOIN demand d ON d.item_code = s.item_code"
    )
    assert guard.validate(sql, schema, question="which items need reorder?").ok


def test_lateral_subquery_alias_is_not_column_checked():
    schema = _schema_with_columns(
        consignments=["id", "current_status"],
        consignment_items=["consignment_id", "item_name"],
    )
    sql = (
        "SELECT c.id, it.items_on_board FROM consignments c "
        "LEFT JOIN LATERAL ("
        "  SELECT string_agg(DISTINCT ci.item_name, ', ') AS items_on_board "
        "  FROM consignment_items ci WHERE ci.consignment_id = c.id"
        ") it ON TRUE"
    )
    assert guard.validate(sql, schema, question="how many imports are on water?").ok


def test_star_and_bare_table_qualification_are_handled():
    schema = _schema_with_columns(purchases_data=["supplier", "po_number"])

    assert guard.validate("SELECT p.* FROM purchases_data p", schema, question="q").ok
    assert guard.validate(
        "SELECT purchases_data.supplier FROM purchases_data", schema, question="q"
    ).ok


def test_table_with_no_known_columns_is_skipped():
    """A schema entry carrying no column detail means "columns unknown", not
    "this table has none" — rejecting every reference to it would break real
    queries (and every column-less test fixture in this file)."""
    schema = _schema("stock")  # no columns attached

    assert guard.validate("SELECT s.branch FROM stock s", schema, question="q").ok


# --- import grand total that forgot its outer SUM (rule 9, in code) --------

def _import_schema() -> Schema:
    return _schema_with_columns(
        consignments=["id", "exchange_rate", "current_status", "branch_id"],
        consignment_items=["consignment_id", "quantity", "unit_price", "item_name"],
    )


_ONE_STAGE = (
    "SELECT c.id, SUM(ci.quantity * ci.unit_price) * MAX(c.exchange_rate) AS pkr_value "
    "FROM consignments c JOIN consignment_items ci ON ci.consignment_id = c.id "
    "WHERE ci.unit_price IS NOT NULL GROUP BY c.id"
)

_TWO_STAGE = (
    "WITH per_consignment AS (" + _ONE_STAGE + ") "
    "SELECT SUM(pkr_value) AS total_import_value_pkr, COUNT(*) AS consignments_priced "
    "FROM per_consignment"
)


def test_import_total_without_outer_sum_is_rejected():
    """One row per consignment reported as "what our imports are worth"
    understates the answer ~13x (rule 9). Observed: 173 rows narrated as a
    series of individual consignment values."""
    result = guard.validate(
        _ONE_STAGE, _import_schema(), question="what are our imports worth?"
    )
    assert not result.ok
    assert "PER CONSIGNMENT" in result.reason
    assert "WITH per_consignment" in result.reason, "must hand over the rewrite"


def test_correct_two_stage_import_total_passes():
    result = guard.validate(
        _TWO_STAGE, _import_schema(), question="what are our imports worth?"
    )
    assert result.ok, result.reason


def test_per_consignment_breakdown_request_is_allowed():
    """The one-stage query IS the right answer when the user asked for the
    per-shipment rows — the check keys on the question's intent."""
    for question in (
        "show me the value of each import consignment",
        "what is the value per shipment?",
        "give me a breakdown of import value by consignment",
    ):
        result = guard.validate(_ONE_STAGE, _import_schema(), question=question)
        assert result.ok, f"{question!r}: {result.reason}"


def test_non_value_import_question_is_untouched():
    """Grouping by consignment id is perfectly normal outside value questions."""
    sql = (
        "SELECT c.id, COUNT(*) AS lines FROM consignments c "
        "JOIN consignment_items ci ON ci.consignment_id = c.id GROUP BY c.id"
    )
    result = guard.validate(
        sql, _import_schema(), question="how many item lines does each import have?"
    )
    assert result.ok, result.reason

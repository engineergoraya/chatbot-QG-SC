"""
test_answer_grounding.py — regression tests for two verified wrong answers,
both of which were ANSWER-STAGE failures on perfectly correct SQL results.

Observed in production:

  1. "give me the shafts table along with their specs?" ran a correct query
     returning 117 rows (117 item_codes across only 19 distinct product
     names) and the answer opened with "117 types of shafts identified".
     When the user pushed back, it flipped to "19 distinct types" at the
     same 95% confidence — it had no rule to be right by, in either
     direction.

  2. "how many orders are in QCL branch of supplier Al Hatim Impex in
     purchases?" ran a correct COUNT(*) over `purchases_data` returning 4,
     and the answer said "4 orders are recorded". A purchases_data row is a
     PO LINE: the truth is ONE order (QG/PO/2026062357) carrying 4 lines.

The root cause was structural, not a prompt-wording slip: the answer stage
(`OpenAIClient.explain`) received the STYLE rules only, with nothing telling
it what the numbers MEAN, while the grain/counting rules lived exclusively in
the SQL-generation prompt. No amount of fixing SQL generation could reach
either bug.

These tests pin the two halves of the fix:
  * the deterministic half — `_preview` now ships `row_count_means` and a
    per-column `distinct_values` map computed over ALL retrieved rows, so the
    composer has the real 117-vs-19 figures instead of an inference from a
    30-row sample;
  * the prompt half — `explain()`'s system prompt now carries
    ANSWER_GROUNDING and COUNTING_SEMANTICS, and the business rules state the
    PO-line-vs-order grain.

No network calls and no database: `_preview` is pure, and the explain() test
captures the assembled messages from a stubbed SDK client.
"""

from __future__ import annotations

import json

import pytest

from app.graph import nodes
from app.knowledge import business_rules
from app.llm import openai_client as oc


# --- the deterministic half: what the answer stage is actually shown -----

def _shaft_rows(n_rows: int = 117, n_names: int = 19) -> tuple[list[str], list[dict]]:
    """A stand-in for the real shaft result: many item_codes, few names."""
    columns = ["item_code", "item_name", "specification", "available_qty"]
    rows = []
    for i in range(n_rows):
        rows.append(
            {
                "item_code": f"{1000 + i}-60",
                "item_name": f"Shaft Type {i % n_names}",
                "specification": f"SPEC{i}",
                # Mostly NULL, as in the real data: only 1 of the 117 shaft
                # item_codes has a stock row at all.
                "available_qty": 4.0 if i == 0 else None,
            }
        )
    return columns, rows


def test_preview_reports_distinct_value_counts_over_all_rows():
    """The composer only sees a 30-row sample, so the 117-vs-19 distinction
    has to be computed here, in code, over the full retrieved set."""
    columns, rows = _shaft_rows()
    payload = json.loads(nodes._preview(columns, rows))

    assert payload["row_count"] == 117
    assert len(payload["rows"]) == 30, "still a sample, not the whole result"

    distinct = payload["distinct_values"]
    assert distinct["item_code"] == 117
    assert distinct["item_name"] == 19, (
        "this is the number the answer must call 'types' — the bug was "
        "narrating row_count (117) as the type count"
    )
    # NULLs are not distinct values: only one row carries a real quantity.
    assert distinct["available_qty"] == 1


def test_preview_spells_out_what_row_count_is_not():
    """`row_count` was read as a business quantity twice over ("117 types",
    "4 orders"), so the payload states plainly what it is not."""
    columns, rows = _shaft_rows()
    payload = json.loads(nodes._preview(columns, rows))

    meaning = payload["row_count_means"].lower()
    assert "result rows" in meaning
    assert "types" in meaning and "orders" in meaning
    assert "quantity" in meaning
    assert "distinct_values_means" in payload


def test_preview_omits_distinct_values_for_a_single_row():
    """On a one-row aggregate, a distinct-count per column is always 1 and
    carries no information — the same trap as `total_matching_rows` on a
    scalar aggregate (rule 17b), so don't ship noise the model may narrate."""
    payload = json.loads(nodes._preview(["orders"], [{"orders": 1}]))

    assert payload["row_count"] == 1
    assert "distinct_values" not in payload


def test_preview_handles_unhashable_and_empty_results():
    """A JSON/array column must not raise, and an empty result must not
    fabricate a distinct-values map."""
    payload = json.loads(nodes._preview(["blob"], [{"blob": [1, 2]}, {"blob": [1, 2]}]))
    assert payload["distinct_values"]["blob"] == 1  # keyed by str(), so equal

    empty = json.loads(nodes._preview(["orders"], []))
    assert empty["row_count"] == 0
    assert "distinct_values" not in empty


# --- the prompt half: the answer stage is finally told what things mean ---

class _StubResponse:
    def __init__(self, text: str) -> None:
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


@pytest.fixture
def captured_explain_messages(monkeypatch) -> list:
    """Run explain() against a stubbed SDK and hand back the messages that
    would have been sent."""
    captured: list = []

    def _create(**kwargs):
        captured.append(kwargs["messages"])
        return _StubResponse("**Descriptive**\n- ok")

    client = oc.OpenAIClient.__new__(oc.OpenAIClient)
    client.available = True
    client._client = type(
        "SDK", (), {"chat": type("Chat", (), {"completions": type("Comp", (), {"create": staticmethod(_create)})()})()}
    )()

    columns, rows = _shaft_rows()
    client.explain("give me the shafts table", "SELECT ...", nodes._preview(columns, rows))
    return captured[0]


def test_explain_prompt_carries_the_grain_and_counting_rules(captured_explain_messages):
    """The regression that caused both wrong answers: this stage used to get
    RESPONSE_STYLE + ANALYTIC_STRUCTURE only."""
    system = captured_explain_messages[0]["content"]

    assert business_rules.ANSWER_GROUNDING in system
    assert business_rules.COUNTING_SEMANTICS in system
    # ...without losing what was already there.
    assert business_rules.RESPONSE_STYLE in system
    assert business_rules.ANALYTIC_STRUCTURE in system


def test_answer_grounding_covers_both_observed_failures():
    text = business_rules.ANSWER_GROUNDING

    # Case 2: PO lines are not orders.
    assert "po_number" in text
    assert "PO LINE" in text
    # Case 1: row count is not a type count.
    assert "row_count" in text
    assert "distinct_values" in text, "must point at the map _preview ships"
    # The NULL-quantity misreading that produced "awaiting restock".
    assert "restock" in text.lower()


def test_conversational_prompt_carries_grain_rules_but_not_preview_rules():
    """The push-back turn ("but 117 are not the types") is a question about a
    figure already given, so it takes the CONVERSATIONAL escape — where there
    is no result preview. It needs the grain rules to settle the dispute, but
    must NOT be told to consult a `distinct_values` map it will never see."""
    captured: list = []

    def _create(**kwargs):
        captured.append(kwargs["messages"])
        return _StubResponse("- 117 is the item-code count.")

    client = oc.OpenAIClient.__new__(oc.OpenAIClient)
    client.available = True
    client._client = type(
        "SDK", (), {"chat": type("Chat", (), {"completions": type("Comp", (), {"create": staticmethod(_create)})()})()}
    )()

    client.answer_conversationally([], "but 117 are not the types, this is the quantity?")
    system = captured[0][0]["content"]

    assert business_rules.GRAIN_RULES in system
    assert "distinct_values" not in system, "no preview exists on this path"
    assert business_rules.ANSWER_GROUNDING not in system


def test_conversational_escape_covers_a_pushback_on_a_figures_meaning():
    """Without this, the push-back went down the SQL path and produced a
    third, newly-wrong number ("Total types: 4")."""
    escape = oc._CONVERSATIONAL_ESCAPE

    assert "117 are not the types" in escape
    assert "lines, not orders" in escape


def test_generation_reminder_states_the_order_grain():
    reminder = oc._GENERATION_REMINDER_FRESH

    assert "COUNT(DISTINCT po_number)" in reminder
    assert "PO LINE" in reminder


def test_types_template_prefers_the_ungrouped_two_scalar_form():
    """Both grouped variants went wrong in testing: `GROUP BY i.category`
    reported "Total types: 4", and `COUNT(DISTINCT i.name)` inside a query
    grouped BY that name reported "1 product type". The plain two-scalar form
    cannot be misread, so it is the one the rules lead with."""
    counting = business_rules.COUNTING_SEMANTICS

    assert "COUNT(DISTINCT i.name) AS total_types" in counting
    assert "total_item_codes" in counting
    # Both traps must stay documented.
    assert "It is ALWAYS 1 there" in counting
    assert "types_in_category" in counting


def test_delayed_suppliers_rule_forbids_a_rate_column():
    """The delayed list twice came back carrying a LATE percentage aliased
    `on_time_pct`, and the answer narrated "100% on-time ... no late
    deliveries" about the 84 most delayed suppliers."""
    rules = business_rules.BUSINESS_RULES

    assert "DO NOT ADD A RATE\n         COLUMN TO THIS QUERY AT ALL" in rules
    assert "avg_delay_days" in rules
    assert "lines_measured" in rules
    assert "NEVER alias a late/delay rate as `on_time_pct`" in rules


def test_answer_grounding_requires_checking_aliases_against_expressions():
    """The answer stage trusted `on_time_pct` over the CASE that built it."""
    text = business_rules.ANSWER_GROUNDING

    assert "ALIAS AGAINST THE EXPRESSION" in text
    assert "on_time_pct" in text


def test_business_rules_teach_distinct_po_number_for_orders():
    """Rule 15's worked examples previously aliased `COUNT(*) AS orders`,
    actively teaching the bug. No example may do that any more."""
    for block in (business_rules.BUSINESS_RULES, business_rules.COUNTING_SEMANTICS):
        assert "COUNT(*) AS orders" not in block

    assert "COUNT(DISTINCT po_number)" in business_rules.BUSINESS_RULES
    assert "23,131" in business_rules.BUSINESS_RULES, "verified distinct-PO count"


def test_business_rules_no_longer_forbid_purchases_po_date():
    """`purchases_data.po_date` is populated on all 68,298 rows on the current
    load; the rules used to declare it 100% NULL and ban it outright, which
    hid the only real order-date dimension."""
    rules = business_rules.BUSINESS_RULES

    assert "purchases_data.po_date" not in rules.split("7. NULLS ARE EXPECTED")[1].split(
        "8. IMPORTS"
    )[0], "must not still be listed as a 100%-empty column"
    assert "2022-05-09" in rules, "the verified po_date range"
    # The import-domain column of the same name IS still empty — the two must
    # not be conflated in either direction.
    assert "consignments.po_date" in rules


# --- the true total behind the row cap -------------------------------------
#
# Queries carry `COUNT(*) OVER () AS total_matching_rows` so the real total
# survives the 200-row display cap (rule 17b). Sitting inside the row dicts it
# was easy to overlook: an RFD-delay listing capped at 200 rows was narrated
# "200 item lines with delays" when the true total was 379. Lifting it to the
# top level of the preview made the answer quote 379.

def test_preview_lifts_the_true_total_above_the_row_cap():
    rows = [{"id": i, "total_matching_rows": 379} for i in range(200)]
    payload = json.loads(nodes._preview(["id", "total_matching_rows"], rows))

    assert payload["row_count"] == 200
    assert payload["true_total_matching_rows"] == 379
    assert "379" in payload["true_total_means"]
    assert "never 200" in payload["true_total_means"]


def test_preview_omits_true_total_when_it_equals_the_row_count():
    """Nothing was capped, so there is no second number to explain — emitting
    one invites the "the total matching rows is 1" filler rule 17b forbids."""
    rows = [{"id": i, "total_matching_rows": 3} for i in range(3)]
    payload = json.loads(nodes._preview(["id", "total_matching_rows"], rows))

    assert "true_total_matching_rows" not in payload


def test_true_total_ignores_a_column_that_disagrees_across_rows():
    """A window count is constant by construction. If it varies, the column is
    something else entirely and must not be quoted as the total."""
    rows = [{"total_matching_rows": 5}, {"total_matching_rows": 9}]
    assert nodes._true_total(rows) is None


def test_true_total_ignores_non_integer_and_boolean_values():
    assert nodes._true_total([{"total_matching_rows": "many"}]) is None
    assert nodes._true_total([{"total_matching_rows": True}]) is None
    assert nodes._true_total([{"other": 4}]) is None


def test_export_lateness_caveat_fires_only_for_export_lateness_questions():
    """The answer must disclose that export ARRIVAL lateness is unmeasurable.
    Import delay questions have a real `eta`, so they must NOT get this note."""
    from app.knowledge import coverage

    assert coverage.note_for_question("which export shipments are delayed?")
    assert coverage.note_for_question("are our exports late?")
    # Not a lateness question, and not an export question, respectively.
    assert coverage.note_for_question("how many export shipments are on water?") is None
    assert coverage.note_for_question("which imports are overdue?") is None

    note = coverage.note_for_question("which export shipments are delayed?")
    assert "no planned or expected ARRIVAL date" in note
    assert "READY-FOR-DISPATCH" in note
    assert "total_matching_rows" in note

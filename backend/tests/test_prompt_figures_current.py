"""
test_prompt_figures_current.py — a staleness tripwire for the VERIFIED figures
baked into business_rules.py.

WHY THIS EXISTS. The prompt asserts dozens of concrete counts as facts ("2,778
purchase lines", "194 suppliers", "ZERO of the 117 shaft codes have purchase
history"). Those are load-bearing: the model quotes them, reasons from them,
and — worst case — declines to query a table the prompt says is empty.

A data reload silently invalidates them. That has already happened twice:

  * `purchases_data.po_date` went from 100% NULL to 100% populated, while the
    rules still said "NEVER use it, it will silently return nothing" — which
    hid the only real order-date dimension in the table.
  * the shaft family went from zero purchase/issuance history to 17 purchase
    lines and 37 issuance lines, while the rules still said there was none —
    which would make the assistant refuse to look.

Nothing in the ordinary test suite catches that, because the prompt is just a
string and it stays internally consistent while becoming factually wrong.

These tests re-derive each figure from the LIVE database and assert the prompt
still agrees. They SKIP cleanly when no database is reachable, so CI without a
DB is unaffected. A failure here does NOT mean the code is broken — it means
the data moved and the prompt text needs refreshing to match.
"""

from __future__ import annotations

import pytest

from app.knowledge.business_rules import (
    ANSWER_GROUNDING,
    BUSINESS_RULES,
    COUNTING_SEMANTICS,
    GRAIN_RULES,
    ITEM_NAME_ALIASES,
)

# Any of these blocks may legitimately be where a given figure is stated —
# they are all assembled into the same prompt (see build_system_prompt).
ALL_PROMPT_TEXT = "\n".join(
    [BUSINESS_RULES, COUNTING_SEMANTICS, GRAIN_RULES, ANSWER_GROUNDING, ITEM_NAME_ALIASES]
)

psycopg = pytest.importorskip("psycopg2", reason="psycopg2 not installed")


@pytest.fixture(scope="module")
def rows():
    """One round-trip that collects every figure these tests check."""
    from app import config

    try:
        conn = psycopg.connect(**config.readonly_dsn(), connect_timeout=3)
    except Exception as exc:  # no DB in this environment
        pytest.skip(f"database not reachable: {exc}")

    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT (SELECT count(*) FROM purchases_data),
                   (SELECT count(DISTINCT po_number) FROM purchases_data),
                   (SELECT count(DISTINCT supplier) FROM purchases_data),
                   (SELECT count(po_date) FROM purchases_data),
                   (SELECT count(*) FROM items),
                   (SELECT count(DISTINCT name) FROM items),
                   (SELECT count(*) FROM stock),
                   (SELECT count(*) FROM issuance),
                   (SELECT count(*) FROM consignments),
                   (SELECT count(*) FROM consignment_items)
            """
        )
        r = cur.fetchone()
    conn.close()
    return {
        "po_lines": r[0],
        "po_orders": r[1],
        "suppliers": r[2],
        "po_date_filled": r[3],
        "items": r[4],
        "item_names": r[5],
        "stock": r[6],
        "issuance": r[7],
        "consignments": r[8],
        "consignment_items": r[9],
    }


def _fmt(n: int) -> str:
    return f"{n:,}"


@pytest.mark.parametrize(
    "key,label",
    [
        ("po_lines", "purchases_data row count"),
        ("po_orders", "distinct po_number count"),
        ("suppliers", "distinct purchases_data.supplier count"),
        ("items", "items row count"),
        ("item_names", "distinct items.name count"),
        ("issuance", "issuance row count"),
    ],
)
def test_business_rules_quote_the_live_figure(rows, key, label):
    """Each of these appears in the prompt as a VERIFIED count. If the data
    moved, the prompt text has to move with it."""
    expected = _fmt(rows[key])
    assert expected in ALL_PROMPT_TEXT, (
        f"The prompt no longer states the live {label} ({expected}). "
        "The data was reloaded — refresh the VERIFIED figures in "
        "business_rules.py rather than deleting this assertion."
    )


def test_grain_blocks_quote_the_live_po_grain(rows):
    """The line-vs-order ratio is the fact that fixed the "4 orders" bug, and
    it is stated in three places that must not drift apart."""
    lines, orders = _fmt(rows["po_lines"]), _fmt(rows["po_orders"])

    for name, block in (("GRAIN_RULES", GRAIN_RULES), ("COUNTING_SEMANTICS", COUNTING_SEMANTICS)):
        assert lines in block and orders in block, (
            f"{name} must state the live PO grain ({lines} lines / {orders} orders)"
        )


def test_po_date_is_still_populated(rows):
    """The rules now tell the model po_date IS usable. If a future load empties
    it again, that instruction becomes actively harmful."""
    assert rows["po_date_filled"] == rows["po_lines"], (
        "purchases_data.po_date is no longer fully populated — rule 3 currently "
        "tells the model to use it as the order date. Re-check rule 3."
    )


def test_shaft_family_still_has_the_activity_the_rules_describe(rows):
    """The rules were wrong in BOTH directions here at different times: first
    claiming zero purchase/issuance history when there was none (true then),
    then keeping that claim after a load added some. Pin the current state."""
    from app import config

    conn = psycopg.connect(**config.readonly_dsn(), connect_timeout=3)
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH shafts AS (
              SELECT item_code FROM items
              WHERE category = 'Shaft Material(Temp)' OR name ILIKE '%shaft%'
            )
            SELECT (SELECT count(*) FROM shafts),
                   (SELECT count(DISTINCT i.name) FROM items i
                    WHERE i.category = 'Shaft Material(Temp)' OR i.name ILIKE '%shaft%'),
                   (SELECT count(*) FROM stock s JOIN shafts USING (item_code)),
                   (SELECT count(*) FROM purchases_data p JOIN shafts USING (item_code))
            """
        )
        codes, names, stock_rows, purchase_lines = cur.fetchone()
    conn.close()

    assert f"{codes}" in ALL_PROMPT_TEXT
    assert (codes, names) == (117, 19), (
        f"the shaft family is now {codes} codes / {names} names; the rules and "
        "the ITEM_NAME_ALIASES block both quote 117 / 19"
    )
    assert stock_rows == 1, "rules say exactly one shaft code carries a stock row"
    assert purchase_lines > 0, (
        "shafts have no catalogue-code purchase history again — the rules now "
        "tell the model such history EXISTS. Re-check the shaft COVERAGE note."
    )

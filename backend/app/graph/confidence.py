"""
confidence.py — the ChatResponse.confidence scheme, in one documented place.

This is a heuristic reflecting HOW the answer was produced, not a
model-calibrated probability — there is no ground truth to calibrate
against, so we don't pretend to more precision than that. Every value below
is derived only from state the graph already tracks (repair_count,
row_count, done_reason) — nothing here is invented per-answer.

Scheme, highest to lowest trust:

  DICTIONARY_HIT (1.0)
    A static glossary definition (app/knowledge/dictionary.py) — fixed,
    human-authored text with zero live-data risk. Never wrong in the sense
    that matters here: it's not a claim about current data at all.

  CLEAN_SQL (0.95)
    Generated SQL passed the guard and executed successfully on the FIRST
    attempt, with rows. The common, expected case.

  REPAIRED_ONCE (0.75) / REPAIRED_TWICE (0.55)
    The SQL guard rejected the query or the DB returned an error, and the
    model corrected it on the 1st/2nd repair attempt (see nodes.repair_sql,
    capped at _MAX_REPAIRS=2). Still a real, executed, correct-per-the-guard
    query — but needing a correction is itself a (mild, then stronger)
    signal that the question was harder to translate reliably.

  EMPTY_RESULT (0.6)
    The query executed correctly (passed the guard, no DB error) but
    matched zero rows. The SQL logic is presumably sound; the uncertainty
    is whether the ZERO is the right answer or a subtly wrong filter (e.g.
    a misspelled item/supplier/branch) — moderate, not high, confidence.

  CONVERSATIONAL (0.9)
    A question about the conversation itself ("what did I ask first?",
    "explain that more simply"), answered from the session transcript
    rather than the database. High, but below CLEAN_SQL: it's grounded in
    real prior turns, though it restates earlier figures rather than
    re-verifying them against live data.

  CLARIFICATION_NEEDED (0.4)
    Not an answer at all yet — the assistant asked back for a time period
    (business_rules.py rule 14) rather than assume a window. Scored low
    because there IS no data-backed answer on this turn.

  GIVE_UP (0.0) / CONFIG_OR_GENERATION_ERROR (0.0)
    No usable answer: repairs exhausted (guard kept rejecting, or the DB
    kept erroring), or a hard failure before that (no API key, an
    exception calling the LLM). Zero, not a small positive number — there
    is nothing here to trust.
"""

from __future__ import annotations

DICTIONARY_HIT = 1.0
CLEAN_SQL = 0.95
REPAIRED_ONCE = 0.75
REPAIRED_TWICE = 0.55
EMPTY_RESULT = 0.6
CONVERSATIONAL = 0.9
CLARIFICATION_NEEDED = 0.4
GIVE_UP = 0.0
CONFIG_OR_GENERATION_ERROR = 0.0


def for_successful_answer(repair_count: int) -> float:
    """Confidence for a real, executed-with-rows answer, scaled by how many
    repair attempts it took to get a query that passed the guard and ran."""
    if repair_count <= 0:
        return CLEAN_SQL
    if repair_count == 1:
        return REPAIRED_ONCE
    return REPAIRED_TWICE

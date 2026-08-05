"""
coverage.py — verified DOMAIN COVERAGE GAPS, enforced deterministically.

A coverage gap is a case where an entity the user asks about has ZERO rows in
some domain, so any query joining that domain can only return one of two
wrong answers:

  * it filters to the entity and gets 0 rows — reported as "we have none",
    which is false about an entity that demonstrably exists; or
  * it drops the entity filter to avoid the zero and reports a table-wide
    total under the entity's name — an outright fabrication.

WHY THIS IS CODE AND NOT PROMPT TEXT. Prompt text is guidance the model
weighs; this is a check it cannot talk its way past. The guard rejects the
query and the existing repair loop re-prompts with `explanation`.

REGISTER A GAP ONLY WHEN IT IS VERIFIED AND STRUCTURAL — a fact about how the
business records data, not a fact about how much data happens to be loaded
today. That distinction matters:

  A shaft/imports gap WAS registered here on 2026-08-03, on the verified
  observation that the shaft family had zero consignment_items rows. The very
  next data load added 79 'Forged Steel Round Bar' lines — shafts ARE
  imported, they are simply recorded under the alias names staff use rather
  than the catalogue name. The gap was never structural, only an artefact of
  a partial load, and once the data arrived the check would have BLOCKED the
  correct query and forced a false "shafts aren't imported" answer. It has
  been removed.

  The lesson: an empty domain today is not the same as a domain that cannot
  hold the entity. Prefer a business rule (which degrades to a slightly
  worse answer) over a hard block (which can invert into a wrong answer)
  unless the gap is structural — e.g. the export/logistics domain has no
  item_code COLUMN at all, which no amount of loading can change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DomainGap:
    name: str
    question_pattern: re.Pattern[str]
    forbidden_tables: frozenset[str]
    verified: str
    explanation: str  # for the repair loop: how to rewrite the SQL
    answer_note: str  # for the answer composer: what to tell the user

    def applies_to(self, question: str) -> bool:
        return bool(self.question_pattern.search(question))


_GAPS: list[DomainGap] = []


@dataclass(frozen=True)
class AnswerCaveat:
    """A note the ANSWER must carry, for a question the data can only answer
    with a SUBSTITUTE measure.

    Distinct from DomainGap: a gap BLOCKS a query (the domain holds nothing
    for the entity), whereas a caveat lets the query run and forces the answer
    to say what the figure actually is. Use this when a real, useful number
    exists but it is not the number the user asked for — the failure mode is
    then not an empty result, it is a confidently mislabelled one.

    WHY THIS EXISTS. app/graph/guard.py rejects the fabricated export-delay
    query shape and its `reason` steers the REPAIR loop to the honest
    substitute (RFD delay / transit time). But that reason only ever reaches
    SQL generation. Once the substitute query succeeded, the answer stage knew
    nothing about the swap and reported RFD dates as "scheduled for delivery",
    called 200 item lines "200 export shipments are delayed", and never
    mentioned that export arrival lateness is not recorded at all. The caveat
    is how that context reaches the composer.
    """

    name: str
    question_pattern: re.Pattern[str]
    verified: str
    answer_note: str

    def applies_to(self, question: str) -> bool:
        return bool(self.question_pattern.search(question))


_CAVEATS: list[AnswerCaveat] = [
    AnswerCaveat(
        name="export_arrival_lateness_not_recorded",
        # Export/logistics lateness only. Requires an export cue AND a
        # lateness cue, so import delay questions (which have a real `eta`)
        # and plain export listings are both untouched.
        question_pattern=re.compile(
            r"(?=.*\b(export|exports|outbound|customer\s+shipment|dispatch)\b)"
            r"(?=.*\b(delay(s|ed|ing)?|late|lateness|overdue|behind\s+schedule)\b)",
            re.IGNORECASE,
        ),
        verified=(
            "logistics_consignments has etd_sailing_date and "
            "actual_arrival_date but NO planned/expected arrival date column "
            "(verified 2026-08-05), so arrival lateness is not computable."
        ),
        answer_note=(
            "REQUIRED: the FIRST bullet under Descriptive must be exactly this "
            "point, before any figure — this database records no planned or "
            "expected ARRIVAL date for export shipments, so export lateness "
            "against arrival cannot be measured, and the delay shown below is "
            "a different measure.\n"
            "Then: the figure is READY-FOR-DISPATCH (RFD) delay — how much "
            "later goods became ready to ship than planned. Never call an RFD "
            "date a delivery date, an arrival date, or 'scheduled for "
            "delivery'. Never call the result 'shipments delayed'; the rows are "
            "ITEM LINES (one shipment contributes several), and the count to "
            "quote is total_matching_rows, not the number of rows shown."
        ),
    ),
]


def find_violation(question: str, referenced_tables: set[str]) -> DomainGap | None:
    """Return the first coverage gap this query violates, if any.

    A violation is: the question is about the gap's entity AND the query
    references one of the tables that hold no rows for it.
    """
    if not question:
        return None
    lowered = {t.lower() for t in referenced_tables}
    for gap in _GAPS:
        if gap.applies_to(question) and (lowered & gap.forbidden_tables):
            return gap
    return None


def note_for_question(question: str) -> str | None:
    """A note for the answer composer when the question touches a known
    coverage gap (nothing to report) or answer caveat (a substitute measure
    was reported instead of the one asked for)."""
    if not question:
        return None
    for gap in _GAPS:
        if gap.applies_to(question):
            return gap.answer_note
    for caveat in _CAVEATS:
        if caveat.applies_to(question):
            return caveat.answer_note
    return None


def all_caveats() -> list[AnswerCaveat]:
    return list(_CAVEATS)


def all_gaps() -> list[DomainGap]:
    return list(_GAPS)

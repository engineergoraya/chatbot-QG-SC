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
    """A short, user-facing note for the answer composer when the question
    touches a known coverage gap."""
    if not question:
        return None
    for gap in _GAPS:
        if gap.applies_to(question):
            return gap.answer_note
    return None


def all_gaps() -> list[DomainGap]:
    return list(_GAPS)

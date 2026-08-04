"""
coverage.py — verified DOMAIN COVERAGE GAPS, enforced deterministically.

A coverage gap is a case where an entity the user asks about has ZERO rows in
some domain, so any query joining that domain can only return one of two
wrong answers:

  * it filters to the entity and gets 0 rows — reported as "we have none",
    which is false about an entity that demonstrably exists; or
  * it drops the entity filter to avoid the zero and reports a table-wide
    total under the entity's name — an outright fabrication.

Both were observed in production for the same question ("how many types of
shafts are there, and how many are in transit?"): first "0 shafts in transit",
then "11 shafts in transit" — 11 being the count of ALL in-transit
consignments, nothing to do with shafts.

WHY THIS IS CODE AND NOT PROMPT TEXT. The business rules already state the
shaft/import gap twice, emphatically, and the model still produced both wrong
answers. Prompt text is guidance the model weighs; this is a check it cannot
talk its way past. The guard rejects the query and the existing repair loop
re-prompts with `explanation` as the error, so the model rewrites without the
empty domain instead of inventing a number from it.

Each gap is a VERIFIED fact about the live data, not a guess — see the
`verified` field. Add a new entry when profiling turns up another domain with
no rows for a whole entity family; nothing else needs to change.
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


# "shaft", "shafts", plus the owner-confirmed alternative names that mean the
# same family (see business_rules.ITEM_NAME_ALIASES). Deliberately narrow: it
# must not fire on an unrelated question that merely contains the letters.
_SHAFT_RE = re.compile(
    r"\bshafts?\b"
    r"|\bforged\s+(alloy\s+)?steel\s+(alloy\s+)?round\s+bar"
    r"|\bforged\s+steel\s+hollow\s+drill\s+bars?",
    re.IGNORECASE,
)

_GAPS: list[DomainGap] = [
    DomainGap(
        name="shafts-are-never-imported",
        question_pattern=_SHAFT_RE,
        forbidden_tables=frozenset({"consignments", "consignment_items"}),
        verified=(
            "The 117-item shaft family (items.category = 'Shaft "
            "Material(Temp)' OR items.name ILIKE '%shaft%') has ZERO rows in "
            "consignment_items."
        ),
        explanation=(
            "This question is about shafts, and shafts have NO rows in the "
            "import tables at all — verified: zero consignment_items rows "
            "across the whole 117-item shaft family. So `consignments` and "
            "`consignment_items` must not appear in this query in any form "
            "(no join, no subquery, no CTE, no scalar SELECT). A count from "
            "them is either a meaningless 0 or, if the shaft filter is "
            "dropped, the count of ALL consignments reported as if it were "
            "shafts. Rewrite the query against `items` (and `stock` or "
            "`purchases_data` if the question needs them), answer the part "
            "that is actually answerable, and state plainly that shafts do "
            "not appear in import records so in-transit/shipment status "
            "cannot be reported for them."
        ),
        answer_note=(
            "IMPORTANT — this question mentions shafts. Shafts are NOT "
            "imported through this system: verified, the entire 117-item "
            "shaft family has zero rows in the import records. If the "
            "question asked anything about shafts being in transit, on "
            "water, shipped, or arriving, you MUST say plainly that shafts "
            "do not appear in the import records at all, so that status "
            "cannot be reported for them — and that their only "
            "transactional history here is purchase orders. Do NOT say "
            "'none are in transit', 'the in-transit count is 0', or 'the "
            "query did not provide that information' — the first two are "
            "false and the third blames the query for a fact about the "
            "data."
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
    """A short, user-facing note for the answer composer when the question
    touches a known coverage gap.

    The guard's `explanation` is written for the repair loop (it tells the
    model how to rewrite the SQL). This is the other half: once a correct,
    gap-free query has run, the ANSWER still has to tell the user why part
    of what they asked can't be reported. Without it the reply degrades to a
    passive "the query did not provide that information", which reads like a
    system limitation rather than a fact about the data.
    """
    if not question:
        return None
    for gap in _GAPS:
        if gap.applies_to(question):
            return gap.answer_note
    return None


def all_gaps() -> list[DomainGap]:
    return list(_GAPS)

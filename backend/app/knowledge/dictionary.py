"""
dictionary.py — fast-path answers for pure "what does X mean?" questions.

Per the original SQL-reasoning design (11_SQL_Reasoning_Rules.pdf, "Question
classification"): Definition questions never hit the database. They're
answered from a fixed glossary instead of a generated query — zero SQL, zero
hallucination risk, and much faster than a full LLM round trip.

This module loads the two structured knowledge files the user supplied
(business_dictionary.json, 735-entry 13_SYNONYM_MAPPING.json) and exposes:

  * is_definition_question(q)  — heuristic: does this look like a glossary
    lookup rather than a request for a live figure?
  * lookup(q)                  — best-matching glossary entry, if any.

IMPORTANT: only the human-language "meaning" is served verbatim. The
"database_term"/"fields" values in these JSON files describe the ORIGINAL
PLANNED schema, which the live database no longer resembles at all (it was
replaced wholesale in the 2026-08-03 load — see the note at the top of
app/knowledge/business_rules.py) — so those fields are deliberately NOT
surfaced here to avoid contradicting the verified, live-schema rules used for
actual SQL generation. The entries whose plain-English meaning itself assumed
the old design (Critical Item, Reorder Level, Safety Stock, Stock Health, LC,
ALC/ELC) are explicitly corrected below and were re-checked against the
current data on 2026-08-03.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "reference_data"

_DEFINITION_PATTERNS = [
    r"^\s*what\s+(is|are|does)\b",
    r"^\s*what'?s\b",
    r"^\s*define\b",
    r"^\s*meaning\s+of\b",
    r"\bwhat\s+does\s+.+\s+mean\b",
]

# Words that mean the user wants a LIVE figure/lookup, not a glossary
# explanation, even though the sentence also matches a _DEFINITION_PATTERN
# and contains a known term — e.g. "What is OUR CURRENT available stock
# VALUE?" vs "What is hold stock?". Per 11_SQL_Reasoning_Rules.pdf's own
# distinction: "What is supplier delay?" is a Definition question, but
# "What is our worst supplier delay?" is Derived-metric.
_QUANT_TRIGGERS = [
    r"\bour\b", r"\bcurrent(ly)?\b", r"\btotal\b", r"\bvalue\b", r"\bworth\b",
    r"\bhow much\b", r"\bhow many\b", r"\bcount\b", r"\bright now\b",
    r"\btoday\b", r"\bnow\b", r"\bthis (month|year|week|quarter)\b",
    r"\blast (month|year|week|quarter)\b", r"\bnumber of\b", r"\bsum\b",
    r"\baverage\b", r"\bavg\b", r"\btop\b", r"\bworst\b", r"\bbest\b",
    r"\bmost\b", r"\bleast\b", r"\bhighest\b", r"\blowest\b",
]

# An item code like '19981-60', or any capitalized word beyond the sentence's
# first token (a supplier/branch/customer/file name) — both mean the
# question names a SPECIFIC entity, so it wants a live lookup for that
# entity, not a glossary definition of the general term.
_ITEM_CODE_RE = re.compile(r"\b\d{3,7}-\d{1,4}\b")


def _looks_like_abbreviation(word: str) -> bool:
    """A short, fully-uppercase token (GIN, RFD, LC, ALC, ETA, MOP, UOM) is
    almost always a business abbreviation, not a proper noun — real
    supplier/branch names that are all-caps in this data tend to be actual
    words ('TRADERS', 'ENTERPRISES') or multi-word, not a bare 2-5 letter
    acronym. Strip trailing punctuation first so "GIN?" still counts."""
    bare = word.strip("?.,!:;'\"")
    return 2 <= len(bare) <= 5 and bare.isupper()


def _has_mid_sentence_capital(question: str) -> bool:
    """True if any word other than the first is capitalized in a way that
    suggests a proper noun (supplier/branch/customer/file name) — NOT a
    short all-caps abbreviation, which is exempted (see
    _looks_like_abbreviation) so "What is GIN?" / "What is RFD?" still
    resolve as genuine glossary lookups instead of being treated as if
    they named a specific entity."""
    words = question.strip().split()
    return any(
        w[:1].isupper() and not _looks_like_abbreviation(w) for w in words[1:]
    )

# Corrections for entries whose ORIGINAL meaning assumed schema/columns that
# do not exist in the live database. Keyed by the same term text used in
# business_dictionary.json / the synonym map (case-insensitive).
_MEANING_OVERRIDES = {
    "critical item": (
        "High risk-of-stockout item. The live database has no 'Critical' "
        "flag and no ABC rank at all. The closest real signal is the derived "
        "stock status: an item+branch is 'Out of Stock' when available_qty "
        "is at or below zero, and 'Below Reorder' when available_qty is "
        "under its derived reorder level (see Reorder Level). Only the 1,374 "
        "of 6,070 stock rows that have recent requisition demand can be "
        "classified at all."
    ),
    "reorder level": (
        "Minimum stock level before replenishment is needed. Nothing is "
        "stored — the stock.reorder_level column exists but is empty on "
        "every row — so it is derived per item and branch as "
        "avg_daily_demand * lead_time_days * 1.2, where avg_daily_demand is "
        "the last 180 days of store requisition quantity divided by 180, "
        "lead_time_days is that item's average stock-in date minus prepare "
        "date over completed cycles (about 22 days overall, defaulting to 30 "
        "where there is no history), and 1.2 applies the 20% safety factor."
    ),
    "safety stock / days": (
        "Buffer stock to absorb demand and lead-time variability. There is "
        "no safety-stock column and no safety-days column in the live "
        "database. The buffer is applied as a flat 20% safety factor inside "
        "the derived reorder level (the 1.2 multiplier) rather than being "
        "stored or reported as its own quantity."
    ),
    "stock health": (
        "A stock-days coverage concept (how many days of stock remain at the "
        "current usage rate). There is no stock_health column. It is "
        "computed as available_qty divided by average daily issuance over "
        "the last 90 days for that item and branch, and is undefined "
        "(unknown, not zero) for items with no issuance history."
    ),
    "lc": (
        "Letter of Credit — a bank instrument guaranteeing payment to a "
        "foreign supplier. In the live database only the CHOICE of "
        "instrument is recorded, on consignments.payment_instrument ('LC', "
        "'100%LC', 'Advance', 'CAD'). The payments table is empty, so there "
        "is no paid/unpaid status, no retirement date and no bank-charge "
        "data — whether an LC has actually been settled cannot be answered."
    ),
    "alc / elc": (
        "Estimated and Actual Landed Cost per unit — the cost of an imported "
        "item once duty and clearance are included. The live database has "
        "elc and alc columns on consignment_items, but both are empty on "
        "every one of the 161 import lines, so landed cost and its variance "
        "cannot be reported from this data."
    ),
}


@dataclass
class DictionaryEntry:
    term: str
    meaning: str
    department: str | None = None


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _load() -> list[DictionaryEntry]:
    entries: list[DictionaryEntry] = []
    seen_terms: set[str] = set()

    bd_path = _DATA_DIR / "business_dictionary.json"
    if bd_path.exists():
        data = json.loads(bd_path.read_text(encoding="utf-8"))
        for row in data.get("terms", []):
            term = row.get("term", "")
            if not term:
                continue
            key = _normalize(term)
            meaning = _MEANING_OVERRIDES.get(key, row.get("meaning", ""))
            entries.append(DictionaryEntry(term=term, meaning=meaning, department=row.get("department")))
            seen_terms.add(key)

    syn_path = _DATA_DIR / "13_SYNONYM_MAPPING.json"
    if syn_path.exists():
        data = json.loads(syn_path.read_text(encoding="utf-8"))
        for term, row in data.get("synonyms", {}).items():
            key = _normalize(term)
            if key in seen_terms:
                continue  # business_dictionary.json entry already covers this
            meaning = _MEANING_OVERRIDES.get(key, row.get("meaning", ""))
            entries.append(DictionaryEntry(term=term, meaning=meaning, department=row.get("department")))
            seen_terms.add(key)

    # Longest term first so "reorder level" matches before a shorter
    # unrelated substring would.
    entries.sort(key=lambda e: len(e.term), reverse=True)
    return entries


_entries: list[DictionaryEntry] | None = None


def _get_entries() -> list[DictionaryEntry]:
    global _entries
    if _entries is None:
        _entries = _load()
    return _entries


def is_definition_question(question: str) -> bool:
    """True only for a genuine glossary lookup — bare 'what is X' / 'define
    X' with no extra qualifiers. Disqualified by anything suggesting the
    user wants a live figure or names a specific entity (see the module
    docstring for the worked example this guards against)."""
    q = question.strip().lower()
    if not any(re.search(p, q) for p in _DEFINITION_PATTERNS):
        return False
    if _ITEM_CODE_RE.search(q):
        return False
    if any(re.search(t, q) for t in _QUANT_TRIGGERS):
        return False
    if _has_mid_sentence_capital(question):
        return False
    return True


def _match_keys(term: str) -> list[str]:
    """Alternate phrasings a term should also match on.

    A compound term like 'ALC / ELC' or 'Demurrage / Detention' never
    literally appears in a real question — nobody types "what is alc / elc"
    — they ask about just one side ("what is ALC?"). Split on '/' and
    register each side as its own key, alongside the full term, so either
    phrasing resolves to the same entry.
    """
    sides = [p.strip() for p in term.split("/")]
    return [term] + [s for s in sides if s and s != term]


def lookup(question: str) -> DictionaryEntry | None:
    """Find the best-matching glossary entry for a definition-style
    question. Matches on the longest key (the full term, or one side of a
    compound "X / Y" term) that appears as a whole word/phrase in the
    question text."""
    q = _normalize(question)
    candidates: list[tuple[int, DictionaryEntry]] = []
    for entry in _get_entries():
        for key in _match_keys(entry.term):
            key_norm = _normalize(key)
            if key_norm and re.search(rf"(?<!\w){re.escape(key_norm)}(?!\w)", q):
                candidates.append((len(key_norm), entry))
                break  # this entry already matched; don't add it twice
    if not candidates:
        return None
    # Longest matching key wins — e.g. "reorder level" over a shorter,
    # coincidentally-contained alias.
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def format_answer(entry: DictionaryEntry) -> str:
    return entry.meaning.strip()

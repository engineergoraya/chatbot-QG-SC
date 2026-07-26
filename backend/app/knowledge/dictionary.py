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
PLANNED schema, which has since diverged from the live database in a few
places (see app/knowledge/business_rules.py, rule 4) — so those fields are
deliberately NOT surfaced here to avoid contradicting the verified,
live-schema rules used for actual SQL generation. A handful of entries whose
plain-English meaning itself assumed the old design (Critical Item, Reorder
Level, Safety Stock, Stock Health) are explicitly corrected below.
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


def _has_mid_sentence_capital(question: str) -> bool:
    """True if any word other than the first is capitalized — a likely
    proper noun (supplier/branch/customer/file name)."""
    words = question.strip().split()
    return any(w[:1].isupper() for w in words[1:])

# Corrections for entries whose ORIGINAL meaning assumed schema/columns that
# do not exist in the live database. Keyed by the same term text used in
# business_dictionary.json / the synonym map (case-insensitive).
_MEANING_OVERRIDES = {
    "critical item": (
        "High risk-of-stockout item. The live database has no separate "
        "'Critical' flag — the closest real signal is ab_items.rank = 'A' "
        "(the higher tier of an ABC classification), available only for "
        "items stocked at Qadcast (Pvt) Ltd. and Qadri Brothers (Pvt.) Ltd. "
        "(Unit-II), the only two branches ab_items currently covers."
    ),
    "reorder level": (
        "Minimum stock level before replenishment is needed. The live "
        "database has no stored reorder_level column — it is computed live "
        "as branch_daily_usage * (lead_time_days + safety_days), using each "
        "item+branch's own row in ab_items and its own issuance history. "
        "Only computable for the two branches ab_items covers."
    ),
    "safety stock / days": (
        "Buffer stock to absorb demand/lead-time variability. Computed live "
        "as branch_daily_usage * ab_items.safety_days — not a stored total, "
        "and only available for the two branches ab_items covers."
    ),
    "stock health": (
        "A stock-days coverage concept (how many days of stock remain at "
        "the current usage rate). The live database has no dedicated "
        "stock_health column; it must be computed from stock.available_qty "
        "and recent issuance volume for the item/branch in question."
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


def lookup(question: str) -> DictionaryEntry | None:
    """Find the best-matching glossary entry for a definition-style
    question. Matches on the longest dictionary term that appears in the
    question text."""
    q = _normalize(question)
    for entry in _get_entries():
        term_key = _normalize(entry.term)
        if term_key and term_key in q:
            return entry
    return None


def format_answer(entry: DictionaryEntry) -> str:
    return entry.meaning.strip()

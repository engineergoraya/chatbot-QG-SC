"""
entity_resolver.py — deterministic "did you mean" resolution for names the
user types that don't exactly match what is stored.

THE PROBLEM THIS SOLVES. Text-to-SQL writes `WHERE sourcing_o ILIKE
'%Hamza Ahmed%'`, the database stores 'Hamza Ahmad', the query matches
nothing, and the assistant reports "no orders are recorded under this
sourcing officer" — a confident, false statement about someone with 65
orders. The same failure hit 'AB traders' (real: 'Ayyan Traders', 'Al Basit
Traders', 'AN Scrap Traders'). Repeated attempts to fix this with prompt
rules failed, because the model cannot know the stored spelling; only the
database does.

So this runs AFTER a query comes back empty (or a COUNT comes back zero). It
takes the user's question, matches it against the DISTINCT VALUES actually
present in the columns that hold human-typed names, and hands the answer
step a short list of real candidates. The model then says "no one is
recorded as 'Hamza Ahmed'; the closest is 'Hamza Ahmad' with 65 orders"
instead of asserting absence.

Design notes:
  * Read-only and cached. Distinct values per column are loaded once through
    the same SELECT-only connection as everything else.
  * No database extension required — matching is done in Python (difflib),
    so this needs no pg_trgm install and no schema change.
  * Token index first (cheap, catches 'hamza' -> 'Hamza Ahmad'), then a
    fuzzy pass for near-misses with no shared token.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

import psycopg2
import psycopg2.extras

from app import config


@dataclass(frozen=True)
class EntityColumn:
    table: str
    column: str
    label: str  # how to describe it to the user


# The columns that hold human-typed names/labels a user might reasonably
# name in a question. Deliberately NOT every text column — an item
# description or a remark would produce noisy, misleading suggestions.
ENTITY_COLUMNS: list[EntityColumn] = [
    EntityColumn("purchases_data", "supplier", "supplier (local purchases)"),
    EntityColumn("purchases_data", "sourcing_o", "sourcing officer"),
    EntityColumn("purchases_data", "branch", "branch code (purchases)"),
    EntityColumn("suppliers", "name", "supplier (imports)"),
    EntityColumn("issuance", "department", "department"),
    EntityColumn("issuance", "branch", "branch"),
    EntityColumn("store_requisition", "department", "department (requisitions)"),
    EntityColumn("logistics_consignments", "customer_name", "export customer"),
    EntityColumn("logistics_consignments", "department", "logistics business line"),
    EntityColumn("trucking_consignments", "transporter_name", "transporter"),
    EntityColumn("clearing_agents", "name", "clearing agent"),
    EntityColumn("ports", "name", "port"),
]

# Values longer than this are descriptions, not names — skip them.
_MAX_VALUE_LEN = 60
# Per-column cap, so one huge column can't dominate memory or matching time.
_MAX_VALUES_PER_COLUMN = 4000

_STOPWORDS = {
    "how", "many", "much", "what", "which", "who", "the", "are", "is", "in",
    "of", "for", "from", "under", "and", "or", "on", "at", "to", "by", "we",
    "our", "us", "do", "does", "did", "have", "has", "was", "were", "there",
    "all", "any", "show", "tell", "me", "give", "list", "orders", "order",
    "purchase", "purchases", "total", "count", "value", "amount", "please",
    "with", "that", "this", "it", "be", "been", "their", "his", "her",
}

_cache: dict[tuple[str, str], list[str]] | None = None


def _load_values(conn) -> dict[tuple[str, str], list[str]]:
    values: dict[tuple[str, str], list[str]] = {}
    with conn.cursor() as cur:
        for ec in ENTITY_COLUMNS:
            try:
                cur.execute(
                    f"SELECT DISTINCT {ec.column} FROM {ec.table} "
                    f"WHERE {ec.column} IS NOT NULL AND {ec.column} <> '' "
                    f"AND length({ec.column}) <= %s LIMIT %s",
                    (_MAX_VALUE_LEN, _MAX_VALUES_PER_COLUMN),
                )
                values[(ec.table, ec.column)] = [r[0] for r in cur.fetchall()]
            except psycopg2.Error:
                # A table/column that doesn't exist in this load is simply
                # skipped — the resolver is best-effort and must never break
                # the main answer path.
                conn.rollback()
                values[(ec.table, ec.column)] = []
    return values


def _get_cache() -> dict[tuple[str, str], list[str]]:
    global _cache
    if _cache is not None:
        return _cache
    conn = None
    try:
        conn = psycopg2.connect(**config.readonly_dsn())
        conn.set_session(readonly=True, autocommit=False)
        _cache = _load_values(conn)
    except psycopg2.Error:
        _cache = {}
    finally:
        if conn is not None:
            conn.close()
    return _cache


def reset_cache() -> None:
    """Drop the cached values (used by tests, and after a data reload)."""
    global _cache
    _cache = None


def _question_tokens(question: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9&'-]*", question.lower())
    return [w for w in words if len(w) >= 3 and w not in _STOPWORDS]


def _phrases(question: str, max_words: int = 3) -> list[str]:
    """Contiguous 1..max_words word windows of the meaningful tokens, so a
    two-word name like 'hamza ahmed' is compared as a unit, not just as
    separate words."""
    toks = _question_tokens(question)
    out: list[str] = []
    for n in range(1, max_words + 1):
        for i in range(len(toks) - n + 1):
            out.append(" ".join(toks[i : i + n]))
    return out


@dataclass
class Candidate:
    value: str
    label: str
    table: str
    column: str
    score: float


def find_candidates(
    question: str,
    limit: int = 5,
    threshold: float = 0.72,
) -> list[Candidate]:
    """Real stored values that plausibly match a name in the question.

    Returns at most `limit`, best first. Empty when nothing is close enough
    — in that case the caller should say nothing rather than guess.
    """
    if not question or not question.strip():
        return []

    phrases = _phrases(question)
    if not phrases:
        return []
    tokens = set(_question_tokens(question))

    best: dict[str, Candidate] = {}
    for ec in ENTITY_COLUMNS:
        for value in _get_cache().get((ec.table, ec.column), []):
            v_low = value.lower()
            v_tokens = set(re.findall(r"[a-z][a-z0-9&'-]*", v_low))

            # A shared distinctive token is strong evidence on its own
            # ('hamza' -> 'Hamza Ahmad'), and cheap to test.
            shared = tokens & v_tokens
            score = 0.0
            if shared:
                longest = max(len(t) for t in shared)
                score = 0.75 + min(0.2, 0.03 * longest)

            # Fuzzy compare against each phrase window, for near-misses that
            # share no whole token ('ahmed' vs 'ahmad').
            for phrase in phrases:
                ratio = difflib.SequenceMatcher(None, phrase, v_low).ratio()
                if ratio > score:
                    score = ratio

            if score >= threshold:
                prev = best.get(value)
                if prev is None or score > prev.score:
                    best[value] = Candidate(
                        value=value, label=ec.label, table=ec.table,
                        column=ec.column, score=score,
                    )

    ranked = sorted(best.values(), key=lambda c: (-c.score, c.value))
    return ranked[:limit]


def describe_candidates(question: str, limit: int = 5) -> str | None:
    """A prompt-ready note listing the real values that look like what the
    user typed. None when nothing plausible was found."""
    cands = find_candidates(question, limit=limit)
    if not cands:
        return None
    lines = [
        "REAL VALUES IN THE DATABASE that closely match a name in this "
        "question (the user's spelling did not match exactly — these came "
        "from the actual stored data, so they are the likely intent):",
    ]
    for c in cands:
        lines.append(f"  * '{c.value}'  — {c.label} ({c.table}.{c.column})")
    lines.append(
        "State plainly that nothing is recorded under the name the user "
        "typed, then name the closest real value(s) above so they can "
        "confirm. Do NOT claim the entity has no records until the name has "
        "been resolved."
    )
    return "\n".join(lines)

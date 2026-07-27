"""
openai_client.py — thin wrapper around the OpenAI SDK.

Two jobs:
  1. generate_sql(messages)       -> a single SQL string (text-to-SQL), or
                                      the CLARIFY_TIME_PERIOD sentinel line.
  2. explain(question, sql, rows) -> a plain-language answer.

Stateless per call (the LangGraph node layer owns conversation state), so a
single client instance is safe to reuse across concurrent FastAPI requests.

If no API key is configured, `available` is False and the workflow falls
back to a clear "set your key" message instead of crashing.

RESILIENCE: transient failures (429 rate limits, connection errors,
timeouts, 5xx) are retried with exponential backoff (see _call_with_retry).
A persistent failure raises a plain RuntimeError with a clean, honest
message — callers must never let that surface as a raw stack trace to the
end user (app/graph/nodes.py catches it and produces the "could not
generate a query" / "couldn't build a safe query" fallback answers).
"""

from __future__ import annotations

import re
import time

from app import config
from app.knowledge.business_rules import RESPONSE_STYLE

try:
    from openai import (
        OpenAI,
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
    _SDK = True
    _TRANSIENT_EXC = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
except Exception:  # SDK not installed yet
    _SDK = False
    _TRANSIENT_EXC = ()


_FENCE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

_RETRY_ATTEMPTS = 4          # matches spec: 4 attempts total
_RETRY_DELAYS = (2, 4, 8)    # seconds between attempts 1->2, 2->3, 3->4

# A short reminder re-stated right next to the generation call (not just
# buried in the long system prompt). Two rules from business_rules.py are
# easy for the model to skip on some phrasings even though they're already
# in the system prompt — restating them here, close to the actual
# instruction, measurably improves compliance:
#   - rule 14 (time window): "What did Production consume?" / "Which
#     suppliers are delayed?" are both aggregate-over-a-dated-table
#     questions with no period named, and must ask via CLARIFY_TIME_PERIOD
#     rather than silently defaulting to an all-time total.
#   - rule 5 (UOM on stock quantities): a stock quantity without its unit
#     ("5,462.20" vs "5,462.20 kg") is easy to drop when the join to items
#     feels optional.
#
# IMPORTANT: there are TWO variants. A fresh question gets the full
# reminder (including "ask for a period if none is stated"). A question
# that is ITSELF the reply to a pending time-period clarification (see
# main.py's pairing wrapper) must NOT get that same instruction restated —
# doing so previously caused a real regression: the model would re-trigger
# CLARIFY_TIME_PERIOD on the reply turn instead of resolving it, because the
# blunt "no period stated -> ask" restatement outweighed rule 14's own
# (correct) handling of the paired-reply case earlier in the system prompt.
# The follow-up variant instead explicitly tells the model the period is
# already resolved, so it must proceed straight to SQL.
_GENERATION_REMINDER_FRESH = (
    "\n\nBefore answering: (1) if this aggregates/counts/ranks a dated "
    "transaction table (purchases_data, issuance, import_details/"
    "shipment_details, exports/export_shipments, store_requisition, "
    "shifting_movements) and no time period is stated anywhere in the "
    "question, output ONLY the CLARIFY_TIME_PERIOD line per rule 14 — do "
    "not silently assume all-time. (2) if this is a STOCK quantity "
    "question, join items and include items.uom on every quantity per "
    "rule 5."
)
_GENERATION_REMINDER_FOLLOWUP = (
    "\n\nThis message is the user's reply to your own earlier "
    "CLARIFY_TIME_PERIOD question — a time period has now been resolved "
    "(either a real period, or a decline meaning 'use the 6-month "
    "default'). Do NOT output CLARIFY_TIME_PERIOD again for this turn; "
    "write the actual SQL now, filtered to that resolved period (per rule "
    "14's reply-handling). Separately, if this is a STOCK quantity "
    "question, join items and include items.uom on every quantity per "
    "rule 5."
)


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text).strip()


def _is_transient(exc: Exception) -> bool:
    return _SDK and isinstance(exc, _TRANSIENT_EXC)


def _call_with_retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs), retrying transient errors (429 rate limits,
    connection errors, timeouts, 5xx) with exponential backoff. Any other
    exception propagates immediately. Once retries are exhausted, raises a
    clean RuntimeError instead of the raw SDK error."""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not _is_transient(exc):
                raise
            if attempt == _RETRY_ATTEMPTS - 1:
                raise RuntimeError(
                    "The AI service is temporarily unavailable after several retries. "
                    "Please try again shortly."
                ) from exc
            time.sleep(_RETRY_DELAYS[attempt])


class OpenAIClient:
    def __init__(self) -> None:
        self.available = bool(_SDK and config.openai_ready())
        self._client = OpenAI(api_key=config.OPENAI_API_KEY) if self.available else None

    def _chat(self, messages: list, temperature: float) -> str:
        resp = _call_with_retry(
            self._client.chat.completions.create,
            model=config.OPENAI_MODEL,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    # -- SQL generation -------------------------------------------------
    def generate_sql(
        self,
        system_prompt: str,
        history: list[dict],
        question: str,
        is_clarification_reply: bool = False,
    ) -> str:
        """Ask the model for a single SQL query answering `question`.

        `history` is the prior turns of THIS session's SQL-generation
        conversation (role/content dicts), so multi-turn follow-ups and the
        time-period clarification flow keep context.

        `is_clarification_reply` must be True when `question` is itself the
        pending-question/reply pairing built for rule 14's follow-up turn
        (see app/main.py) — it switches to a reminder that tells the model
        the period is already resolved, instead of the fresh-question
        reminder that would otherwise re-trigger a second clarification.
        """
        if not self.available:
            raise RuntimeError("OpenAI unavailable (no API key configured).")
        reminder = _GENERATION_REMINDER_FOLLOWUP if is_clarification_reply else _GENERATION_REMINDER_FRESH
        messages = [{"role": "system", "content": system_prompt}] + history + [
            {
                "role": "user",
                "content": (
                    "Write ONE PostgreSQL SELECT query that answers the question below. "
                    "Return ONLY the SQL — no explanation, no markdown fences.\n\n"
                    f"Question: {question}" + reminder
                ),
            }
        ]
        text = self._chat(messages, temperature=0)
        return _strip_fences(text)

    def repair_sql(self, system_prompt: str, history: list[dict], error: str) -> str:
        if not self.available:
            raise RuntimeError("OpenAI unavailable (no API key configured).")
        messages = [{"role": "system", "content": system_prompt}] + history + [
            {
                "role": "user",
                "content": (
                    "The previous query was rejected or failed with this error:\n"
                    f"{error}\n\n"
                    "Return ONE corrected PostgreSQL SELECT query. ONLY the SQL."
                ),
            }
        ]
        text = self._chat(messages, temperature=0)
        return _strip_fences(text)

    # -- Explanation ------------------------------------------------------
    def explain(self, question: str, sql: str, result_preview: str) -> str:
        if not self.available:
            raise RuntimeError("OpenAI unavailable.")
        messages = [
            {
                "role": "system",
                "content": (
                    "You explain live database query results to Qadri Group "
                    "supply-chain staff.\n\n" + RESPONSE_STYLE
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User question: {question}\n\n"
                    f"SQL run:\n{sql}\n\n"
                    f"Result (rows):\n{result_preview}\n\n"
                    "Write the answer now."
                ),
            },
        ]
        text = self._chat(messages, temperature=0.2)
        return text.strip()


# Module-level singleton — the SDK client itself holds no per-conversation
# state, so one instance safely serves concurrent requests.
_client: OpenAIClient | None = None


def get_client() -> OpenAIClient:
    global _client
    if _client is None:
        _client = OpenAIClient()
    return _client

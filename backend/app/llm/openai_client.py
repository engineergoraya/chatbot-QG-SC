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

import json
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
    "\n\nBefore answering: (1) rule 14 applies ONLY to a HISTORICAL "
    "total/average/ranking over a date RANGE on a dated transaction table "
    "(e.g. 'total purchases', 'average delay', 'what did X consume') where "
    "no period is stated — for those, output ONLY the CLARIFY_TIME_PERIOD "
    "line, do not silently assume all-time. Rule 14 does NOT apply to a "
    "CURRENT status/snapshot question — 'how many are on water/in transit "
    "right now', 'which items need reorder', 'which items are critical', "
    "'when should we reorder/order next' (a projection off CURRENT stock + "
    "historical usage rate, see rule 4's projected-reorder-date formula), "
    "any stock-level question — those describe the PRESENT state and have "
    "no time period to ask for at all; answer them directly with SQL, "
    "never CLARIFY_TIME_PERIOD. If genuinely unsure which kind this is, "
    "prefer answering directly over asking. (2) if this is a STOCK "
    "quantity question, join items and include items.uom on every "
    "quantity per rule 5. (3) per rule 17b, if this asks HOW MANY / "
    "WHICH / LIST over identifiable RECORDS (shipments, items, POs, "
    "suppliers, requisitions, jobs), do NOT return a bare COUNT(*) — "
    "SELECT the useful identifying columns for those records plus "
    "`COUNT(*) OVER () AS total_matching_rows`, so the reader gets the "
    "actual list and the true total. Keep a plain aggregate ONLY for a "
    "scalar money/quantity measure (SUM/AVG of value, an average of "
    "days)."
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

# Second sentinel escape (alongside CLARIFY_TIME_PERIOD): some messages in a
# real conversation aren't data questions at all — "what did I ask you
# first?", "explain that more simply", "thanks". Without this, EVERY message
# was forced down the SQL path, so a meta-question got answered with a
# confidently-wrong data figure (observed: "What did I ask you first?"
# returned an in-transit count). The graph routes this sentinel to
# answer_conversationally(), which uses the transcript instead of the DB.
_CONVERSATIONAL_ESCAPE = (
    "\n\nSEPARATE ESCAPE HATCH: if this message is not a request for "
    "supply-chain DATA at all — i.e. it asks about THIS CONVERSATION "
    "(\"what did I ask you first?\", \"summarise what we discussed\"), asks "
    "you to rephrase/re-explain something you ALREADY answered (\"explain "
    "that more simply\", \"why does that matter?\"), or is pure "
    "pleasantry (\"thanks\") — then output ONLY the single line "
    "`CONVERSATIONAL:` and nothing else. Do NOT write SQL for those. This "
    "does NOT apply to a follow-up that genuinely needs NEW data (\"what "
    "about the ones awaiting sailing?\", \"and their total value?\") — "
    "those are real data questions; write SQL for them as normal."
)

# Third sentinel escape: a genuine forecasting/projection intent ("forecast
# demand for X", "how much will we need next quarter", "projected demand",
# "when will we run out of Y"). Per business_rules.py rule 13, this system
# has no forecasting model of its own for the LLM to reason with in SQL —
# app/analytics/forecasting.py does the actual maths, deterministically, on
# a plain historical series. This escape hatch is how the two meet: the
# model still writes ONE ordinary SELECT (so it goes through the SAME guard
# + executor as any other query — no new write path, no relaxed guard), but
# aggregated to one row per period rather than a final analytical answer.
_FORECAST_ESCAPE = (
    "\n\nTHIRD ESCAPE HATCH: if this question asks to FORECAST/PROJECT future "
    "demand, consumption, or reorder timing (\"forecast demand for X\", \"how "
    "much will we need next quarter\", \"projected demand for Y\", \"when will "
    "we run out of Z\") — do NOT write a final analytical answer yourself. "
    "Instead:\n"
    "  1. Resolve the target item per rule 5 (exact item_code, or item+specs, "
    "including the punctuation-insensitive grade/code matching rule 5 "
    "describes) and branch if named.\n"
    "  2. Write ONE SELECT returning that item's HISTORICAL monthly activity "
    "as a plain two-column time series, grouped by calendar month and "
    "aliased EXACTLY `period` and `value` (nothing else on those two "
    "columns) — e.g. for issuance/consumption history:\n"
    "       SELECT date_trunc('month', i.from_date)::date AS period, "
    "SUM(i.quantity) AS value\n"
    "       FROM issuance i JOIN items it ON i.item_code = it.item_code\n"
    "       WHERE <item filter> AND i.status NOT IN ('HoldIssuence', 'Hold')\n"
    "       GROUP BY period ORDER BY period\n"
    "     Use purchases_data instead of issuance if the question is about "
    "purchase/procurement volume rather than consumption.\n"
    "  3. Prefix your ENTIRE response with exactly one line before that SQL "
    "(nothing else on that line):\n"
    "       FORECAST: horizon=<n>\n"
    "     where <n> is how many future periods the user asked for (default "
    "3 if unspecified). The SQL follows on the next line(s).\n"
    "This does NOT apply to a plain historical-trend or current-reorder-"
    "level question with no forward projection asked for — those still get "
    "a normal analytical SQL answer as usual."
)

CONVERSATIONAL_PREFIX = "CONVERSATIONAL:"


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
                    f"Question: {question}" + reminder + _CONVERSATIONAL_ESCAPE + _FORECAST_ESCAPE
                ),
            }
        ]
        text = self._chat(messages, temperature=0)
        return _strip_fences(text)

    def answer_conversationally(self, transcript: list[dict], question: str) -> str:
        """Answer a question ABOUT the conversation (not about the data)
        using only the human-facing transcript — no SQL, no DB access.

        Used for the CONVERSATIONAL sentinel path: "what did I ask you
        first?", "explain your last answer more simply", "thanks". These
        previously fell through to SQL generation, which produced a
        confidently-wrong data answer to a question that wasn't about data
        at all.
        """
        if not self.available:
            raise RuntimeError("OpenAI unavailable (no API key configured).")
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the Qadri Group supply-chain assistant, mid-conversation "
                    "with a user. The messages that follow are the real conversation so "
                    "far. Answer the user's latest message using ONLY that conversation "
                    "history — it is a question about the conversation itself (or a "
                    "request to rephrase/clarify something you already said), not a "
                    "request for new data.\n\n"
                    "Rules: never invent a figure that does not appear earlier in the "
                    "conversation. If the answer genuinely isn't in the history above, "
                    "say so plainly and invite them to ask the data question directly. "
                    "Keep it to 3-4 lines, professional and direct."
                ),
            }
        ] + transcript + [{"role": "user", "content": question}]
        return self._chat(messages, temperature=0.2).strip()

    def repair_sql(
        self,
        system_prompt: str,
        history: list[dict],
        error: str,
        question: str,
        failed_sql: str,
    ) -> str:
        """Ask for a corrected query. `question` and `failed_sql` are
        REQUIRED, not optional context: `history` is frequently empty (a
        session's first turn, or right after a clarify-only turn carries no
        prior exchange), and without restating what was actually being
        asked, a repair call has nothing to ground a correction in except
        the bare error text — which previously caused the model to invent
        an entirely unrelated but syntactically-valid query (e.g. a plain
        `SELECT * FROM ab_items` for a "what did Production consume"
        question) instead of fixing the real one. Always pass the real
        values; never call this with a placeholder.
        """
        if not self.available:
            raise RuntimeError("OpenAI unavailable (no API key configured).")
        messages = [{"role": "system", "content": system_prompt}] + history + [
            {
                "role": "user",
                "content": (
                    f"You were asked: {question}\n\n"
                    f"You wrote this query:\n{failed_sql}\n\n"
                    f"It was rejected or failed with this error:\n{error}\n\n"
                    "Return ONE corrected PostgreSQL SELECT query that still "
                    "answers the ORIGINAL question above. ONLY the SQL."
                ),
            }
        ]
        text = self._chat(messages, temperature=0)
        return _strip_fences(text)

    # -- Forecast explanation ----------------------------------------------
    def explain_forecast(
        self,
        question: str,
        result: dict,
        transcript: list[dict] | None = None,
    ) -> str:
        """Turn an app.analytics.forecasting.forecast_series() result into
        the user-facing answer. Mirrors explain(), but the source of truth
        is the forecast dict (method/forecast/confidence_interval/reason),
        never the raw historical rows — the model reports what the
        deterministic forecasting engine already computed, it does not
        recompute or estimate a number itself."""
        if not self.available:
            raise RuntimeError("OpenAI unavailable.")
        messages = [
            {
                "role": "system",
                "content": (
                    "You explain a demand forecast to Qadri Group supply-chain "
                    "staff.\n\n" + RESPONSE_STYLE + "\n"
                    "- The forecast JSON below is the ONLY source for any number, "
                    "method name, or confidence claim — never invent or adjust a "
                    "figure yourself, and never silently switch to a different "
                    "unit/timeframe than what's given.\n"
                    "- If `ok` is false, say plainly that there isn't enough "
                    "history to forecast this reliably, state the `reason` given, "
                    "and do NOT report a number as if it were a real projection.\n"
                    "- If `ok` is true, state the projected figure(s) for the "
                    "requested horizon, name the method chosen (e.g. 'Holt-"
                    "Winters', 'Croston' — Croston means intermittent/spare-part-"
                    "style demand, mention that plainly when intermittent_demand "
                    "is true), and give the confidence interval and confidence "
                    "label in one plain sentence — this is a projection from "
                    "historical data, not a guarantee, and the answer should read "
                    "that way.\n"
                    "- Earlier turns may be shown before the current question, for "
                    "CONTINUITY only."
                ),
            },
            *(transcript or []),
            {
                "role": "user",
                "content": (
                    f"User question: {question}\n\n"
                    f"Forecast result:\n{json.dumps(result, default=str, ensure_ascii=False)}\n\n"
                    "Write the answer now."
                ),
            },
        ]
        text = self._chat(messages, temperature=0.2)
        return text.strip()

    # -- Explanation ------------------------------------------------------
    def explain(
        self,
        question: str,
        sql: str,
        result_preview: str,
        transcript: list[dict] | None = None,
    ) -> str:
        """Turn the query result into the user-facing answer.

        `transcript` is the prior human-facing conversation. It's included
        so the answer reads as a continuation rather than an isolated
        response — e.g. a follow-up can say "that's 34 fewer than the 55 in
        transit above" instead of repeating context the user already has.
        The result rows remain the ONLY source for new figures (see the
        instruction below): prior turns give continuity, never data.
        """
        if not self.available:
            raise RuntimeError("OpenAI unavailable.")
        messages = [
            {
                "role": "system",
                "content": (
                    "You explain live database query results to Qadri Group "
                    "supply-chain staff.\n\n" + RESPONSE_STYLE + "\n"
                    "- Earlier turns of this conversation may be shown before the "
                    "current question, for CONTINUITY only (so you can refer back "
                    "naturally to what was already discussed). Every NUMBER you "
                    "state about the current question must come from the result "
                    "rows below — never carry a figure over from an earlier turn "
                    "as if it answered this one."
                ),
            },
            *(transcript or []),
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

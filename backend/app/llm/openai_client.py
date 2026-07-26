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
"""

from __future__ import annotations

import re
import time

from app import config
from app.knowledge.business_rules import RESPONSE_STYLE

try:
    from openai import OpenAI, RateLimitError
    _SDK = True
except Exception:  # SDK not installed yet
    _SDK = False


_FENCE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_BASE_DELAY = 2  # seconds; doubles each retry


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text).strip()


def _is_rate_limited(exc: Exception) -> bool:
    return _SDK and isinstance(exc, RateLimitError)


def _call_with_retry(fn, *args, **kwargs):
    delay = _RATE_LIMIT_BASE_DELAY
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not _is_rate_limited(exc):
                raise
            if attempt == _RATE_LIMIT_RETRIES:
                raise RuntimeError(
                    "The AI is rate-limited right now. Wait a minute and try again."
                ) from exc
            time.sleep(delay)
            delay *= 2


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
    def generate_sql(self, system_prompt: str, history: list[dict], question: str) -> str:
        """Ask the model for a single SQL query answering `question`.

        `history` is the prior turns of THIS session's SQL-generation
        conversation (role/content dicts), so multi-turn follow-ups and the
        time-period clarification flow keep context.
        """
        if not self.available:
            raise RuntimeError("OpenAI unavailable (no API key configured).")
        messages = [{"role": "system", "content": system_prompt}] + history + [
            {
                "role": "user",
                "content": (
                    "Write ONE PostgreSQL SELECT query that answers the question below. "
                    "Return ONLY the SQL — no explanation, no markdown fences.\n\n"
                    f"Question: {question}"
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

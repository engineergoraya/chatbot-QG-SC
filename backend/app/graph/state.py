"""
state.py — the shared state object threaded through the LangGraph workflow.

One graph invocation answers ONE question. Multi-turn memory (the SQL
generation message history, and a pending time-period clarification) is
owned by the caller (a per-session store in app/main.py) and passed in/out
as plain fields here — the graph itself is stateless across calls.
"""

from __future__ import annotations

from typing import TypedDict


class ChatState(TypedDict, total=False):
    # -- input --
    original_question: str      # exactly what the user typed this turn
    llm_question: str           # what's actually sent to the SQL-gen model
                                 # (may be original_question wrapped with
                                 # clarification context — see nodes.py)
    history: list[dict]         # prior SQL-gen turns for this session
    transcript: list[dict]      # prior human-facing Q&A turns (see
                                 # session_store.SessionData.transcript)
    is_clarification_reply: bool  # True when llm_question is a pending-
                                   # question/reply pairing (see main.py) —
                                   # changes which reminder generate_sql uses
                                   # so it doesn't re-trigger rule 14

    # -- understand_question --
    is_definition: bool
    dictionary_answer: str | None

    # -- retrieve_business_context --
    system_prompt: str
    rag_context: str

    # -- generate_sql --
    sql: str | None
    needs_clarification: bool
    clarifying_question: str | None
    is_conversational: bool     # True when the question is about the
                                 # CONVERSATION itself ("what did I ask
                                 # first?", "explain that more simply") and
                                 # needs the transcript, not a SQL query
    is_forecast: bool           # True when this is a forecasting intent —
                                 # `sql` is a plain historical-series SELECT
                                 # (columns aliased period/value), executed
                                 # through the SAME guard/executor as any
                                 # other query; only the answer stage
                                 # branches to forecast_answer instead of
                                 # generate_answer (see workflow.py)
    forecast_horizon: int        # periods ahead requested (see nodes.py's
                                  # FORECAST: sentinel parsing)

    # -- validate_sql --
    guard_ok: bool
    safe_sql: str | None
    guard_reason: str | None

    # -- execute_sql --
    exec_ok: bool
    columns: list[str] | None
    rows: list[dict] | None
    exec_error: str | None
    row_count: int
    truncated: bool

    # -- generate_answer / forecast_answer / control --
    answer: str | None
    confidence: float
    repair_count: int
    give_up: bool
    new_history: list[dict]     # history to persist back into the session
    done_reason: str            # "dictionary" | "clarify" | "guard_failed" |
                                 # "exec_failed" | "empty" | "answered" |
                                 # "conversational" | "forecast" | "error"
    forecast_result: dict | None  # app.analytics.forecasting.forecast_series()
                                   # output, or an {"ok": False, "reason": ...}
                                   # dict — see nodes.forecast_answer

"""
nodes.py — the node functions for the single LangGraph workflow.

Flow for one question (see workflow.py for the edges):

    understand_question
      -> [pure definition, glossary hit]  -> dictionary_answer_node -> END
      -> retrieve_business_context
      -> generate_sql
           -> [no OpenAI key]             -> END (clear "not configured" msg)
           -> [asks for a time period]    -> END (clarifying question)
           -> validate_sql
                -> [rejected]  -> repair_sql -> validate_sql (loop, capped)
                -> [ok] -> execute_sql
                     -> [db error] -> repair_sql -> validate_sql (loop, capped)
                     -> [0 rows]   -> END (plain "no matching rows" message)
                     -> [ok]       -> generate_answer -> END

Everything degrades gracefully: no OpenAI key, guard rejection, empty
result, or DB error each produce a clear sentence instead of a crash or a
made-up number (never hallucinate — see the FastAPI spec's error-handling
requirement).
"""

from __future__ import annotations

import json
import re

from app.db import executor
from app.db.introspect import introspect
from app.graph import confidence, guard
from app.graph.state import ChatState
from app.knowledge import coverage, dictionary
from app.knowledge.business_rules import build_system_prompt
from app.knowledge.rag import get_index
from app.llm.openai_client import CONVERSATIONAL_PREFIX, get_client

_CLARIFY_PREFIX = "CLARIFY_TIME_PERIOD:"
_DEFAULT_CLARIFY_TEXT = (
    "For what time period should I calculate this? (e.g. 3 months, 6 months, 1 year)"
)
_MAX_REPAIRS = 2  # matches: one repair attempt on guard rejection + one on DB error

_FORECAST_PREFIX = "FORECAST:"
_FORECAST_HORIZON_RE = re.compile(r"horizon\s*=\s*(\d+)", re.IGNORECASE)
_DEFAULT_FORECAST_HORIZON = 3
_MAX_FORECAST_HORIZON = 24


def _preview(columns, rows, max_rows_shown: int = 30) -> str:
    total = len(rows or [])
    shown = rows[:max_rows_shown] if rows else []
    payload = {"columns": columns, "row_count": total, "rows": shown}
    if total > len(shown):
        # Without this, explain() only sees `len(shown)` actual row dicts and
        # can misread the situation as data being cut off / unavailable — the
        # user already sees every one of the `total` rows in the UI table (see
        # RESPONSE_STYLE's guidance on this exact note).
        payload["note"] = (
            f"This is a sample of {len(shown)} rows for your own reasoning only. "
            f"The full {total} rows are already shown to the user in a table in "
            f"the app UI — never say the data is incomplete or unavailable."
        )
    return json.dumps(payload, default=str, ensure_ascii=False)


def _fallback_answer(columns, rows, row_count) -> str:
    """If the explain call fails, present the result plainly — never invent."""
    if row_count == 1 and columns and len(columns) == 1:
        col = columns[0]
        return f"{col}: {rows[0][col]}"
    head = ", ".join(columns or [])
    return f"Returned {row_count} row(s) with columns: {head}."


def understand_question(state: ChatState) -> ChatState:
    """Lightweight intent check: is this a pure glossary/definition question
    that never needs SQL at all? (Doc 11: 'Definition questions never hit
    the database'.)"""
    question = state["original_question"]
    is_definition = dictionary.is_definition_question(question)
    entry = dictionary.lookup(question) if is_definition else None
    return {
        **state,
        "is_definition": is_definition and entry is not None,
        "dictionary_answer": dictionary.format_answer(entry) if entry else None,
    }


def dictionary_answer_node(state: ChatState) -> ChatState:
    return {
        **state,
        "answer": state["dictionary_answer"],
        "confidence": confidence.DICTIONARY_HIT,
        "done_reason": "dictionary",
    }


def retrieve_business_context(state: ChatState) -> ChatState:
    """Build the system prompt: live schema + verified business rules
    (authoritative), plus a small amount of subordinate reference material
    retrieved from the original knowledge PDFs for extra terminology
    context. The verified rules always win on any conflict — see
    app/knowledge/rag.py's module docstring for why."""
    schema = introspect()
    system_prompt = build_system_prompt(schema)

    rag_chunks = get_index().retrieve(state["original_question"], k=2)
    rag_text = get_index().format_for_prompt(rag_chunks)
    if rag_text:
        system_prompt += (
            "\n\nSUPPLEMENTARY REFERENCE MATERIAL (background/terminology context "
            "only — describes the ORIGINAL planned design, not necessarily the "
            "live schema; if it conflicts with the VERIFIED BUSINESS RULES or "
            "LIVE DATABASE SCHEMA above, those win, not this):\n\n" + rag_text
        )

    return {**state, "system_prompt": system_prompt, "rag_context": rag_text}


def generate_sql(state: ChatState) -> ChatState:
    client = get_client()
    if not client.available:
        return {
            **state,
            "answer": (
                "The AI assistant is not configured yet: no OpenAI API key was "
                "found. Add OPENAI_API_KEY to backend/.env, then restart the "
                "server. (The database and safety guard are ready.)"
            ),
            "confidence": confidence.CONFIG_OR_GENERATION_ERROR,
            "done_reason": "error",
            "sql": None,
            "needs_clarification": False,
        }

    history = state.get("history") or []
    try:
        sql = client.generate_sql(
            state["system_prompt"],
            history,
            state["llm_question"],
            is_clarification_reply=state.get("is_clarification_reply", False),
        )
    except Exception as e:
        return {
            **state,
            "answer": f"Could not generate a query for that question ({e}).",
            "confidence": confidence.CONFIG_OR_GENERATION_ERROR,
            "done_reason": "error",
            "sql": None,
            "needs_clarification": False,
        }

    if sql.strip().startswith(_CLARIFY_PREFIX):
        clarification = sql.strip()[len(_CLARIFY_PREFIX):].strip() or _DEFAULT_CLARIFY_TEXT
        return {
            **state,
            "sql": None,
            "needs_clarification": True,
            "clarifying_question": clarification,
            "answer": clarification,
            "confidence": confidence.CLARIFICATION_NEEDED,
            "done_reason": "clarify",
            "new_history": history,  # nothing durable to add yet
        }

    if sql.strip().startswith(CONVERSATIONAL_PREFIX):
        # Not a data question — answered from the transcript instead.
        return {**state, "sql": None, "is_conversational": True, "needs_clarification": False}

    if sql.strip().startswith(_FORECAST_PREFIX):
        # First line carries the sentinel + params; everything after it is a
        # normal SELECT (columns aliased period/value — see the reminder in
        # openai_client.py) that goes through the SAME guard/executor as any
        # other query. Only the answer stage branches (see workflow.py).
        first_line, _, rest = sql.strip().partition("\n")
        horizon_match = _FORECAST_HORIZON_RE.search(first_line)
        horizon = _DEFAULT_FORECAST_HORIZON
        if horizon_match:
            horizon = max(1, min(int(horizon_match.group(1)), _MAX_FORECAST_HORIZON))
        forecast_sql = rest.strip()
        if not forecast_sql:
            return {
                **state,
                "answer": "Could not generate a query for that forecast — no SQL followed the FORECAST directive.",
                "confidence": confidence.CONFIG_OR_GENERATION_ERROR,
                "done_reason": "error",
                "sql": None,
                "needs_clarification": False,
            }
        return {
            **state,
            "sql": forecast_sql,
            "is_forecast": True,
            "forecast_horizon": horizon,
            "needs_clarification": False,
            "repair_count": state.get("repair_count", 0),
        }

    return {**state, "sql": sql, "needs_clarification": False, "repair_count": state.get("repair_count", 0)}


def conversational_answer(state: ChatState) -> ChatState:
    """Answer a question about the CONVERSATION (not the data) from the
    human-facing transcript. No SQL, no DB access."""
    client = get_client()
    transcript = state.get("transcript") or []
    try:
        answer = client.answer_conversationally(transcript, state["original_question"])
    except Exception:
        answer = (
            "I couldn't reconstruct that from our conversation so far. "
            "Try asking the supply-chain question directly."
        )
    return {
        **state,
        "answer": answer,
        "confidence": confidence.CONVERSATIONAL,
        "done_reason": "conversational",
    }


def validate_sql(state: ChatState) -> ChatState:
    schema = introspect()
    result = guard.validate(
        state["sql"], schema, question=state.get("original_question")
    )
    return {
        **state,
        "guard_ok": result.ok,
        "safe_sql": result.safe_sql,
        "guard_reason": result.reason,
    }


def execute_sql(state: ChatState) -> ChatState:
    outcome = executor.run_safe_query(state["safe_sql"])
    if not outcome.ok:
        return {
            **state,
            "exec_ok": False,
            "exec_error": outcome.error,
            "columns": None,
            "rows": None,
            "row_count": 0,
            "truncated": False,
        }

    if outcome.row_count == 0:
        return {
            **state,
            "exec_ok": True,
            "columns": outcome.columns,
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "answer": (
                "The query ran successfully but returned no matching rows. "
                "That does NOT necessarily mean the item/supplier/branch is "
                "absent from the database — most often the filters were too "
                "narrow, or the query required a table that only covers part "
                "of the catalogue (e.g. `stock` has no row for about two "
                "thirds of the items that have issuance history). Try naming "
                "the item slightly differently, or ask about its usage/"
                "purchase history instead."
            ),
            "confidence": confidence.EMPTY_RESULT,
            "done_reason": "empty",
        }

    return {
        **state,
        "exec_ok": True,
        "columns": outcome.columns,
        "rows": outcome.rows,
        "row_count": outcome.row_count,
        "truncated": outcome.truncated,
    }


def repair_sql(state: ChatState) -> ChatState:
    repair_count = state.get("repair_count", 0) + 1
    if repair_count > _MAX_REPAIRS:
        reason = state.get("guard_reason") if not state.get("guard_ok") else state.get("exec_error")
        return {
            **state,
            "repair_count": repair_count,
            "give_up": True,
            "answer": (
                "I couldn't build a safe, correct query for that question after "
                f"a couple of attempts (last issue: {reason or 'unknown error'}). "
                "Try rephrasing the question."
            ),
            "confidence": confidence.GIVE_UP,
            "done_reason": "guard_failed" if not state.get("guard_ok") else "exec_failed",
        }

    client = get_client()
    error = state.get("guard_reason") if not state.get("guard_ok") else state.get("exec_error")
    history = state.get("history") or []
    try:
        new_sql = client.repair_sql(
            state["system_prompt"],
            history,
            error or "Unknown error.",
            question=state["llm_question"],
            failed_sql=state.get("sql") or "(no SQL was produced)",
        )
    except Exception:
        return {
            **state,
            "repair_count": repair_count,
            "give_up": True,
            "answer": "I couldn't build a safe query for that. Try rephrasing the question.",
            "confidence": confidence.GIVE_UP,
            "done_reason": "guard_failed" if not state.get("guard_ok") else "exec_failed",
        }

    return {
        **state,
        "sql": new_sql,
        "repair_count": repair_count,
        "give_up": False,
        # reset downstream flags so this loops back through validate cleanly
        "guard_ok": False,
        "exec_ok": False,
    }


def generate_answer(state: ChatState) -> ChatState:
    client = get_client()
    preview = _preview(state["columns"], state["rows"])
    # A verified coverage gap (app/knowledge/coverage.py) means part of what
    # was asked cannot be reported from this data at all. The guard already
    # kept the empty domain out of the SQL; this makes the ANSWER say why,
    # instead of trailing off into "the query did not provide that".
    gap_note = coverage.note_for_question(state.get("original_question") or "")
    question_for_answer = state["llm_question"]
    if gap_note:
        question_for_answer = f"{question_for_answer}\n\n{gap_note}"
    try:
        answer = client.explain(
            question_for_answer,
            state["safe_sql"],
            preview,
            transcript=state.get("transcript") or [],
        )
    except Exception:
        answer = _fallback_answer(state["columns"], state["rows"], state["row_count"])

    if state.get("truncated"):
        from app import config
        answer += f"\n\n(Showing the first {config.MAX_ROWS} rows; there may be more.)"

    repairs = state.get("repair_count", 0)
    confidence_value = confidence.for_successful_answer(repairs)

    history = state.get("history") or []
    new_history = history + [
        {"role": "user", "content": state["llm_question"]},
        {"role": "assistant", "content": state["safe_sql"]},
    ]

    return {
        **state,
        "answer": answer,
        "confidence": confidence_value,
        "done_reason": "answered",
        "new_history": new_history,
    }


def forecast_answer(state: ChatState) -> ChatState:
    """Turn the historical-series query result into a forecast.

    `rows` came from the SAME guard-validated, read-only SELECT as any other
    query (see generate_sql's FORECAST: sentinel handling and workflow.py's
    routing) — this node only shapes those rows into a series and hands them
    to the deterministic forecasting engine; it never writes SQL or touches
    the DB itself. The LLM's only remaining job (explain_forecast) is to
    describe the forecast dict in plain English, not to compute anything.
    """
    from app.analytics.forecasting import forecast_series
    from app.analytics.series import build_series

    horizon = state.get("forecast_horizon") or _DEFAULT_FORECAST_HORIZON
    try:
        series = build_series(state["rows"], "period", "value", freq="monthly")
        result = forecast_series(series, horizon=horizon)
    except Exception as e:
        result = {
            "ok": False,
            "reason": f"Could not build a forecast from the query result ({e}).",
        }

    client = get_client()
    try:
        answer = client.explain_forecast(
            state["llm_question"], result, transcript=state.get("transcript") or []
        )
    except Exception:
        if result.get("ok"):
            answer = (
                f"Projected {result['method']} forecast for the next {result['horizon']} "
                f"period(s): {result['forecast']} (confidence: {result['confidence']})."
            )
        else:
            answer = result.get("reason", "Not enough history to forecast this reliably.")

    history = state.get("history") or []
    new_history = history + [
        {"role": "user", "content": state["llm_question"]},
        {"role": "assistant", "content": state["safe_sql"]},
    ]

    return {
        **state,
        "forecast_result": result,
        "answer": answer,
        "confidence": confidence.for_forecast(result),
        "done_reason": "forecast",
        "new_history": new_history,
    }

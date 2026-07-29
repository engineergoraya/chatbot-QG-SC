"""
main.py — FastAPI entry point.

Exposes:
  POST /api/chat          — the chatbot endpoint (see schemas.ChatRequest/Response)
  POST /api/chat/stream    — same thing, streamed as Server-Sent Events
  GET  /api/health          — liveness + DB/LLM readiness check

Session memory (SQL-generation history, and a pending time-period
clarification question) lives behind app/session_store.py's SessionStore
interface — in-memory by default, Redis when REDIS_URL is set. See that
module for details.
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import config
from app.db.introspect import introspect
from app.graph.workflow import get_graph
from app.knowledge.rag import get_index
from app.llm.openai_client import get_client
from app.logging_config import log_request
from app.schemas import ChatRequest, ChatResponse
from app.session_store import SessionData, SessionStore, append_turn, get_store

app = FastAPI(title="Qadri Group AI Supply Chain Assistant")


@app.on_event("startup")
def _warm_caches() -> None:
    """Build the RAG index and introspect the DB schema once at startup
    (both are process-level singletons) so the first user request isn't
    slow. Failures here are logged, not fatal — the endpoint still degrades
    gracefully per-request if the DB is briefly unavailable."""
    try:
        get_index()
    except Exception as e:  # pragma: no cover
        print(f"WARNING: RAG index failed to build at startup: {e}")
    try:
        introspect()
    except Exception as e:  # pragma: no cover
        print(f"WARNING: schema introspection failed at startup: {e}")
    get_client()  # cheap; just checks OPENAI_API_KEY presence

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    db_ok, db_detail = True, "ok"
    try:
        schema = introspect()
        db_detail = f"{len(schema.tables)} tables/views visible"
    except Exception as e:  # pragma: no cover - manual/ops check
        db_ok, db_detail = False, str(e)

    return {
        "status": "ok" if db_ok else "degraded",
        "database": {"ok": db_ok, "detail": db_detail},
        "openai_configured": get_client().available,
        "session_store": "redis" if config.REDIS_URL else "in-memory",
    }


def _prepare(req: ChatRequest) -> tuple[str, SessionStore, SessionData, dict]:
    """Shared setup for both /api/chat and /api/chat/stream: resolve the
    session, build the time-period clarification pairing (rule 14) if one
    is pending, and assemble the graph's initial state. Returns
    (session_id, store, session, initial_state)."""
    question = req.question.strip()
    store = get_store()
    session_id = req.session_id or str(uuid.uuid4())
    session = store.get(session_id)

    is_clarification_reply = session.pending_question is not None
    if is_clarification_reply:
        llm_question = (
            f'Earlier you (the assistant) asked the user for a time period '
            f'to answer this question: "{session.pending_question}". '
            f'The user\'s reply is: "{question}".'
        )
        session.pending_question = None
    else:
        llm_question = question

    initial_state = {
        "original_question": question,
        "llm_question": llm_question,
        "is_clarification_reply": is_clarification_reply,
        "history": session.history,
        "transcript": session.transcript,
        "repair_count": 0,
        "give_up": False,
    }
    return session_id, store, session, initial_state


def _guard_verdict(result: dict) -> str:
    """Human-readable summary of what the guard/execution actually did,
    for the structured log line (see app/logging_config.py)."""
    done_reason = result.get("done_reason")
    if done_reason in ("dictionary", "clarify", "error"):
        return f"n/a ({done_reason})"
    if done_reason == "guard_failed":
        return "rejected"
    if done_reason == "exec_failed":
        return "passed_exec_failed"
    if done_reason in ("empty", "answered", "forecast"):
        return "passed"
    return "unknown"


def _finalize(
    session_id: str,
    store: SessionStore,
    session: SessionData,
    result: dict,
    start_time: float | None = None,
) -> ChatResponse:
    """Shared teardown: persist session memory, log the request, and shape
    the final response — identical payload whether it came from invoke()
    or the last chunk of stream()."""
    if result.get("needs_clarification"):
        session.pending_question = result.get("original_question") or session.pending_question
    else:
        session.history = result.get("new_history", session.history)

    # Record the human-facing exchange on EVERY terminal path — including the
    # dictionary, conversational, empty-result and clarification turns, which
    # `history` (SQL-only) deliberately skips. Without this the transcript
    # would have holes, and a follow-up like "explain that again" right after
    # a definition answer would have nothing to refer back to.
    answer_text = result.get("answer")
    if answer_text:
        session.transcript = append_turn(
            session.transcript, result.get("original_question", ""), answer_text
        )
    store.save(session_id, session)

    response = ChatResponse(
        answer=result.get("answer") or "I could not find sufficient information in the available supply chain data.",
        sql_used=result.get("safe_sql"),
        confidence=float(result.get("confidence", 0.0)),
        session_id=session_id,
        needs_clarification=bool(result.get("needs_clarification")),
        columns=result.get("columns"),
        rows=result.get("rows"),
    )

    log_request(
        question=result.get("original_question", ""),
        sql_used=response.sql_used,
        guard_verdict=_guard_verdict(result),
        repair_count=int(result.get("repair_count", 0)),
        row_count=result.get("row_count"),
        latency_ms=(time.monotonic() - start_time) * 1000 if start_time is not None else -1.0,
        confidence=response.confidence,
        done_reason=result.get("done_reason"),
        session_id=session_id,
    )

    return response


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    question = req.question.strip()
    if not question:
        return ChatResponse(
            answer="Please type a question about stock, issuance, purchases, imports, or logistics.",
            sql_used=None,
            confidence=0.0,
            session_id=req.session_id or str(uuid.uuid4()),
        )

    start_time = time.monotonic()
    session_id, store, session, initial_state = _prepare(req)
    result = get_graph().invoke(initial_state)
    result.setdefault("original_question", question)
    return _finalize(session_id, store, session, result, start_time=start_time)


# Which of the graph's actual node names corresponds to which of the four
# progress events a streaming client sees. Several nodes fold into the same
# event because they're the same conceptual phase from the outside (e.g.
# validate_sql/repair_sql are still "generating_sql" from the client's POV).
_NODE_TO_EVENT = {
    "understand": "understanding",
    "dictionary_answer": "understanding",
    "retrieve_context": "understanding",
    "generate_sql": "generating_sql",
    "validate_sql": "generating_sql",
    "repair_sql": "generating_sql",
    "execute_sql": "running_query",
    "generate_answer": "explaining",
    "conversational_answer": "explaining",
    "forecast_answer": "explaining",
}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Same question-answering as /api/chat, streamed as Server-Sent
    Events: one small `event: <phase>` line per LangGraph node as it runs
    (understanding / generating_sql / running_query / explaining), then a
    final `event: result` carrying the exact same payload /api/chat
    returns (so a client can ignore the progress events entirely and just
    read the last one if it wants)."""
    question = req.question.strip()
    start_time = time.monotonic()

    def gen():
        if not question:
            yield _sse("result", {
                "answer": "Please type a question about stock, issuance, purchases, imports, or logistics.",
                "sql_used": None,
                "confidence": 0.0,
                "session_id": req.session_id or str(uuid.uuid4()),
                "needs_clarification": False,
                "columns": None,
                "rows": None,
            })
            return

        session_id, store, session, initial_state = _prepare(req)
        graph = get_graph()

        last_state: dict = dict(initial_state)
        seen_events: set[str] = set()
        try:
            for chunk in graph.stream(initial_state, stream_mode="updates"):
                for node_name, update in chunk.items():
                    last_state.update(update)
                    event_name = _NODE_TO_EVENT.get(node_name)
                    if event_name and event_name not in seen_events:
                        seen_events.add(event_name)
                        yield _sse(event_name, {"phase": event_name})
        except Exception as e:  # pragma: no cover - defensive; never break the stream silently
            yield _sse("error", {"message": f"Stream interrupted: {e}"})

        last_state.setdefault("original_question", question)
        response = _finalize(session_id, store, session, last_state, start_time=start_time)
        yield _sse("result", response.model_dump())

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

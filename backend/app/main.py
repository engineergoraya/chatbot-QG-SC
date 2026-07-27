"""
main.py — FastAPI entry point.

Exposes:
  POST /api/chat    — the chatbot endpoint (see schemas.ChatRequest/Response)
  GET  /api/health   — liveness + DB/LLM readiness check

Session memory (SQL-generation history, and a pending time-period
clarification question) lives in a simple in-memory dict keyed by
session_id. That's enough for a single-process prototype; if this ever runs
behind multiple workers/replicas, swap _SESSIONS for a shared store (Redis)
without changing the graph or endpoint contract.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.db.introspect import introspect
from app.graph.workflow import get_graph
from app.knowledge.rag import get_index
from app.llm.openai_client import get_client
from app.schemas import ChatRequest, ChatResponse

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


class _Session:
    __slots__ = ("history", "pending_question")

    def __init__(self) -> None:
        self.history: list[dict] = []
        self.pending_question: str | None = None


_SESSIONS: dict[str, _Session] = {}


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
    }


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

    session_id = req.session_id or str(uuid.uuid4())
    session = _SESSIONS.setdefault(session_id, _Session())

    # If the previous turn asked for a time period, pair this reply with the
    # original question so the model can resolve it (see business_rules.py
    # rule 14). This is the ONE place multi-turn state changes the input.
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

    graph = get_graph()
    result = graph.invoke(
        {
            "original_question": question,
            "llm_question": llm_question,
            "is_clarification_reply": is_clarification_reply,
            "history": session.history,
            "repair_count": 0,
            "give_up": False,
        }
    )

    if result.get("needs_clarification"):
        session.pending_question = question
    else:
        session.history = result.get("new_history", session.history)

    return ChatResponse(
        answer=result.get("answer") or "I could not find sufficient information in the available supply chain data.",
        sql_used=result.get("safe_sql"),
        confidence=float(result.get("confidence", 0.0)),
        session_id=session_id,
        needs_clarification=bool(result.get("needs_clarification")),
    )

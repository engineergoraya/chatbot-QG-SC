"""Pydantic request/response models for the /api/chat contract."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Natural-language supply-chain question")
    session_id: str | None = Field(
        default=None,
        description=(
            "Optional. Pass back the session_id from a prior response to keep "
            "conversation memory (needed for the time-period clarification "
            "follow-up and for 'what about X instead'-style follow-ups). "
            "Omit for a one-off, stateless question."
        ),
    )


class ChatResponse(BaseModel):
    answer: str
    sql_used: str | None = None
    confidence: float
    session_id: str
    needs_clarification: bool = False

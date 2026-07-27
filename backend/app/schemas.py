"""Pydantic request/response models for the /api/chat contract.

Backward compatibility: `rows`/`columns` were added alongside the original
answer/sql_used/confidence/session_id/needs_clarification fields, all with
defaults, so any existing caller that only reads the original fields keeps
working unchanged.
"""

from __future__ import annotations

from typing import Any

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
    columns: list[str] | None = Field(
        default=None,
        description="Ordered column names from the executed query. None when no SQL ran (definition/clarify/error paths).",
    )
    rows: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "The actual query result rows (capped at CHATBOT_MAX_ROWS, same cap "
            "the SQL guard enforces via LIMIT). None when no SQL ran."
        ),
    )

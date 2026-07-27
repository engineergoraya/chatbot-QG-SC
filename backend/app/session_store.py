"""
session_store.py — pluggable per-session conversation memory.

Two implementations, chosen automatically by whether REDIS_URL is set:

  * InMemoryStore (default) — a plain dict, process-local. Fine for a
    single-process dev/demo deployment; lost on restart, not shared across
    workers/replicas. This is exactly what app/main.py did inline before
    (the module-level `_SESSIONS` dict + `_Session` class) — moved here
    unchanged in behavior, just behind the SessionStore interface.

  * RedisStore — used when REDIS_URL is set. Shared across processes/
    workers/replicas and survives a process restart. Stores each session
    as a JSON blob under `chatbot:session:<id>`, with a TTL (default 24h,
    override via REDIS_SESSION_TTL_SECONDS) refreshed on every save, so an
    active conversation never expires mid-use but idle ones eventually do.

Preserves the exact fields main.py already tracked per session:
`history` (the SQL-generation message list) and `pending_question` (set
while the assistant is mid-way through asking for a time-period
clarification, per business_rules.py rule 14). No /api/chat contract
change — this is purely an internal swap.

The `redis` package is imported lazily inside RedisStore.__init__, so
nothing about a plain local/dev run (no REDIS_URL) requires it to be
installed or reachable.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field

from app import config


@dataclass
class SessionData:
    # The SQL-GENERATION conversation: {user: question, assistant: the SQL it
    # wrote}. Replayed into generate_sql so follow-ups like "what about the
    # ones awaiting sailing?" inherit the previous query's shape.
    history: list[dict] = field(default_factory=list)
    # The HUMAN-FACING conversation: {user: question, assistant: the answer
    # text the user actually saw}. Kept separately from `history` because the
    # two serve different callers — the SQL model needs prior SQL, while the
    # explain/conversational steps need prior ANSWERS ("explain that more
    # simply", "what did I ask first?"). Trimmed to the most recent
    # MAX_TRANSCRIPT_TURNS exchanges so a long session can't grow the prompt
    # without bound.
    transcript: list[dict] = field(default_factory=list)
    pending_question: str | None = None


# One "turn" = a user message + the assistant's reply, so this is 12 messages.
MAX_TRANSCRIPT_TURNS = 6


def append_turn(transcript: list[dict], question: str, answer: str) -> list[dict]:
    """Append one exchange to the transcript, trimmed to the recent window."""
    updated = transcript + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]
    return updated[-(MAX_TRANSCRIPT_TURNS * 2):]


class SessionStore(ABC):
    @abstractmethod
    def get(self, session_id: str) -> SessionData:
        """Return this session's data, creating an empty one if new."""

    @abstractmethod
    def save(self, session_id: str, data: SessionData) -> None:
        """Persist this session's data."""


class InMemoryStore(SessionStore):
    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}

    def get(self, session_id: str) -> SessionData:
        return self._sessions.setdefault(session_id, SessionData())

    def save(self, session_id: str, data: SessionData) -> None:
        self._sessions[session_id] = data


class RedisStore(SessionStore):
    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        import redis  # lazy import — only required when REDIS_URL is set

        self._r = redis.Redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    @staticmethod
    def _key(session_id: str) -> str:
        return f"chatbot:session:{session_id}"

    def get(self, session_id: str) -> SessionData:
        raw = self._r.get(self._key(session_id))
        if raw is None:
            return SessionData()
        payload = json.loads(raw)
        return SessionData(
            history=payload.get("history", []),
            transcript=payload.get("transcript", []),
            pending_question=payload.get("pending_question"),
        )

    def save(self, session_id: str, data: SessionData) -> None:
        self._r.set(self._key(session_id), json.dumps(asdict(data)), ex=self._ttl)


_store: SessionStore | None = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        if config.REDIS_URL:
            _store = RedisStore(config.REDIS_URL, config.REDIS_SESSION_TTL_SECONDS)
        else:
            _store = InMemoryStore()
    return _store

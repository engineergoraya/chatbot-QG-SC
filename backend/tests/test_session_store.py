"""
Unit tests for app/session_store.py (item 3).

InMemoryStore is tested directly. RedisStore is tested against a small
fake Redis client (no real Redis server needed) to verify it serializes/
deserializes SessionData correctly and applies the TTL on save — the
`redis` package itself is trusted, we're only testing our own wiring.
"""

from __future__ import annotations

import json

from app.session_store import InMemoryStore, RedisStore, SessionData


def test_in_memory_store_roundtrip():
    store = InMemoryStore()

    fresh = store.get("s1")
    assert fresh.history == []
    assert fresh.pending_question is None

    fresh.history.append({"role": "user", "content": "hi"})
    fresh.pending_question = "what period?"
    store.save("s1", fresh)

    reloaded = store.get("s1")
    assert reloaded.history == [{"role": "user", "content": "hi"}]
    assert reloaded.pending_question == "what period?"


def test_in_memory_store_sessions_are_independent():
    store = InMemoryStore()
    a = store.get("a")
    a.pending_question = "for a"
    store.save("a", a)

    b = store.get("b")
    assert b.pending_question is None


class _FakeRedis:
    """Minimal stand-in for redis.Redis — just get/set with an expiry
    kwarg, backed by a plain dict."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.last_ex: int | None = None

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        self.last_ex = ex


def test_redis_store_roundtrip_and_ttl(monkeypatch):
    store = RedisStore.__new__(RedisStore)  # bypass __init__ (no real redis import/connection)
    fake = _FakeRedis()
    store._r = fake
    store._ttl = 3600

    fresh = store.get("s1")
    assert fresh == SessionData()

    fresh.history.append({"role": "assistant", "content": "SELECT 1;"})
    store.save("s1", fresh)

    assert fake.last_ex == 3600
    stored_raw = fake.data["chatbot:session:s1"]
    assert json.loads(stored_raw)["history"] == [{"role": "assistant", "content": "SELECT 1;"}]

    reloaded = store.get("s1")
    assert reloaded.history == [{"role": "assistant", "content": "SELECT 1;"}]
    assert reloaded.pending_question is None


def test_redis_store_missing_session_returns_fresh():
    store = RedisStore.__new__(RedisStore)
    store._r = _FakeRedis()
    store._ttl = 3600

    assert store.get("never-seen") == SessionData()

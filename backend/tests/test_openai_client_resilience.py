"""
Unit tests for app/llm/openai_client.py's retry/backoff resilience
(item 4). No network calls, no real API key needed — the OpenAI SDK's HTTP
client is never invoked; we exercise _call_with_retry (and OpenAIClient._chat
wired to a mocked SDK client) directly.

Covers:
  - a transient error (429 rate limit) that recovers within the retry
    budget succeeds and returns the real result;
  - a persistent transient error (retries exhausted) fails CLEANLY with a
    plain RuntimeError and an honest message — never a raw SDK stack trace;
  - a non-transient error (e.g. a plain ValueError, or an OpenAI error type
    NOT in the transient set) propagates immediately, with zero retries;
  - the backoff delays actually used match the documented 2s/4s/8s schedule
    (time.sleep is patched out so the test itself runs instantly).
"""

from __future__ import annotations

import httpx
import pytest
from openai import APIConnectionError, RateLimitError

from app.llm import openai_client as oc


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("Rate limited", response=response, body=None)


def _connection_error() -> APIConnectionError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return APIConnectionError(message="Connection error.", request=request)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Every test in this file runs instantly — we assert WHAT delays would
    have been used without actually waiting for them."""
    delays: list[float] = []
    monkeypatch.setattr(oc.time, "sleep", lambda s: delays.append(s))
    return delays


def test_transient_error_recovers_within_budget(_no_real_sleep):
    """Fails twice with a transient error, then succeeds — must return the
    real result and have retried exactly twice (waiting 2s, then 4s)."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _rate_limit_error()
        return "ok-result"

    result = oc._call_with_retry(flaky)

    assert result == "ok-result"
    assert calls["n"] == 3
    assert _no_real_sleep == [2, 4]  # documented backoff schedule


def test_persistent_transient_error_fails_cleanly(_no_real_sleep):
    """Fails every attempt (all 4) with a transient error — must raise a
    plain, honest RuntimeError, not leak the raw SDK exception/stack trace,
    and must have exhausted the full retry budget (3 waits: 2s/4s/8s)."""

    def always_fails():
        raise _connection_error()

    with pytest.raises(RuntimeError) as exc_info:
        oc._call_with_retry(always_fails)

    message = str(exc_info.value)
    assert "temporarily unavailable" in message.lower()
    # the original SDK exception must not leak into the user-facing message
    assert "APIConnectionError" not in message
    assert _no_real_sleep == [2, 4, 8]


def test_non_transient_error_propagates_immediately(_no_real_sleep):
    """A non-transient error (not in the retry set) must propagate as-is,
    on the FIRST attempt, with zero retries/delays."""
    calls = {"n": 0}

    def fails_hard():
        calls["n"] += 1
        raise ValueError("not a transient error")

    with pytest.raises(ValueError, match="not a transient error"):
        oc._call_with_retry(fails_hard)

    assert calls["n"] == 1
    assert _no_real_sleep == []


def test_chat_wires_through_call_with_retry(monkeypatch, _no_real_sleep):
    """OpenAIClient._chat must route through _call_with_retry (not call the
    SDK directly), so a transient failure on the underlying
    chat.completions.create is retried and recovers exactly like the
    lower-level test above."""
    client = oc.OpenAIClient.__new__(oc.OpenAIClient)  # bypass __init__ (no API key needed)
    client.available = True

    calls = {"n": 0}

    class _FakeMessage:
        content = "SELECT 1;"

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    def fake_create(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _rate_limit_error()
        return _FakeResponse()

    fake_client = type(
        "FakeClient", (), {"chat": type("Chat", (), {"completions": type("Completions", (), {"create": staticmethod(fake_create)})()})()}
    )()
    client._client = fake_client

    result = client._chat([{"role": "user", "content": "hi"}], temperature=0)

    assert result == "SELECT 1;"
    assert calls["n"] == 2
    assert _no_real_sleep == [2]

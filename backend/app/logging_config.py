"""
logging_config.py — structured (JSON-lines) request logging.

One INFO-level line per /api/chat (or /api/chat/stream) request, emitted
from app.main._finalize so both endpoints log identically. Fields: the
question, the final SQL (or None), the guard verdict, repair_count, the
row count, latency in ms, confidence, and done_reason. Deliberately does
NOT log: the OPENAI_API_KEY, DB credentials, or the actual result ROWS
(only the count) — row contents can be business-sensitive and aren't
needed to debug routing/guard/latency issues; sql_used already shows what
was queried.

Level is configurable via LOG_LEVEL (default INFO) — set to DEBUG for
verbose per-node detail, or WARNING to quiet it down in production.
"""

from __future__ import annotations

import json
import logging
import sys

from app import config

_LOGGER_NAME = "chatbot"


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str, ensure_ascii=False)


_configured = False


def get_logger() -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonLineFormatter())
        logger.addHandler(handler)
        logger.setLevel(config.LOG_LEVEL)
        logger.propagate = False
        _configured = True
    return logger


def log_request(
    *,
    question: str,
    sql_used: str | None,
    guard_verdict: str,
    repair_count: int,
    row_count: int | None,
    latency_ms: float,
    confidence: float,
    done_reason: str | None,
    session_id: str,
) -> None:
    get_logger().info(
        "chat_request",
        extra={
            "fields": {
                "question": question,
                "sql_used": sql_used,
                "guard_verdict": guard_verdict,
                "repair_count": repair_count,
                "row_count": row_count,
                "latency_ms": round(latency_ms, 1),
                "confidence": confidence,
                "done_reason": done_reason,
                "session_id": session_id,
            }
        },
    )

"""
config.py — central configuration for the chatbot backend.

Reads everything from environment variables (loaded from `.env` via
python-dotenv). Nothing secret is hardcoded here.

Two connection strings are recognized:

  * DATABASE_URL          -> the primary Postgres connection (owner-capable).
  * CHATBOT_DATABASE_URL  -> the chatbot's own SELECT-only connection
                              (points at the `chatbot_ro` role created by
                              scripts/setup_readonly_role.sql). This is what
                              the chatbot uses at runtime.

If CHATBOT_DATABASE_URL is not set, the app falls back to DATABASE_URL with a
loud warning — running the chatbot on a write-capable role defeats the guard
layer's second safety net (see graph/guard.py).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _get(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    return val if val not in (None, "") else default


DATABASE_URL = _get("DATABASE_URL", "postgresql://localhost:5432/supplychain_automation")
CHATBOT_DATABASE_URL = _get("CHATBOT_DATABASE_URL")

OPENAI_API_KEY = _get("OPENAI_API_KEY")
OPENAI_MODEL = _get("OPENAI_MODEL", "gpt-4o-mini")

MAX_ROWS = int(_get("CHATBOT_MAX_ROWS", "200"))
STATEMENT_TIMEOUT_MS = int(_get("CHATBOT_STATEMENT_TIMEOUT_MS", "8000"))

CORS_ORIGINS = [o.strip() for o in _get("CORS_ORIGINS", "http://localhost:5173").split(",")]


def _dsn_from_url(url: str) -> dict:
    p = urlparse(url)
    return {
        "host": p.hostname or "localhost",
        "port": p.port or 5432,
        "dbname": (p.path or "/").lstrip("/"),
        "user": p.username or os.getenv("USER", "postgres"),
        "password": p.password or "",
    }


def write_dsn() -> dict:
    """Connection kwargs for the write-capable (owner) role. Not used by the
    chatbot at query time — kept for completeness / future admin scripts."""
    return _dsn_from_url(DATABASE_URL)


def readonly_dsn() -> dict:
    """Connection kwargs for the chatbot's SELECT-only role."""
    if CHATBOT_DATABASE_URL:
        return _dsn_from_url(CHATBOT_DATABASE_URL)

    print(
        "WARNING: CHATBOT_DATABASE_URL is not set. Falling back to DATABASE_URL "
        "for the chatbot connection. This is UNSAFE — the SQL guard is your "
        "only protection against writes. Run "
        "backend/scripts/setup_readonly_role.sql and set CHATBOT_DATABASE_URL "
        "to use a real read-only role.",
        file=sys.stderr,
    )
    return _dsn_from_url(DATABASE_URL)


def openai_ready() -> bool:
    return bool(OPENAI_API_KEY)

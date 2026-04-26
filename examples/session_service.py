"""Durable ADK session service helpers for examples."""

from __future__ import annotations

import os
from pathlib import Path

from google.adk.sessions.sqlite_session_service import SqliteSessionService

SESSION_DB_ENV = "ADK_CHANNELS_SESSION_DB"
DEFAULT_SESSION_DB = Path(__file__).resolve().parent.parent / ".adk_channels" / "sessions.sqlite"


def resolve_session_db_path() -> str:
    """Resolve the SQLite database path used by example ADK sessions."""
    configured = os.environ.get(SESSION_DB_ENV)
    if configured:
        return configured
    return str(DEFAULT_SESSION_DB)


def create_sqlite_session_service() -> SqliteSessionService:
    """Create a durable SQLite-backed ADK session service."""
    db_path = resolve_session_db_path()
    if not db_path.startswith(("sqlite:", "sqlite+aiosqlite:")):
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return SqliteSessionService(db_path)

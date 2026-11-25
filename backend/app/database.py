from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import Settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    system_prompt  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id_id
    ON messages (session_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, settings: Settings) -> None:
        self.path: Path = settings.database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            connection.commit()

    def health(self) -> None:
        with self.connection() as connection:
            connection.execute("SELECT 1").fetchone()

    def get_system_prompt(self, session_id: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row["system_prompt"] if row else None

    def save_turn(
        self,
        session_id: str,
        system_prompt: str | None,
        user_message: str,
        assistant_reply: str,
        metadata: dict,
    ) -> None:
        timestamp = _now()
        with self.connection() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO sessions (session_id, system_prompt, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        system_prompt = excluded.system_prompt,
                        updated_at = excluded.updated_at
                    """,
                    (session_id, system_prompt, timestamp, timestamp),
                )
                connection.execute(
                    """
                    INSERT INTO messages (session_id, role, content, metadata, created_at)
                    VALUES (?, 'user', ?, NULL, ?)
                    """,
                    (session_id, user_message, timestamp),
                )
                connection.execute(
                    """
                    INSERT INTO messages (session_id, role, content, metadata, created_at)
                    VALUES (?, 'assistant', ?, ?, ?)
                    """,
                    (session_id, assistant_reply, json.dumps(metadata, ensure_ascii=False), timestamp),
                )

    def get_history(self, session_id: str) -> list[sqlite3.Row] | None:
        with self.connection() as connection:
            session = connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                return None
            rows = connection.execute(
                """
                SELECT role, content, metadata
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        return list(rows)

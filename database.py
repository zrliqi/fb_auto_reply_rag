from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    facebook_id TEXT NOT NULL UNIQUE,
                    current_stage TEXT NOT NULL,
                    last_interaction DATETIME NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    message_text TEXT NOT NULL,
                    model_used TEXT,
                    timestamp DATETIME NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            self._ensure_model_used_column(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_user_timestamp ON messages(user_id, timestamp DESC, id DESC)"
            )

    def get_or_create_user(self, facebook_id: str, initial_stage: str = "greeting") -> dict[str, Any]:
        now = self._utc_now()
        with self._connect() as conn:
            user = conn.execute(
                """
                SELECT id, facebook_id, current_stage, last_interaction
                FROM users
                WHERE facebook_id = ?
                """,
                (facebook_id,),
            ).fetchone()

            if user is None:
                try:
                    conn.execute(
                        """
                        INSERT INTO users (facebook_id, current_stage, last_interaction)
                        VALUES (?, ?, ?)
                        """,
                        (facebook_id, initial_stage, now),
                    )
                except sqlite3.IntegrityError:
                    # Parallel worker may insert the same user concurrently.
                    pass

            conn.execute(
                "UPDATE users SET last_interaction = ? WHERE facebook_id = ?",
                (now, facebook_id),
            )

            created_or_found = conn.execute(
                """
                SELECT id, facebook_id, current_stage, last_interaction
                FROM users
                WHERE facebook_id = ?
                """,
                (facebook_id,),
            ).fetchone()

            if created_or_found is None:
                raise RuntimeError(f"Failed to get or create user for facebook_id={facebook_id}")

            return dict(created_or_found)

    def set_user_stage(self, user_id: int, new_stage: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET current_stage = ?, last_interaction = ?
                WHERE id = ?
                """,
                (new_stage, self._utc_now(), user_id),
            )

    def touch_user(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_interaction = ? WHERE id = ?",
                (self._utc_now(), user_id),
            )

    def save_message(self, user_id: int, role: str, message_text: str, model_used: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (user_id, role, message_text, model_used, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, role, message_text, model_used, self._utc_now()),
            )

    def get_recent_messages(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, message_text, timestamp
                FROM messages
                WHERE user_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        # Return oldest -> newest for prompt building.
        return [dict(row) for row in reversed(rows)]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _ensure_model_used_column(conn: sqlite3.Connection) -> None:
        # Keep existing production DBs compatible by adding the column if missing.
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "model_used" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN model_used TEXT")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

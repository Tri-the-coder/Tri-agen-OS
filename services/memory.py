import os
import sqlite3
from typing import List, Dict, Any


class ConversationMemory:
    def __init__(self, db_path: str = None) -> None:
        self.db_path = db_path or os.getenv("MEMORY_DB_PATH", "memory.db")
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def add_message(self, session_id: str, role: str, content: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            conn.commit()

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [{"role": role, "content": content} for role, content in rows]

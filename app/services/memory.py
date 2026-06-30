from typing import List, Dict, Any

from app.db.session import get_db_connection


class MemoryService:
    def __init__(self) -> None:
        self._init_table()

    def _init_table(self) -> None:
        with get_db_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    value TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def remember(self, key: str, value: str) -> None:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memories (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()

    def recall(self, key: str):
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT value FROM memories WHERE key = ?",
                (key,),
            ).fetchone()
        return row[0] if row else None

    def search(self, query: str) -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT key, value FROM memories WHERE key LIKE ? OR value LIKE ?",
                (f"%{query}%", f"%{query}%"),
            ).fetchall()
        return [{"key": key, "value": value} for key, value in rows]

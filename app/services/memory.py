from typing import List, Dict, Any

from app.db.session import get_db_connection, init_db


class MemoryService:
    def __init__(self) -> None:
        self._init_table()

    def _init_table(self) -> None:
        # The memories table is created with the rest of the schema.
        init_db()

    def remember(self, key: str, value: str) -> None:
        with get_db_connection() as conn:
            # INSERT OR REPLACE is SQLite-only; delete-then-insert is portable.
            conn.execute("DELETE FROM memories WHERE key = ?", (key,))
            conn.execute(
                "INSERT INTO memories (key, value) VALUES (?, ?)",
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

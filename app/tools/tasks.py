import sqlite3
from typing import Any, Dict

from app.db.session import get_db_connection


def add_task(title: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (title, status) VALUES (?, ?)",
            (title, "pending"),
        )
        conn.commit()
        task_id = cursor.lastrowid
    return {"id": task_id, "title": title, "status": "pending"}

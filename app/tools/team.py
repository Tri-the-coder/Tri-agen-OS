from typing import Any, Dict

from app.db.session import get_db_connection


def assign_task_to_member(task_title: str, member_name: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (title, status, assignee) VALUES (?, ?, ?)",
            (task_title, "assigned", member_name),
        )
        conn.commit()
    return {"ok": True, "message": f"'{task_title}' {member_name}-কে দেওয়া হয়েছে"}

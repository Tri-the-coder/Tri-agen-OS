from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.session import get_db_connection


def _row_to_report(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "slack_user_id": row[1],
        "user_name": row[2],
        "text": row[3],
        "created_at": row[4],
    }


def add_report(slack_user_id: str, user_name: Optional[str], text: str) -> Dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO reports (slack_user_id, user_name, text, created_at) VALUES (?, ?, ?, ?)",
            (slack_user_id, user_name, text, created_at),
        )
        conn.commit()
        report_id = cursor.lastrowid
    return {
        "id": report_id,
        "slack_user_id": slack_user_id,
        "user_name": user_name,
        "text": text,
        "created_at": created_at,
    }


def reports_for_user(slack_user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, slack_user_id, user_name, text, created_at FROM reports "
            "WHERE slack_user_id = ? ORDER BY id DESC LIMIT ?",
            (slack_user_id, limit),
        ).fetchall()
    return [_row_to_report(row) for row in rows]


def recent_reports(limit: int = 25) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, slack_user_id, user_name, text, created_at FROM reports "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_report(row) for row in rows]


def latest_per_person(limit: int = 20) -> List[Dict[str, Any]]:
    """Most recent report from each teammate - the basis for "who is doing what"."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, slack_user_id, user_name, text, created_at FROM reports "
            "WHERE id IN (SELECT MAX(id) FROM reports GROUP BY slack_user_id) "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_report(row) for row in rows]

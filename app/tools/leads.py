from typing import Any, Dict

from app.db.session import get_db_connection


def add_lead(details: str) -> Dict[str, Any]:
    name = details.split(",")[0].strip() if "," in details else details.strip()
    with get_db_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO leads (name, stage, notes) VALUES (?, ?, ?)",
            (name, "new", details),
        )
        conn.commit()
        lead_id = cursor.lastrowid
    return {"id": lead_id, "name": name, "stage": "new"}

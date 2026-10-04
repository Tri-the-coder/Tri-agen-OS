import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.session import get_db_connection

# Scoring rubric. Deterministic on purpose: the model extracts the facts, but the
# score is arithmetic so the same lead always scores the same.
SCORE_HAS_BUSINESS = 30
SCORE_ASKED_PRICING = 25
SCORE_SHARED_PHONE = 25
SCORE_BUYING_INTENT = 20

# 55 rather than 65: business + phone scores exactly 55, and someone who hands over
# a phone number is about as qualified as a lead gets.
AUTO_SAVE_THRESHOLD = 55

# Bangladeshi mobile numbers: 01XXXXXXXXX, optionally +88 prefixed.
PHONE_RE = re.compile(r"(?:\+?88)?0?1[3-9]\d{8}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_phone(text: str) -> Optional[str]:
    digits = re.sub(r"[\s\-().]", "", text or "")
    match = PHONE_RE.search(digits)
    return match.group(0) if match else None


def score_lead(lead: Dict[str, Any]) -> int:
    """Score out of 100 from the facts extracted, never from the model's opinion."""
    score = 0
    if lead.get("has_business"):
        score += SCORE_HAS_BUSINESS
    if lead.get("asked_pricing"):
        score += SCORE_ASKED_PRICING
    if lead.get("phone"):
        score += SCORE_SHARED_PHONE
    if lead.get("buying_intent"):
        score += SCORE_BUYING_INTENT
    return min(score, 100)


def _row_to_lead(row) -> Dict[str, Any]:
    return {
        "id": row[0], "name": row[1], "stage": row[2], "notes": row[3],
        "lead_ref": row[4], "phone": row[5], "business_type": row[6],
        "score": row[7], "source": row[8], "created_at": row[9],
    }


_SELECT = (
    "SELECT id, name, stage, notes, lead_ref, phone, business_type, score, source, "
    "created_at FROM leads"
)


def create_lead(
    name: str,
    phone: Optional[str] = None,
    business_type: Optional[str] = None,
    notes: str = "",
    score: int = 0,
    source: str = "unknown",
) -> Dict[str, Any]:
    created_at = _now()
    with get_db_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO leads (name, stage, notes, phone, business_type, score, source, "
            "created_at) VALUES (?, 'new', ?, ?, ?, ?, ?, ?)",
            (name or "Unknown", notes, phone, business_type, score, source, created_at),
        )
        lead_id = cursor.lastrowid
        lead_ref = f"LD-{lead_id:05d}"
        conn.execute("UPDATE leads SET lead_ref = ? WHERE id = ?", (lead_ref, lead_id))
        conn.commit()

    return {
        "id": lead_id, "lead_ref": lead_ref, "name": name or "Unknown", "stage": "new",
        "notes": notes, "phone": phone, "business_type": business_type,
        "score": score, "source": source, "created_at": created_at,
    }


def add_lead(details: str, source: str = "telegram") -> Dict[str, Any]:
    """Free-text lead, kept for the existing "Add lead: ..." command."""
    name = details.split(",")[0].strip() if "," in details else details.strip()
    phone = find_phone(details)
    return create_lead(
        name=name,
        phone=phone,
        notes=details,
        score=score_lead({"has_business": True, "phone": phone}),
        source=source,
    )


def parse_lead_command(text: str) -> Dict[str, Any]:
    """Parse "Name - Business - Phone - Notes".

    Fields are matched by shape rather than position, because people reorder them:
    whichever part looks like a phone number is the phone, wherever it lands.
    """
    parts = [part.strip() for part in (text or "").split("-") if part.strip()]
    phone = find_phone(text)

    # Drop the part that was only there to carry the phone number.
    remaining = [part for part in parts if not (phone and find_phone(part))]

    return {
        "name": remaining[0] if remaining else "Unknown",
        "business_type": remaining[1] if len(remaining) > 1 else None,
        "notes": " - ".join(remaining[2:]) if len(remaining) > 2 else "",
        "phone": phone,
    }


def recent_leads(limit: int = 10) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_lead(row) for row in rows]


def lead_exists(phone: Optional[str], name: str) -> bool:
    """Guard against the same person being captured twice in one conversation."""
    if not phone and not name:
        return False
    with get_db_connection() as conn:
        if phone:
            row = conn.execute("SELECT 1 FROM leads WHERE phone = ? LIMIT 1", (phone,)).fetchone()
            if row:
                return True
        row = conn.execute(
            "SELECT 1 FROM leads WHERE lower(name) = lower(?) LIMIT 1", (name,)
        ).fetchone()
    return bool(row)

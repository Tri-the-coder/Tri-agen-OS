import json
import logging
import re
from typing import Any, Dict, Optional

from app.services.models import AllModelsUnavailable, complete
from app.tools.leads import AUTO_SAVE_THRESHOLD, find_phone, score_lead

logger = logging.getLogger(__name__)

# Cheap pre-filter. The model is only asked to extract when a message already looks
# like a lead, so an ordinary question does not cost a second free-tier request.
INTEREST_WORDS = (
    "price", "pricing", "cost", "trial", "subscribe", "package", "plan", "buy",
    "demo", "kinbo", "kinte", "nite chai", "koto", "taka", "tk", "bdt",
    "দাম", "মূল্য", "প্যাকেজ", "ট্রায়াল", "কিনব", "কিনতে", "নিতে চাই", "কত",
    "টাকা", "সাবস্ক্রিপশন", "ব্যবহার করতে",
)
BUSINESS_WORDS = (
    "shop", "store", "business", "dokan", "bebsha", "babsha", "garments",
    "pharmacy", "restaurant", "wholesale", "retail",
    "দোকান", "ব্যবসা", "ব্যবসায়", "গার্মেন্টস", "ফার্মেসি", "হোলসেল",
)

EXTRACTION_PROMPT = """You extract lead details from one message. Reply with JSON only.

Schema:
{"name": string|null, "phone": string|null, "business_type": string|null,
 "has_business": boolean, "asked_pricing": boolean, "buying_intent": boolean,
 "notes": string}

Rules:
- Use only what the message actually says. Never guess a name, phone or business type.
- null means the message did not state it.
- has_business: they say they own or run a business/shop.
- asked_pricing: they asked about price, packages or the trial.
- buying_intent: they say they want to buy, subscribe or start.
- notes: one short sentence, in the message's own language.
Output the JSON object and nothing else."""


def looks_like_lead(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if find_phone(text):
        return True
    has_interest = any(word in lowered for word in INTEREST_WORDS)
    has_business = any(word in lowered for word in BUSINESS_WORDS)
    return has_interest and has_business


def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
    """Free models wrap JSON in prose or code fences more often than not."""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_lead(text: str) -> Optional[Dict[str, Any]]:
    """Pull lead facts out of a message. Returns None when it is not a lead."""
    if not looks_like_lead(text):
        return None

    try:
        result = complete(EXTRACTION_PROMPT, text)
    except AllModelsUnavailable as error:
        logger.warning("Lead extraction skipped, models unavailable | error=%s", error)
        return None
    except Exception as error:  # noqa: BLE001 - extraction must never break the reply
        logger.warning("Lead extraction failed | error=%s", error)
        return None

    data = _parse_json(result.get("content", ""))
    if not data:
        logger.warning("Lead extraction returned unparseable output")
        return None

    # The phone is taken from the text by regex, not from the model, so a
    # hallucinated number cannot reach the database.
    phone = find_phone(text)

    lead = {
        "name": (data.get("name") or "").strip() or None,
        "phone": phone,
        "business_type": (data.get("business_type") or "").strip() or None,
        "has_business": bool(data.get("has_business")),
        "asked_pricing": bool(data.get("asked_pricing")),
        "buying_intent": bool(data.get("buying_intent")),
        "notes": (data.get("notes") or "").strip(),
    }
    lead["score"] = score_lead(lead)
    return lead


def should_auto_save(lead: Optional[Dict[str, Any]]) -> bool:
    return bool(lead) and lead.get("score", 0) >= AUTO_SAVE_THRESHOLD

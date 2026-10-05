import base64
import logging
import os
from typing import Any, Dict, List, Optional

import requests

from app.services.models import AllModelsUnavailable, FALLBACK_STATUSES, is_free

logger = logging.getLogger(__name__)

# Free models that accept image input. Verified to answer with an image attached.
DEFAULT_VISION_CHAIN: List[str] = [
    "qwen/qwen3.8-27b:free",
    "google/gemma-4-31b-it:free",
    "openrouter/free",
]

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def get_vision_chain() -> List[str]:
    configured = os.getenv("OPENROUTER_VISION_MODELS", "").strip()
    if configured:
        chain = [m.strip() for m in configured.split(",") if m.strip()]
        if chain:
            return chain
    return list(DEFAULT_VISION_CHAIN)


def describe(
    image_bytes: bytes,
    question: str,
    system_prompt: str,
    mime: str = "image/jpeg",
) -> Dict[str, Any]:
    """Ask a vision model about one image. Raises AllModelsUnavailable if none answer."""
    if not image_bytes:
        raise ValueError("no image data")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(f"image too large ({len(image_bytes)} bytes)")

    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "text", "text": question or "Describe this image."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]
    headers = {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}",
        "HTTP-Referer": os.getenv("APP_URL", "https://onrender.com"),
        "X-Title": "Babosayee Super Intelligent",
        "Content-Type": "application/json",
    }
    base_url = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1").rstrip("/")

    failures: List[Dict[str, Any]] = []
    for model in get_vision_chain():
        if not is_free(model):
            logger.warning("Skipping paid vision model %s", model)
            continue
        try:
            response = requests.post(
                f"{base_url}/chat/completions", headers=headers,
                json={"model": model, "messages": messages}, timeout=60,
            )
            if response.status_code in FALLBACK_STATUSES:
                failures.append({"model": model, "status": response.status_code,
                                 "error": response.text[:150]})
                continue
            response.raise_for_status()
            content = (response.json().get("choices", [{}])[0]
                       .get("message", {}).get("content", "") or "").strip()
            if not content:
                failures.append({"model": model, "status": None, "error": "empty response"})
                continue
            logger.info("Vision model answered | model=%s", model)
            return {"model": model, "content": content}
        except requests.RequestException as error:
            failures.append({"model": model, "status": None, "error": str(error)[:150]})

    raise AllModelsUnavailable(failures)


def fetch_telegram_photo(file_id: str) -> Optional[bytes]:
    """Download a Telegram photo by file_id."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not file_id:
        return None
    try:
        info = requests.get(f"https://api.telegram.org/bot{token}/getFile",
                            params={"file_id": file_id}, timeout=20).json()
        if not info.get("ok"):
            logger.warning("getFile failed | error=%s", info.get("description"))
            return None
        path = info["result"]["file_path"]
        data = requests.get(f"https://api.telegram.org/file/bot{token}/{path}", timeout=40)
        data.raise_for_status()
        return data.content
    except (requests.RequestException, KeyError, ValueError) as error:
        logger.warning("Telegram photo download failed | error=%s", error)
        return None

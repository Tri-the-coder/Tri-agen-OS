import logging
import os
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

# Free-tier chain. Every entry must be a ":free" (or inherently free) OpenRouter model
# that supports tool calling, so the agent never bills the account.
# thinkingmachines/inkling:free is deliberately absent: OpenRouter gates it behind
# "agentic harnesses" and returns a permanent 403 to a plain webhook like this one,
# so including it would only add a wasted round-trip to every fallback.
DEFAULT_MODEL_CHAIN: List[str] = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "qwen/qwen3.8-27b:free",
    "apodex/apodex-1.1-mini:free",
]

# Statuses that mean "this model is unavailable right now, try the next one"
# rather than "the request itself is malformed".
FALLBACK_STATUSES = {402, 403, 408, 429, 500, 502, 503, 504}


class AllModelsUnavailable(RuntimeError):
    """Every model in the chain failed."""

    def __init__(self, failures: List[Dict[str, Any]]) -> None:
        self.failures = failures
        detail = "; ".join(f"{item['model']}: {item['error']}" for item in failures)
        super().__init__(f"all free models unavailable ({detail})")

    @property
    def rate_limited(self) -> bool:
        return all(item.get("status") in {402, 429} for item in self.failures)


def is_free(model: str) -> bool:
    """A model is free-tier if OpenRouter marks it with the ":free" suffix."""
    return model.endswith(":free") or model == "openrouter/free"


def get_model_chain() -> List[str]:
    """Resolve the model chain from env, falling back to the free defaults.

    OPENROUTER_MODELS is taken as given -- it is the explicit escape hatch for
    anyone who deliberately wants a paid model. OPENROUTER_MODEL is only honoured
    as primary when it is free, so a paid value left over in the environment
    cannot quietly start billing the account.
    """
    configured = os.getenv("OPENROUTER_MODELS", "").strip()
    if configured:
        chain = [model.strip() for model in configured.split(",") if model.strip()]
        if chain:
            return chain

    primary = os.getenv("OPENROUTER_MODEL", "").strip()
    if primary and primary not in DEFAULT_MODEL_CHAIN:
        if is_free(primary):
            return [primary, *DEFAULT_MODEL_CHAIN]
        logger.warning(
            "Ignoring paid OPENROUTER_MODEL=%s; staying on the free chain. "
            "Set OPENROUTER_MODELS to override deliberately.",
            primary,
        )

    return list(DEFAULT_MODEL_CHAIN)


def _base_url() -> str:
    return os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1").rstrip("/")


def _timeout() -> int:
    try:
        return int(os.getenv("OPENROUTER_TIMEOUT", "25"))
    except ValueError:
        return 25


def complete(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """Call each model in the chain until one answers.

    Returns {"model": <model that answered>, "content": <reply>}.
    Raises AllModelsUnavailable if the whole chain fails.
    """
    headers = {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}",
        "HTTP-Referer": os.getenv("APP_URL", "https://onrender.com"),
        "X-Title": "Babosayee Super Intelligent",
        "Content-Type": "application/json",
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    failures: List[Dict[str, Any]] = []

    for model in get_model_chain():
        try:
            response = requests.post(
                f"{_base_url()}/chat/completions",
                headers=headers,
                json={"model": model, "messages": messages},
                timeout=_timeout(),
            )

            if response.status_code in FALLBACK_STATUSES:
                logger.warning(
                    "Model unavailable, trying next | model=%s status=%s",
                    model,
                    response.status_code,
                )
                failures.append(
                    {
                        "model": model,
                        "status": response.status_code,
                        "error": response.text[:200],
                    }
                )
                continue

            response.raise_for_status()
            content = (
                response.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )

            if not content:
                failures.append({"model": model, "status": None, "error": "empty response"})
                continue

            logger.info("Model answered | model=%s", model)
            return {"model": model, "content": content}

        except requests.RequestException as error:
            logger.warning("Model request failed, trying next | model=%s error=%s", model, error)
            failures.append({"model": model, "status": None, "error": str(error)[:200]})

    raise AllModelsUnavailable(failures)

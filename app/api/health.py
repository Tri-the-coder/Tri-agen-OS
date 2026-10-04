import hmac
import logging
import os
from typing import Any, Dict

import requests
from flask import Blueprint, jsonify, request

from app.agent.prompt import build_prompt
from app.db.session import db_status
from app.services import slack
from app.services.models import (
    AllModelsUnavailable,
    complete,
    get_model_chain,
    is_free,
)

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__)


def _authorized() -> bool:
    """Detailed diagnostics require HEALTH_TOKEN.

    /health is public on Render, so quota, credit and key details are only
    returned to a caller that proves it holds the token.
    """
    token = os.getenv("HEALTH_TOKEN", "").strip()
    if not token:
        return False
    provided = request.headers.get("X-Health-Token", "") or request.args.get("token", "")
    return bool(provided) and hmac.compare_digest(provided, token)


def _check_openrouter(detailed: bool) -> Dict[str, Any]:
    """Validate the API key without spending free-model quota.

    GET /key checks auth and reports quota; it does not consume a free request,
    so this stays safe to call on every health check.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return {"configured": False, "reachable": False, "error": "OPENROUTER_API_KEY is not set"}

    base_url = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1").rstrip("/")
    try:
        response = requests.get(
            f"{base_url}/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except requests.RequestException as error:
        return {"configured": True, "reachable": False, "error": str(error)[:200]}

    if response.status_code == 401:
        return {"configured": True, "reachable": True, "key_valid": False, "error": "key rejected (401)"}

    if not response.ok:
        return {
            "configured": True,
            "reachable": True,
            "key_valid": False,
            "error": f"status {response.status_code}",
        }

    result: Dict[str, Any] = {"configured": True, "reachable": True, "key_valid": True}

    if detailed:
        data = response.json().get("data", {})
        free_requests = data.get("free_model_daily_requests") or {}
        result["key_label"] = data.get("label")
        result["credit_limit"] = data.get("limit")
        result["credit_used"] = data.get("usage")
        result["expires_at"] = data.get("expires_at")
        result["free_requests_today"] = {
            "used": free_requests.get("used"),
            "limit": free_requests.get("limit"),
            "remaining": free_requests.get("remaining"),
        }
        if data.get("limit") is None:
            result["warning"] = "this key has no spending cap"

    return result


def _check_slack(detailed: bool) -> Dict[str, Any]:
    """Validate the Slack bot token with auth.test.

    auth.test is free, read-only and has no side effects, so it is safe on every
    health check. It is the only way to tell a good token from a rotated one
    without posting a message into somebody's workspace.
    """
    signing = bool(slack.signing_secret())
    token = slack.bot_token()

    if not token:
        return {"configured": False, "signing_secret_set": signing,
                "error": "SLACK_BOT_TOKEN is not set"}

    try:
        response = requests.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        return {"configured": True, "signing_secret_set": signing,
                "token_valid": False, "error": str(error)[:200]}

    if not data.get("ok"):
        return {"configured": True, "signing_secret_set": signing,
                "token_valid": False, "error": data.get("error", "unknown")}

    result: Dict[str, Any] = {"configured": True, "signing_secret_set": signing,
                              "token_valid": True}
    if detailed:
        result["team"] = data.get("team")
        result["bot_user_id"] = data.get("user_id")
        result["bot_name"] = data.get("user")
    return result


def _check_database(detailed: bool) -> Dict[str, Any]:
    status = db_status()
    if detailed:
        return status
    # Public view: enough to see it is broken, nothing about where it lives.
    return {"backend": status["backend"], "connected": status.get("connected", False)}


@health_bp.get("/health")
def health():
    """Liveness plus OpenRouter configuration status.

    Always 200 by default: if OpenRouter is down the service itself is still
    healthy and degrades gracefully, so a non-2xx here would only make Render
    restart a working process. Pass ?strict=1 for a monitor that wants 503
    when OpenRouter is unreachable.
    """
    detailed = _authorized()
    openrouter = _check_openrouter(detailed)

    chain = get_model_chain()
    models: Dict[str, Any] = {"chain_length": len(chain), "all_free": all(is_free(m) for m in chain)}
    if detailed:
        models["chain"] = chain
        models["paid_models"] = [m for m in chain if not is_free(m)]

    payload: Dict[str, Any] = {
        "status": "ok",
        "service": "tri-buddy-agent",
        "openrouter": openrouter,
        "models": models,
        "slack": _check_slack(detailed),
        "database": _check_database(detailed),
        "detail": "full" if detailed else "public",
    }

    # Opt-in: two Slack API calls, so not run on every health check.
    if detailed and request.args.get("probe") == "slack":
        payload["lead_channel"] = slack.can_post_to_lead_channel()

    # Opt-in live generation: costs one free-model request, so never automatic.
    if detailed and request.args.get("probe") == "model":
        try:
            result = complete(build_prompt(), "Reply with exactly: OK")
            payload["probe"] = {"ok": True, "answered_by": result["model"], "reply": result["content"][:80]}
        except AllModelsUnavailable as error:
            payload["probe"] = {
                "ok": False,
                "rate_limited": error.rate_limited,
                "failures": error.failures,
            }

    healthy = (
        openrouter.get("reachable")
        and openrouter.get("key_valid")
        and payload["database"].get("connected")
    )
    if request.args.get("strict") == "1" and not healthy:
        payload["status"] = "degraded"
        return jsonify(payload), 503

    if not healthy:
        payload["status"] = "degraded"

    return jsonify(payload), 200

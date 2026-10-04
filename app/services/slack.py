import hashlib
import hmac
import logging
import os
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"

# Slack signs with v0. Anything older than this is a replay attempt.
MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5

_user_name_cache: Dict[str, str] = {}


def signing_secret() -> str:
    return os.getenv("SLACK_SIGNING_SECRET", "").strip()


def bot_token() -> str:
    return os.getenv("SLACK_BOT_TOKEN", "").strip()


def is_configured() -> bool:
    return bool(signing_secret() and bot_token())


def verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Validate Slack's v0 request signature.

    Without this anyone who learns the endpoint can post whatever they like to it,
    so an unset signing secret fails closed rather than skipping the check.
    """
    secret = signing_secret()
    if not secret or not timestamp or not signature:
        return False

    try:
        sent_at = int(timestamp)
    except ValueError:
        return False

    if abs(time.time() - sent_at) > MAX_TIMESTAMP_SKEW_SECONDS:
        logger.warning("Rejected Slack request outside the replay window | ts=%s", timestamp)
        return False

    basestring = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def post_message(channel: str, text: str) -> Dict[str, Any]:
    """Send a message as the bot. Used for the delayed answer after the fast ack."""
    if not bot_token():
        return {"ok": False, "error": "SLACK_BOT_TOKEN is not set"}
    try:
        response = requests.post(
            f"{SLACK_API}/chat.postMessage",
            headers={"Authorization": f"Bearer {bot_token()}"},
            json={"channel": channel, "text": text},
            timeout=15,
        )
        data = response.json()
        if not data.get("ok"):
            logger.warning("chat.postMessage failed | error=%s", data.get("error"))
        return data
    except requests.RequestException as error:
        logger.warning("chat.postMessage request failed | error=%s", error)
        return {"ok": False, "error": str(error)}


def respond(response_url: str, text: str, in_channel: bool = False) -> None:
    """Deliver a slash-command answer after the 3-second ack has already gone out."""
    try:
        requests.post(
            response_url,
            json={
                "text": text,
                "response_type": "in_channel" if in_channel else "ephemeral",
                "replace_original": False,
            },
            timeout=15,
        )
    except requests.RequestException as error:
        logger.warning("Slash response_url delivery failed | error=%s", error)


def user_name(slack_user_id: str) -> Optional[str]:
    """Resolve U07ABC123 to a human name, cached for the life of the process."""
    if not slack_user_id:
        return None
    if slack_user_id in _user_name_cache:
        return _user_name_cache[slack_user_id]
    if not bot_token():
        return None

    try:
        response = requests.get(
            f"{SLACK_API}/users.info",
            headers={"Authorization": f"Bearer {bot_token()}"},
            params={"user": slack_user_id},
            timeout=10,
        )
        data = response.json()
    except requests.RequestException as error:
        logger.warning("users.info failed | error=%s", error)
        return None

    if not data.get("ok"):
        return None

    profile = data.get("user", {})
    name = (
        profile.get("profile", {}).get("display_name")
        or profile.get("profile", {}).get("real_name")
        or profile.get("real_name")
        or profile.get("name")
    )
    if name:
        _user_name_cache[slack_user_id] = name
    return name


def resolve_mention(token: str) -> Optional[str]:
    """Turn "<@U07ABC123|rafi>" or "@rafi" into a user id where possible."""
    token = (token or "").strip()
    if token.startswith("<@") and token.endswith(">"):
        inner = token[2:-1]
        return inner.split("|")[0] or None
    return None

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

try:  # redis is optional - the cache works without it
    import redis as redis_lib
except ImportError:  # pragma: no cover - exercised by deployments without the package
    redis_lib = None

# user_id -> (name, fetched_at). Display names change, so entries expire rather than
# living for the life of the process.
USER_CACHE_TTL_SECONDS = 60 * 60 * 6
REDIS_USER_TTL_SECONDS = 60 * 60 * 24 * 7
MAX_CACHED_USERS = 500
_user_name_cache: Dict[str, Any] = {}

_redis_client = None
_redis_resolved = False


def redis_client():
    """Connect lazily, once, and never let Redis break a request.

    REDIS_URL unset, the package missing, or the server unreachable all mean the
    same thing here: fall back to the in-process cache.
    """
    global _redis_client, _redis_resolved
    if _redis_resolved:
        return _redis_client

    _redis_resolved = True
    url = os.getenv("REDIS_URL", "").strip()
    if not url or redis_lib is None:
        return None

    try:
        client = redis_lib.from_url(
            url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        client.ping()
        _redis_client = client
        logger.info("Redis cache connected")
    except Exception as error:  # noqa: BLE001 - any redis failure means "no cache"
        logger.warning("Redis unavailable, using in-memory cache | error=%s", error)
        _redis_client = None
    return _redis_client


def _redis_get(key: str) -> Optional[str]:
    client = redis_client()
    if not client:
        return None
    try:
        return client.get(key)
    except Exception as error:  # noqa: BLE001
        logger.warning("Redis read failed | key=%s error=%s", key, error)
        return None


def _redis_set(key: str, value: str, ttl: int) -> None:
    client = redis_client()
    if not client:
        return
    try:
        client.setex(key, ttl, value)
    except Exception as error:  # noqa: BLE001
        logger.warning("Redis write failed | key=%s error=%s", key, error)


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


def _cached_name(slack_user_id: str) -> Optional[str]:
    entry = _user_name_cache.get(slack_user_id)
    if not entry:
        return None
    name, fetched_at = entry
    if time.time() - fetched_at > USER_CACHE_TTL_SECONDS:
        _user_name_cache.pop(slack_user_id, None)
        return None
    return name


def _remember_name(slack_user_id: str, name: str) -> None:
    # Bounded so a busy workspace cannot grow this without limit.
    if len(_user_name_cache) >= MAX_CACHED_USERS:
        oldest = min(_user_name_cache, key=lambda k: _user_name_cache[k][1])
        _user_name_cache.pop(oldest, None)
    _user_name_cache[slack_user_id] = (name, time.time())


def user_name(slack_user_id: str) -> Optional[str]:
    """Resolve U07ABC123 to a display name, cached with a TTL.

    Falls back to the raw id rather than None, so a caller always has something
    printable even when Slack is unreachable or the token is missing.
    """
    if not slack_user_id:
        return None

    # Bot ids are not users; users.info would just fail on them.
    if slack_user_id.startswith("B"):
        return slack_user_id

    cached = _cached_name(slack_user_id)
    if cached:
        return cached

    shared = _redis_get(f"slack:user:{slack_user_id}")
    if shared:
        _remember_name(slack_user_id, shared)
        return shared

    if not bot_token():
        return slack_user_id

    try:
        response = requests.get(
            f"{SLACK_API}/users.info",
            headers={"Authorization": f"Bearer {bot_token()}"},
            params={"user": slack_user_id},
            timeout=10,
        )
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        logger.warning("users.info failed | user=%s error=%s", slack_user_id, error)
        return slack_user_id

    if not data.get("ok"):
        logger.warning("users.info rejected | user=%s error=%s", slack_user_id, data.get("error"))
        return slack_user_id

    profile = data.get("user", {})
    name = (
        profile.get("profile", {}).get("display_name")
        or profile.get("profile", {}).get("real_name")
        or profile.get("real_name")
        or profile.get("name")
    )
    if not name:
        return slack_user_id

    _remember_name(slack_user_id, name)
    _redis_set(f"slack:user:{slack_user_id}", name, REDIS_USER_TTL_SECONDS)
    return name


def resolve_mention(token: str) -> Optional[str]:
    """Turn "<@U07ABC123|rafi>" or "@rafi" into a user id where possible."""
    token = (token or "").strip()
    if token.startswith("<@") and token.endswith(">"):
        inner = token[2:-1]
        return inner.split("|")[0] or None
    return None


def lead_channel() -> str:
    """Channel for new leads. An ID (C0…) is more reliable than a name."""
    return os.getenv("SLACK_LEAD_CHANNEL", "lead_hub").strip().lstrip("#")


def format_lead(lead: Dict[str, Any]) -> str:
    return (
        "🚀 *New Lead Captured*\n\n"
        f"• *Name:* {lead.get('name') or 'Not provided'}\n"
        f"• *Phone:* {lead.get('phone') or 'Not provided'}\n"
        f"• *Business Type:* {lead.get('business_type') or 'Not specified'}\n"
        f"• *Source:* {lead.get('source') or 'unknown'}\n"
        f"• *Score:* {lead.get('score', 0)}/100\n"
        f"• *Notes:* {lead.get('notes') or '-'}\n"
        "• *Status:* New\n"
        f"• *Lead ID:* `{lead.get('lead_ref') or lead.get('id')}`"
    )


def post_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Announce a lead in the lead channel.

    With only chat:write the bot must be a member of that channel; Slack answers
    not_in_channel otherwise, which is logged loudly because it is silent failure
    from the user's side.
    """
    result = post_message(lead_channel(), format_lead(lead))
    if not result.get("ok"):
        error = result.get("error")
        if error == "not_in_channel":
            logger.error(
                "Cannot post lead: the bot is not in #%s. Invite it with /invite @<bot>, "
                "or add the chat:write.public scope.",
                lead_channel(),
            )
        elif error == "channel_not_found":
            logger.error(
                "Cannot post lead: channel #%s not found. Set SLACK_LEAD_CHANNEL to the "
                "channel ID (C0...) rather than the name.",
                lead_channel(),
            )
    return result

import logging
import threading
from typing import Any, Dict, Optional

from flask import Blueprint, current_app, jsonify, make_response, request

from app.agent.prompt import build_prompt
from app.services import slack
from app.services.models import AllModelsUnavailable, complete
from app.tools.reports import add_report, latest_per_person, reports_for_user

logger = logging.getLogger(__name__)

slack_bp = Blueprint("slack", __name__)

ACK_TEXT = "Got it - working on that..."
RATE_LIMIT_TEXT = (
    "All free models are rate-limited right now. Try again in a few minutes, "
    "or send a shorter request."
)
ERROR_TEXT = "I ran into an issue and could not process that right now."


# --- shared helpers -------------------------------------------------------------


def _verified() -> bool:
    return slack.verify_signature(
        request.get_data(),
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    )


def _run_in_background(fn, *args) -> None:
    """Slack demands an ack within 3 seconds; model calls take far longer."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _ask_hermes(orchestrator, question: str) -> str:
    try:
        memories = orchestrator.memory.search("")
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not load stored facts | error=%s", error)
        memories = []

    try:
        reports = latest_per_person()
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not load team reports | error=%s", error)
        reports = []

    try:
        result = complete(build_prompt(memories, reports), question)
        logger.info("Slack reply | model=%s", result["model"])
        return result["content"]
    except AllModelsUnavailable as error:
        logger.warning("Free model chain exhausted on Slack | error=%s", error)
        return RATE_LIMIT_TEXT if error.rate_limited else ERROR_TEXT
    except Exception as error:  # noqa: BLE001
        logger.exception("Slack model call failed | error=%s", error)
        return ERROR_TEXT


def _format_reports(reports) -> str:
    if not reports:
        return "No reports yet."
    return "\n".join(
        f"*{r['user_name'] or r['slack_user_id']}* ({r['created_at'][:10]}): {r['text']}"
        for r in reports
    )


# --- slash commands -------------------------------------------------------------


def _handle_daily(user_id: str, user_name: Optional[str], text: str, response_url: str) -> None:
    report = add_report(user_id, user_name, text)
    slack.respond(
        response_url,
        f"Report saved for *{user_name or user_id}* ({report['created_at'][:10]}):\n>{text}",
    )


def _handle_status(target_id: str, response_url: str) -> None:
    reports = reports_for_user(target_id, limit=5)
    name = slack.user_name(target_id) or target_id
    if not reports:
        slack.respond(response_url, f"No reports from *{name}* yet.")
        return
    body = "\n".join(f"• ({r['created_at'][:10]}) {r['text']}" for r in reports)
    slack.respond(response_url, f"Last {len(reports)} report(s) from *{name}*:\n{body}")


def _handle_team(response_url: str) -> None:
    slack.respond(response_url, "*Latest from each teammate*\n" + _format_reports(latest_per_person()))


def _handle_ask(orchestrator, question: str, response_url: str) -> None:
    slack.respond(response_url, _ask_hermes(orchestrator, question))


@slack_bp.post("/slack/commands")
def slash_command():
    if not _verified():
        return make_response("invalid signature", 403)

    form = request.form
    command = (form.get("command") or "").strip().lower()
    text = (form.get("text") or "").strip()
    user_id = form.get("user_id", "")
    response_url = form.get("response_url", "")
    # Slack sends the display name with the command, which saves a users.info call.
    name = form.get("user_name") or slack.user_name(user_id)

    orchestrator = current_app.config.get("agent_orchestrator")

    if command in ("/daily", "/report"):
        if not text:
            return jsonify({"text": f"Usage: `{command} what you worked on today`"}), 200
        _run_in_background(_handle_daily, user_id, name, text, response_url)

    elif command == "/status":
        target = slack.resolve_mention(text) or user_id
        _run_in_background(_handle_status, target, response_url)

    elif command == "/team":
        _run_in_background(_handle_team, response_url)

    elif command == "/ask":
        if not text:
            return jsonify({"text": "Usage: `/ask your question`"}), 200
        _run_in_background(_handle_ask, orchestrator, text, response_url)

    else:
        return jsonify({"text": f"Unknown command: {command}"}), 200

    return jsonify({"text": ACK_TEXT}), 200


# --- events (DMs and mentions) --------------------------------------------------


def _handle_event_message(orchestrator, channel: str, question: str) -> None:
    slack.post_message(channel, _ask_hermes(orchestrator, question))


@slack_bp.post("/slack/events")
def events():
    if not _verified():
        return make_response("invalid signature", 403)

    payload: Dict[str, Any] = request.get_json(silent=True) or {}

    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge", "")}), 200

    # Slack retries anything it thinks failed. The work is already running, so a retry
    # would post the same answer twice.
    if request.headers.get("X-Slack-Retry-Num"):
        return make_response("", 200)

    event = payload.get("event") or {}
    event_type = event.get("type")

    # Never react to our own messages, or to any other bot's.
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return make_response("", 200)

    text = (event.get("text") or "").strip()
    channel = event.get("channel", "")

    if event_type == "message" and event.get("channel_type") == "im" and text and channel:
        orchestrator = current_app.config.get("agent_orchestrator")
        _run_in_background(_handle_event_message, orchestrator, channel, text)

    elif event_type == "app_mention" and text and channel:
        orchestrator = current_app.config.get("agent_orchestrator")
        _run_in_background(_handle_event_message, orchestrator, channel, text)

    return make_response("", 200)

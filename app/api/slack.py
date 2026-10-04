import logging
import threading
from typing import Any, Dict, Optional

from flask import Blueprint, current_app, jsonify, make_response, request

from app.agent.prompt import build_prompt
from app.services import slack
from app.services.models import AllModelsUnavailable, complete
from app.tools.lead_capture import extract_lead, should_auto_save
from app.tools.leads import create_lead, lead_exists, parse_lead_command, recent_leads, score_lead
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


def _handle_lead(text: str, response_url: str) -> None:
    parsed = parse_lead_command(text)
    parsed["has_business"] = bool(parsed.get("business_type"))
    parsed["buying_intent"] = True  # an explicit /lead is intent by definition
    score = score_lead(parsed)

    lead = create_lead(
        name=parsed["name"], phone=parsed["phone"],
        business_type=parsed["business_type"], notes=parsed["notes"],
        score=score, source="slack",
    )
    posted = slack.post_lead(lead)

    confirmation = f"Lead saved as `{lead['lead_ref']}` ({lead['score']}/100)."
    if posted.get("ok"):
        confirmation += f" Posted to #{slack.lead_channel()}."
    else:
        confirmation += (
            f" Could not post to #{slack.lead_channel()}: `{posted.get('error')}`. "
            "The lead is saved."
        )
    slack.respond(response_url, confirmation)


def _handle_leads(response_url: str) -> None:
    leads = recent_leads(10)
    if not leads:
        slack.respond(response_url, "No leads captured yet.")
        return
    body = "\n".join(
        f"• `{l['lead_ref']}` {l['name']} | {l['business_type'] or '-'} | "
        f"{l['phone'] or 'no phone'} | {l['score']}/100"
        for l in leads
    )
    slack.respond(response_url, f"*Recent leads*\n{body}")


def capture_lead_from(text: str, source: str) -> Optional[Dict[str, Any]]:
    """Extract and store a lead if the message carries one. Never raises."""
    try:
        lead = extract_lead(text)
        if not should_auto_save(lead):
            return None
        if lead_exists(lead.get("phone"), lead.get("name") or ""):
            logger.info("Lead already captured, skipping | phone=%s", lead.get("phone"))
            return None

        saved = create_lead(
            name=lead.get("name") or "Unknown", phone=lead.get("phone"),
            business_type=lead.get("business_type"), notes=lead.get("notes", ""),
            score=lead["score"], source=source,
        )
        slack.post_lead(saved)
        logger.info("Lead auto-captured | ref=%s score=%s", saved["lead_ref"], saved["score"])
        return saved
    except Exception as error:  # noqa: BLE001 - capture must never break the conversation
        logger.warning("Lead auto-capture failed | error=%s", error)
        return None


@slack_bp.post("/slack/commands")
def slash_command():
    if not _verified():
        return make_response("invalid signature", 403)

    form = request.form
    command = (form.get("command") or "").strip().lower()
    text = (form.get("text") or "").strip()
    user_id = form.get("user_id", "")
    response_url = form.get("response_url", "")
    # Slack's user_name field is the handle ("rafi.a"); users.info gives the display
    # name people actually recognise. Cached for 6 hours, so this is ~1 call per person
    # per session, and it falls back to the handle if Slack is unreachable.
    name = slack.user_name(user_id) or form.get("user_name") or user_id

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

    elif command == "/lead":
        if not text:
            return jsonify({"text": "Usage: `/lead Name - Business - Phone - Notes`"}), 200
        _run_in_background(_handle_lead, text, response_url)

    elif command == "/leads":
        _run_in_background(_handle_leads, response_url)

    elif command == "/ask":
        if not text:
            return jsonify({"text": "Usage: `/ask your question`"}), 200
        _run_in_background(_handle_ask, orchestrator, text, response_url)

    else:
        return jsonify({"text": f"Unknown command: {command}"}), 200

    return jsonify({"text": ACK_TEXT}), 200


# --- events (DMs and mentions) --------------------------------------------------


def _handle_event_message(orchestrator, channel: str, question: str) -> None:
    # Answer first: lead capture must never delay the reply.
    slack.post_message(channel, _ask_hermes(orchestrator, question))
    capture_lead_from(question, "slack")


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

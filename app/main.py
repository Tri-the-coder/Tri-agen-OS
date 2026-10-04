import logging
import os
import threading
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from flask import Flask, current_app, jsonify, request

from app.api.health import health_bp
from app.agent.orchestrator import AgentOrchestrator
from app.agent.prompt import build_prompt
from app.db.session import init_db
from app.services.models import AllModelsUnavailable, complete

load_dotenv()

logger = logging.getLogger(__name__)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1").rstrip("/")

RATE_LIMIT_REPLY = (
    "সব ফ্রি মডেল এখন রেট-লিমিটে আছে। কয়েক মিনিট পরে আবার চেষ্টা করুন, "
    "অথবা ছোট করে প্রশ্নটি পাঠান।\n\n"
    "All free models are rate-limited right now. Please try again in a few minutes, "
    "or send a shorter request."
)
GENERIC_ERROR_REPLY = "I ran into an issue and could not process that request right now."


def _stored_facts(orchestrator) -> list:
    """Facts the team saved via "Remember that ...", folded into the system prompt.

    A memory lookup must never cost us the reply, so failures degrade to no facts.
    """
    try:
        return orchestrator.memory.search("")
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not load stored facts | error=%s", error)
        return []


def telegram_webhook():
    payload = request.get_json(silent=True) or {}

    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if webhook_secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != webhook_secret:
        return jsonify({"status": "ignored", "reason": "invalid_secret"}), 403

    message = payload.get("message") or payload.get("edited_message") or {}
    sender = message.get("from", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    incoming_text = (message.get("text") or "").strip()

    logger.info(
        "Incoming Telegram webhook | chat_id=%s sender_id=%s text=%r",
        chat_id,
        sender.get("id"),
        incoming_text,
    )

    if sender.get("is_bot") is True:
        return jsonify({"status": "ignored", "reason": "loop_protection"}), 200

    if not incoming_text:
        return jsonify({"status": "ignored", "reason": "missing_context"}), 200

    prompt_content = incoming_text

    if chat_id is None and current_app.config["TESTING"]:
        result = current_app.config.get("agent_orchestrator").handle_message(prompt_content)
        logger.info("Testing fallback reply | chat_id=%s reply=%s", chat_id, result.get("message"))
        return jsonify(result), 200

    if chat_id is None:
        return jsonify({"status": "ignored", "reason": "missing_context"}), 200

    orchestrator = current_app.config.get("agent_orchestrator")

    # Deterministic commands ("Add task:", "Approve 3", ...) must run before the model,
    # otherwise the model only talks about them and nothing is ever written to the database.
    command_result = orchestrator.handle_message(prompt_content)
    if command_result.get("handled"):
        ai_text = command_result.get("message") or GENERIC_ERROR_REPLY
        logger.info("Command handled | chat_id=%s reply=%s", chat_id, ai_text)
    else:
        try:
            result = complete(build_prompt(_stored_facts(orchestrator)), prompt_content)
            ai_text = result["content"]
            logger.info("Hermes reply | chat_id=%s model=%s", chat_id, result["model"])

        except AllModelsUnavailable as error:
            logger.warning("Free model chain exhausted | chat_id=%s error=%s", chat_id, error)
            ai_text = RATE_LIMIT_REPLY if error.rate_limited else (
                command_result.get("message") or GENERIC_ERROR_REPLY
            )

        except Exception as error:  # noqa: BLE001 - never drop a Telegram update
            logger.exception("Model call failed | chat_id=%s error=%s", chat_id, error)
            ai_text = command_result.get("message") or GENERIC_ERROR_REPLY

    logger.info("Orchestrator reply | chat_id=%s reply=%s", chat_id, ai_text)

    telegram_payload = {"chat_id": chat_id, "text": ai_text}
    send_url = f"{TELEGRAM_API_URL}/sendMessage"

    logger.info("Sending reply to Telegram | chat_id=%s url=%s", chat_id, send_url)
    try:
        dispatch_response = requests.post(send_url, json=telegram_payload, timeout=10)
        dispatch_response.raise_for_status()
        logger.info("Telegram dispatch success | chat_id=%s status=%s", chat_id, dispatch_response.status_code)
    except Exception as error:
        logger.exception("Telegram dispatch failed | chat_id=%s error=%s", chat_id, error)
        if current_app.config["TESTING"]:
            return jsonify({"ok": False, "message": str(error)}), 500
        return jsonify({"status": "dispatch_error", "message": str(error)}), 500

    # Lead capture runs after the reply is delivered, on its own thread, so an extra
    # model call never delays the answer or risks Telegram's webhook timeout.
    if not current_app.config["TESTING"]:
        from app.api.slack import capture_lead_from

        threading.Thread(
            target=capture_lead_from, args=(prompt_content, "telegram"), daemon=True
        ).start()

    if current_app.config["TESTING"]:
        return jsonify({"ok": True, "message": ai_text}), 200

    return jsonify({"status": "success", "processed_chat": chat_id}), 200


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing
    init_db()

    orchestrator = AgentOrchestrator()
    app.config["agent_orchestrator"] = orchestrator

    @app.get("/")
    def index():
        return jsonify({"status": "ok", "service": "tri-buddy-agent"})

    from app.api.webhook import webhook_bp
    from app.api.slack import slack_bp
    app.register_blueprint(webhook_bp)
    app.register_blueprint(slack_bp)
    app.register_blueprint(health_bp)
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

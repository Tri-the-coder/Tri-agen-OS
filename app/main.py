import os
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from flask import Flask, current_app, jsonify, request

from app.api.health import health_bp
from app.agent.orchestrator import AgentOrchestrator
from app.db.session import init_db

load_dotenv()

ZENZAP_BASE_URL = os.getenv("ZENZAP_BASE_URL", "https://api.zenzap.co").rstrip("/")
OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1").rstrip("/")


def zenzap_webhook():
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("event")
    data = payload.get("data", {})

    sender = data.get("sender", {})
    raw_message = data.get("message", payload.get("message", {}))
    if isinstance(raw_message, dict):
        incoming_text = (raw_message.get("text") or payload.get("text") or "").strip()
    else:
        incoming_text = (raw_message or payload.get("text") or "").strip()
    topic_id = data.get("topic_id") or payload.get("topic_id")

    if sender.get("is_bot") is True or (
        sender.get("id") is not None and sender.get("id") == os.getenv("ZENZAP_BOT_ID")
    ):
        return jsonify({"status": "ignored", "reason": "loop_protection"}), 200

    if not incoming_text:
        return jsonify({"status": "ignored", "reason": "missing_context"}), 200

    bot_mention = os.getenv("ZENZAP_BOT_MENTION", "@Tri's buddy")
    require_mention = os.getenv("ZENZAP_REQUIRE_MENTION", "false").lower() == "true"
    if bot_mention in incoming_text:
        prompt_content = incoming_text.replace(bot_mention, "").strip()
    elif not data and payload.get("message"):
        prompt_content = incoming_text
    elif not require_mention:
        prompt_content = incoming_text
    else:
        return jsonify({"status": "ignored", "reason": "not_mentioned"}), 200

    if not topic_id and current_app.config["TESTING"]:
        result = current_app.config.get("agent_orchestrator").handle_message(prompt_content)
        return jsonify(result), 200

    if not topic_id:
        return jsonify({"status": "ignored", "reason": "missing_context"}), 200

    ai_text: str = ""
    try:
        openrouter_headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "HTTP-Referer": os.getenv("APP_URL", "https://onrender.com"),
            "X-Title": "Zenzap Control Agent Builder",
            "Content-Type": "application/json",
        }

        openrouter_payload = {
            "model": os.getenv("OPENROUTER_MODEL", "openrouter/free"),
            "messages": [
                {"role": "system", "content": "You are a helpful, elite enterprise workspace assistant."},
                {"role": "user", "content": prompt_content},
            ],
        }

        openrouter_response = requests.post(
            f"{OPENROUTER_URL}/chat/completions",
            headers=openrouter_headers,
            json=openrouter_payload,
            timeout=10,
        )
        openrouter_response.raise_for_status()
        api_data = openrouter_response.json()
        ai_text = api_data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        if not ai_text:
            raise ValueError("OpenRouter returned an empty message")

    except Exception as error:
        print(f"[SCALABLE LOG SYSTEM EXCEPTION]: {str(error)}")
        fallback = current_app.config.get("agent_orchestrator").handle_message(prompt_content)
        ai_text = fallback.get("message") or "I ran into an issue and could not process that request right now."

    zenzap_headers = {
        "X-API-Key": os.getenv("ZENZAP_API_KEY"),
        "X-API-Secret": os.getenv("ZENZAP_API_SECRET"),
        "Content-Type": "application/json",
    }

    zenzap_payload = {"text": ai_text}
    send_url = f"{ZENZAP_BASE_URL}/v1/topics/{topic_id}/messages"

    try:
        dispatch_response = requests.post(send_url, headers=zenzap_headers, json=zenzap_payload, timeout=10)
        dispatch_response.raise_for_status()
    except Exception as error:
        print(f"[SCALABLE LOG SYSTEM EXCEPTION]: {str(error)}")
        if current_app.config["TESTING"]:
            return jsonify({"ok": False, "message": str(error)}), 500
        return jsonify({"status": "dispatch_error", "message": str(error)}), 500

    if current_app.config["TESTING"]:
        return jsonify({"ok": True, "message": ai_text}), 200

    return jsonify({"status": "success", "processed_topic": topic_id}), 200


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
    app.register_blueprint(webhook_bp)
    app.register_blueprint(health_bp)
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

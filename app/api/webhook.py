import os

from flask import Blueprint, current_app, jsonify, request

from app.services.openrouter import get_openrouter_client
from zenzap_client import ZenzapClient

webhook_bp = Blueprint("webhook", __name__)
zenzap_client = ZenzapClient()
client = get_openrouter_client()


@webhook_bp.post("/api/webhook/zenzap")
def zenzap_webhook():
    payload = request.get_json(silent=True) or {}
    message = payload.get("message") or payload.get("text") or ""
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400

    orchestrator = current_app.config.get("agent_orchestrator")
    result = orchestrator.handle_message(message)

    reply_text = result.get("message") or "Done"
    if os.getenv("ZENZAP_REPLY_ENABLED", "true").lower() == "true":
        try:
            completion = client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": os.getenv("APP_URL", "https://onrender.com"),
                    "X-Title": "Zenzap Control Bot",
                },
                model=os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3-8b-instruct:free"),
                messages=[{"role": "user", "content": message}],
            )
            reply_text = completion.choices[0].message.content or reply_text
        except Exception:
            pass

        zenzap_client.send_message(reply_text)

    return jsonify(result)

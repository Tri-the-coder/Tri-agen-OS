from flask import Blueprint, current_app, jsonify, request

webhook_bp = Blueprint("webhook", __name__)


@webhook_bp.post("/api/webhook/zenzap")
def zenzap_webhook():
    payload = request.get_json(silent=True) or {}
    message = payload.get("message") or payload.get("text") or ""
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400

    orchestrator = current_app.config.get("agent_orchestrator")
    result = orchestrator.handle_message(message)
    return jsonify(result)

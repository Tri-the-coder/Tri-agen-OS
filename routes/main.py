from flask import Blueprint, current_app, jsonify, request

from services.ai import AIService

main_bp = Blueprint("main", __name__)


def register_routes(app, ai_service: AIService) -> None:
    app.register_blueprint(main_bp)
    app.extensions["ai_service"] = ai_service


@main_bp.get("/")
def index():
    return {"status": "ok", "service": "tri-buddy-agent"}


@main_bp.get("/health")
def health():
    ai_service: AIService = current_app.extensions["ai_service"]
    return {
        "status": "ok",
        "telegram": ai_service.telegram_client.health_check(),
        "openrouter": ai_service.ai_client.health_check(),
    }


@main_bp.post("/message")
def message():
    ai_service: AIService = current_app.extensions["ai_service"]
    payload = request.get_json(silent=True) or {}
    text = payload.get("message") or payload.get("text") or ""
    if not text:
        return jsonify({"ok": False, "error": "message is required"}), 400

    result = ai_service.send_to_telegram(text)
    return jsonify(result)


@main_bp.post("/chat")
def chat():
    ai_service: AIService = current_app.extensions["ai_service"]
    payload = request.get_json(silent=True) or {}
    prompt = payload.get("message") or payload.get("text") or ""
    if not prompt:
        return jsonify({"ok": False, "error": "message is required"}), 400

    session_id = payload.get("session_id") or "default"
    result = ai_service.generate_reply(prompt, session_id=session_id)
    return jsonify(result)

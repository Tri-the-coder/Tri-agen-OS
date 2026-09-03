import os
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from openrouter_client import OpenRouterClient
from telegram_client import TelegramClient

load_dotenv()

app = Flask(__name__)
telegram_client = TelegramClient()
air_client = OpenRouterClient()


@app.get("/")
def index() -> Dict[str, Any]:
    return {"status": "ok", "service": "tri-buddy-agent"}


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "telegram": telegram_client.health_check(),
        "openrouter": air_client.health_check(),
    }


@app.post("/message")
def message() -> Dict[str, Any]:
    payload = request.get_json(silent=True) or {}
    text = payload.get("message") or payload.get("text") or ""
    if not text:
        return jsonify({"ok": False, "error": "message is required"}), 400

    result = telegram_client.send_message(text)
    return jsonify(result)


@app.post("/chat")
def chat() -> Dict[str, Any]:
    payload = request.get_json(silent=True) or {}
    prompt = payload.get("message") or payload.get("text") or ""
    if not prompt:
        return jsonify({"ok": False, "error": "message is required"}), 400

    result = air_client.generate_response(prompt)
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

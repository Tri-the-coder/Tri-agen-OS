import os
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask

from routes.main import register_routes
from services.ai import AIService
from services.memory import ConversationMemory
from zenzap_client import ZenzapClient
from openrouter_client import OpenRouterClient

load_dotenv()


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing

    memory = ConversationMemory()
    zenzap_client = ZenzapClient()
    ai_client = OpenRouterClient()
    ai_service = AIService(ai_client=ai_client, memory=memory, zenzap_client=zenzap_client)

    register_routes(app, ai_service)
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

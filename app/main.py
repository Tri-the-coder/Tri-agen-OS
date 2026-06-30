import os
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, jsonify

from app.api.health import health_bp
from app.api.webhook import webhook_bp
from app.agent.orchestrator import AgentOrchestrator
from app.db.session import init_db

load_dotenv()


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing
    init_db()

    orchestrator = AgentOrchestrator()
    app.config["agent_orchestrator"] = orchestrator

    @app.get("/")
    def index():
        return jsonify({"status": "ok", "service": "tri-buddy-agent"})

    app.register_blueprint(health_bp)
    app.register_blueprint(webhook_bp)
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)

from flask import Blueprint

from app.main import telegram_webhook

# Keep existing naming structures intact so imports do not break
webhook_bp = Blueprint("webhook", __name__)


@webhook_bp.route("/api/webhook/telegram", methods=["POST"])
def proxy_webhook():
    """Redirect legacy API layer calls into the scalable engine"""
    return telegram_webhook()

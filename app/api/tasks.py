import hmac
import logging
import os
from typing import Any, Dict

from flask import Blueprint, jsonify, make_response, request

from app.services import slack
from app.tools.seo_report import build_daily_report

logger = logging.getLogger(__name__)

tasks_bp = Blueprint("tasks", __name__)


def seo_site_url() -> str:
    return os.getenv("SEO_SITE_URL", "https://babosayee.shop").strip()


def seo_channel() -> str:
    return os.getenv("SLACK_SEO_CHANNEL", "babosayee_seo").strip().lstrip("#")


def _authorized() -> bool:
    """Same admin token as the detailed health view. Fails closed when unset."""
    token = os.getenv("HEALTH_TOKEN", "").strip()
    if not token:
        return False
    provided = request.headers.get("X-Health-Token", "") or request.args.get("token", "")
    return bool(provided) and hmac.compare_digest(provided, token)


@tasks_bp.post("/tasks/seo-report")
def seo_report():
    """Run the SEO audit and post it. Triggered by an external scheduler.

    Render's free plan has no cron, so this is an endpoint rather than a job, and
    the request that wakes a sleeping instance is also the one that runs the check.
    """
    if not _authorized():
        return make_response("forbidden", 403)

    report = build_daily_report(seo_site_url())
    payload: Dict[str, Any] = {
        "url": report["audit"].get("url"),
        "reachable": report["audit"].get("reachable"),
        "passed": report["audit"].get("passed"),
        "total": report["audit"].get("total"),
        "changes": report["changes"],
    }

    if request.args.get("dry") == "1":
        payload["posted"] = False
        payload["preview"] = report["text"]
        return jsonify(payload), 200

    posted = slack.post_message(seo_channel(), report["text"])
    payload["posted"] = bool(posted.get("ok"))
    if not posted.get("ok"):
        payload["slack_error"] = posted.get("error")
        logger.error("SEO report could not be posted to #%s | error=%s",
                     seo_channel(), posted.get("error"))
    return jsonify(payload), 200

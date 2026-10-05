import logging
from typing import Any, Dict

from app.services import ga4

logger = logging.getLogger(__name__)


def _duration(seconds: str) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "?"
    return f"{total // 60}m {total % 60}s"


def _percent(value: str) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "?"


def collect() -> Dict[str, Any]:
    """Pull yesterday's numbers plus live users. Never raises."""
    if not ga4.is_configured():
        return {"ok": False, "error": "GA4 is not configured"}

    data: Dict[str, Any] = {"ok": True}
    # Each call is independent: one failing should not lose the others.
    for key, fn in (
        ("totals", ga4.totals),
        ("sources", ga4.top_sources),
        ("pages", ga4.top_pages),
        ("realtime", ga4.realtime_users),
    ):
        try:
            data[key] = fn()
        except Exception as error:  # noqa: BLE001
            logger.warning("GA4 %s failed | error=%s", key, error)
            data.setdefault("errors", {})[key] = str(error)[:150]

    if "totals" not in data and "realtime" not in data:
        return {"ok": False, "error": (data.get("errors") or {}).get("totals", "GA4 unavailable")}
    return data


def format_report(data: Dict[str, Any]) -> str:
    if not data.get("ok"):
        return (
            "📊 *Daily traffic — babosayee.shop*\n\n"
            f"Could not read Google Analytics: `{data.get('error')}`\n"
            "No numbers below, because none were retrieved."
        )

    lines = ["📊 *Daily traffic — yesterday*"]

    totals = data.get("totals")
    if totals:
        lines += [
            f"*{totals.get('active_users','?')} users* · "
            f"{totals.get('sessions','?')} sessions · "
            f"{totals.get('page_views','?')} pageviews",
            f"Avg session {_duration(totals.get('avg_session_seconds','0'))} · "
            f"bounce {_percent(totals.get('bounce_rate','0'))} · "
            f"{totals.get('conversions','0')} conversions",
        ]
    else:
        lines.append("_Yesterday's totals were not retrieved._")

    if data.get("realtime") is not None:
        lines.append(f"*{data['realtime']} active right now* (last 30 min)")

    sources = data.get("sources")
    if sources:
        lines += ["", "*Where they came from*"]
        lines += [f"• {s['source']} — {s['sessions']}" for s in sources]

    pages = data.get("pages")
    if pages:
        lines += ["", "*Most viewed*"]
        lines += [f"• `{p['path']}` — {p['views']}" for p in pages]

    if data.get("errors"):
        failed = ", ".join(data["errors"])
        lines += ["", f"_Not retrieved: {failed}. Those sections are missing, not zero._"]

    lines += ["", "_Figures straight from the GA4 Data API. Nothing here is estimated._"]
    return "\n".join(lines)


def build_daily_report() -> Dict[str, Any]:
    data = collect()
    return {"data": data, "text": format_report(data)}

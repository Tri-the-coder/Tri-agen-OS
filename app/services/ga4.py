import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

GA4_API = "https://analyticsdata.googleapis.com/v1beta"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
TIMEOUT = 20


class GA4NotConfigured(RuntimeError):
    pass


def property_id() -> str:
    return os.getenv("GA4_PROPERTY_ID", "").strip()


def is_configured() -> bool:
    return not missing_settings()


def missing_settings() -> List[str]:
    """Which GA4 settings are absent or unusable, by name."""
    missing = []
    if not property_id():
        missing.append("GA4_PROPERTY_ID")
    elif not property_id().isdigit():
        missing.append("GA4_PROPERTY_ID (must be the numeric id, not G-XXXX)")

    raw = os.getenv("GA4_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        missing.append("GA4_SERVICE_ACCOUNT_JSON")
    else:
        try:
            info = json.loads(raw)
        except ValueError:
            missing.append("GA4_SERVICE_ACCOUNT_JSON (not valid JSON)")
        else:
            for field in ("client_email", "private_key", "token_uri"):
                if not info.get(field):
                    missing.append(f"GA4_SERVICE_ACCOUNT_JSON (missing {field})")
    return missing


def config_status() -> Dict[str, Any]:
    missing = missing_settings()
    status: Dict[str, Any] = {"configured": not missing}
    if missing:
        status["missing"] = missing
    else:
        try:
            info = json.loads(os.getenv("GA4_SERVICE_ACCOUNT_JSON", "{}"))
            status["service_account"] = info.get("client_email")
            status["property_id"] = property_id()
        except ValueError:
            pass
    return status


def _credentials():
    """Build service-account credentials from the JSON key in the environment."""
    raw = os.getenv("GA4_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw or not property_id():
        raise GA4NotConfigured(
            "GA4_PROPERTY_ID and GA4_SERVICE_ACCOUNT_JSON must both be set"
        )
    try:
        info = json.loads(raw)
    except ValueError as error:
        raise GA4NotConfigured(f"GA4_SERVICE_ACCOUNT_JSON is not valid JSON: {error}")

    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])


def _access_token() -> str:
    from google.auth.transport.requests import Request

    creds = _credentials()
    creds.refresh(Request())
    return creds.token


def _call(endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
    token = _access_token()
    response = requests.post(
        f"{GA4_API}/properties/{property_id()}:{endpoint}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"GA4 {endpoint} returned {response.status_code}: "
                           f"{response.text[:200]}")
    return response.json()


def _rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for row in payload.get("rows", []) or []:
        out.append({
            "dimensions": [d.get("value", "") for d in row.get("dimensionValues", [])],
            "metrics": [m.get("value", "0") for m in row.get("metricValues", [])],
        })
    return out


def totals(start: str = "yesterday", end: str = "yesterday") -> Dict[str, Any]:
    payload = _call("runReport", {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "metrics": [{"name": n} for n in
                    ("activeUsers", "sessions", "screenPageViews",
                     "averageSessionDuration", "bounceRate", "conversions")],
    })
    rows = _rows(payload)
    values = rows[0]["metrics"] if rows else ["0"] * 6
    keys = ("active_users", "sessions", "page_views",
            "avg_session_seconds", "bounce_rate", "conversions")
    return dict(zip(keys, values))


def top_sources(limit: int = 5) -> List[Dict[str, Any]]:
    payload = _call("runReport", {
        "dateRanges": [{"startDate": "yesterday", "endDate": "yesterday"}],
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": limit,
    })
    return [{"source": r["dimensions"][0], "sessions": r["metrics"][0]} for r in _rows(payload)]


def top_pages(limit: int = 5) -> List[Dict[str, Any]]:
    payload = _call("runReport", {
        "dateRanges": [{"startDate": "yesterday", "endDate": "yesterday"}],
        "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": "screenPageViews"}],
        "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
        "limit": limit,
    })
    return [{"path": r["dimensions"][0], "views": r["metrics"][0]} for r in _rows(payload)]


def realtime_users() -> Optional[str]:
    """Active users in the last 30 minutes."""
    payload = _call("runRealtimeReport", {"metrics": [{"name": "activeUsers"}]})
    rows = _rows(payload)
    return rows[0]["metrics"][0] if rows else "0"

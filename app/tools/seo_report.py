import json
import logging
from typing import Any, Dict, List, Optional

from app.db.session import get_db_connection
from app.tools.seo import run_audit

logger = logging.getLogger(__name__)


def save_audit(audit: Dict[str, Any]) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO seo_audits (url, checked_at, passed, total, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (audit.get("url", ""), audit.get("checked_at", ""), audit.get("passed", 0),
             audit.get("total", 0), json.dumps(audit, ensure_ascii=False)),
        )
        conn.commit()


def previous_audit(url: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT payload FROM seo_audits WHERE url = ? ORDER BY id DESC LIMIT 1", (url,)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def diff_checks(current: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """What changed since the last run. Empty when there is nothing to compare."""
    if not previous:
        return {"fixed": [], "broken": []}

    before = {c["check"]: c["pass"] for c in previous.get("checks", [])}
    fixed, broken = [], []
    for check in current.get("checks", []):
        was = before.get(check["check"])
        if was is None:
            continue
        if check["pass"] and not was:
            fixed.append(check["check"])
        elif not check["pass"] and was:
            broken.append(check["check"])
    return {"fixed": fixed, "broken": broken}


def format_report(audit: Dict[str, Any], changes: Dict[str, List[str]]) -> str:
    if not audit.get("reachable"):
        return (
            f"🔴 *Daily SEO check — {audit.get('url')}*\n\n"
            f"The site could not be reached: `{audit.get('error')}`\n"
            "No checks were run, so nothing below is measured."
        )

    passed, total = audit["passed"], audit["total"]
    icon = "🟢" if passed == total else ("🟡" if passed >= total - 2 else "🔴")

    lines = [
        f"{icon} *Daily SEO check — {audit['url']}*",
        f"*{passed}/{total} checks passing* · {audit['elapsed_ms']} ms response",
        "",
    ]

    failures = [c for c in audit["checks"] if not c["pass"]]
    if failures:
        lines.append("*Needs attention*")
        lines.extend(f"• *{c['check']}* — {c['detail']}" for c in failures)
    else:
        lines.append("All technical checks passing.")

    if changes["fixed"]:
        lines += ["", "*Fixed since last check*", "• " + ", ".join(changes["fixed"])]
    if changes["broken"]:
        lines += ["", "*Newly broken*", "• " + ", ".join(changes["broken"])]

    lines += [
        "",
        "_Technical on-page checks measured from the live page. Rankings, traffic and "
        "keyword data are not included — those need Search Console, which this bot "
        "cannot read._",
    ]
    return "\n".join(lines)


def build_daily_report(base_url: str) -> Dict[str, Any]:
    audit = run_audit(base_url)
    changes = diff_checks(audit, previous_audit(audit.get("url", base_url)))
    if audit.get("reachable"):
        save_audit(audit)
    return {"audit": audit, "changes": changes, "text": format_report(audit, changes)}

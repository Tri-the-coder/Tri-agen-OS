import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "BabosayeeSEOBot/1.0 (+https://babosayee.shop)"

# Widely used ranges. They are conventions, not guarantees of ranking.
TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 120, 160


def _fetch(url: str, timeout: int = 20) -> Dict[str, Any]:
    try:
        response = requests.get(
            url, timeout=timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=True
        )
        return {
            "ok": True,
            "status": response.status_code,
            "url": response.url,
            "redirects": len(response.history),
            "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
            "bytes": len(response.content),
            "text": response.text,
        }
    except requests.RequestException as error:
        return {"ok": False, "error": str(error)[:200]}


def _check(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"check": name, "pass": bool(passed), "detail": detail}


def audit_page(url: str) -> Dict[str, Any]:
    """Technical on-page SEO for one URL.

    Everything here is measured from the live response. Nothing is estimated:
    there are no rankings, search volumes or traffic numbers, because those need
    Search Console and this has no access to it.
    """
    fetched = _fetch(url)
    if not fetched["ok"]:
        return {"url": url, "reachable": False, "error": fetched["error"], "checks": []}

    soup = BeautifulSoup(fetched["text"], "html.parser")
    checks: List[Dict[str, Any]] = []

    checks.append(_check("HTTP status", fetched["status"] == 200, f"{fetched['status']}"))
    checks.append(_check("HTTPS", urlparse(fetched["url"]).scheme == "https", fetched["url"]))
    checks.append(_check("Redirects", fetched["redirects"] <= 1,
                         f"{fetched['redirects']} redirect(s)"))
    checks.append(_check("Response time", fetched["elapsed_ms"] < 2000,
                         f"{fetched['elapsed_ms']} ms"))
    checks.append(_check("Page weight", fetched["bytes"] < 2_000_000,
                         f"{fetched['bytes'] // 1024} KB"))

    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    checks.append(_check("Title", bool(title) and TITLE_MIN <= len(title) <= TITLE_MAX,
                         f"{len(title)} chars: {title[:70] or 'MISSING'}"))

    desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = (desc_tag.get("content") or "").strip() if desc_tag else ""
    checks.append(_check("Meta description", bool(desc) and DESC_MIN <= len(desc) <= DESC_MAX,
                         f"{len(desc)} chars" if desc else "MISSING"))

    h1s = soup.find_all("h1")
    checks.append(_check("Single H1", len(h1s) == 1,
                         f"{len(h1s)} found" + (f": {h1s[0].get_text(strip=True)[:50]}" if h1s else "")))

    canonical = soup.find("link", attrs={"rel": re.compile("^canonical$", re.I)})
    checks.append(_check("Canonical", canonical is not None,
                         canonical.get("href", "") if canonical else "MISSING"))

    robots_meta = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    robots_val = (robots_meta.get("content") or "").lower() if robots_meta else ""
    checks.append(_check("Indexable", "noindex" not in robots_val, robots_val or "no robots meta"))

    viewport = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    checks.append(_check("Mobile viewport", viewport is not None,
                         "present" if viewport else "MISSING"))

    html_tag = soup.find("html")
    lang = html_tag.get("lang", "") if html_tag else ""
    checks.append(_check("html lang", bool(lang), lang or "MISSING"))

    og = {t.get("property"): t.get("content") for t in
          soup.find_all("meta", attrs={"property": re.compile("^og:", re.I)})}
    missing_og = [k for k in ("og:title", "og:description", "og:image") if not og.get(k)]
    checks.append(_check("Open Graph", not missing_og,
                         "complete" if not missing_og else f"missing {', '.join(missing_og)}"))

    images = soup.find_all("img")
    no_alt = [i for i in images if not (i.get("alt") or "").strip()]
    checks.append(_check("Image alt text", not no_alt,
                         f"{len(no_alt)}/{len(images)} missing alt" if images else "no images"))

    return {
        "url": fetched["url"],
        "reachable": True,
        "status": fetched["status"],
        "elapsed_ms": fetched["elapsed_ms"],
        "checks": checks,
        "passed": sum(1 for c in checks if c["pass"]),
        "total": len(checks),
    }


def audit_site_files(base_url: str) -> List[Dict[str, Any]]:
    """robots.txt and sitemap.xml, which live outside any single page."""
    checks = []

    robots = _fetch(urljoin(base_url, "/robots.txt"), timeout=15)
    robots_ok = robots.get("ok") and robots.get("status") == 200
    checks.append(_check("robots.txt", bool(robots_ok),
                         "found" if robots_ok else "missing or unreachable"))

    if robots_ok:
        checks.append(_check("Sitemap in robots.txt", "sitemap" in robots["text"].lower(),
                             "declared" if "sitemap" in robots["text"].lower() else "not declared"))

    sitemap = _fetch(urljoin(base_url, "/sitemap.xml"), timeout=15)
    sitemap_ok = sitemap.get("ok") and sitemap.get("status") == 200
    url_count = sitemap["text"].count("<loc>") if sitemap_ok else 0
    checks.append(_check("sitemap.xml", bool(sitemap_ok),
                         f"{url_count} URLs" if sitemap_ok else "missing or unreachable"))

    return checks


def run_audit(base_url: str) -> Dict[str, Any]:
    page = audit_page(base_url)
    if not page["reachable"]:
        return {"url": base_url, "reachable": False, "error": page.get("error"),
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    page["checks"].extend(audit_site_files(base_url))
    page["passed"] = sum(1 for c in page["checks"] if c["pass"])
    page["total"] = len(page["checks"])
    page["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return page

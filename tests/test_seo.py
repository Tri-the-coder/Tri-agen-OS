import importlib
import tempfile

import pytest

import app.api.tasks as tasks_api
import app.tools.seo as seo
import app.tools.seo_report as seo_report

GOOD_HTML = """<!doctype html><html lang="bn"><head>
<title>Babosayee — Business Operating System for Bangladeshi shops</title>
<meta name="description" content="%s">
<meta name="viewport" content="width=device-width">
<link rel="canonical" href="https://babosayee.shop">
<meta property="og:title" content="Babosayee">
<meta property="og:description" content="BOS">
<meta property="og:image" content="/og.png">
</head><body><h1>Babosayee</h1><img src="a.png" alt="a"></body></html>""" % ("x" * 130)

BAD_HTML = """<!doctype html><html><head><title>%s</title></head>
<body><h1>One</h1><h1>Two</h1><img src="a.png"></body></html>""" % ("y" * 200)


class FakeResp:
    def __init__(self, text="", status=200, url="https://babosayee.shop/"):
        self.text = text
        self.status_code = status
        self.url = url
        self.history = []
        self.content = text.encode()

    @property
    def elapsed(self):
        class E:
            @staticmethod
            def total_seconds():
                return 0.4
        return E()


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("DB_PATH", tempfile.mktemp(suffix=".db"))
    import app.db.session as session

    importlib.reload(session)
    importlib.reload(seo_report)
    session.init_db()
    return session


def _results(audit):
    return {c["check"]: c["pass"] for c in audit["checks"]}


def test_good_page_passes_the_content_checks(monkeypatch):
    monkeypatch.setattr(seo.requests, "get", lambda *a, **k: FakeResp(GOOD_HTML))
    r = _results(seo.audit_page("https://babosayee.shop"))
    assert r["Title"] and r["Meta description"] and r["Single H1"]
    assert r["Canonical"] and r["Open Graph"] and r["Image alt text"] and r["html lang"]


def test_bad_page_fails_the_right_checks(monkeypatch):
    monkeypatch.setattr(seo.requests, "get", lambda *a, **k: FakeResp(BAD_HTML))
    r = _results(seo.audit_page("https://babosayee.shop"))
    assert r["Title"] is False           # 200 chars, far over the limit
    assert r["Meta description"] is False  # missing
    assert r["Single H1"] is False       # two of them
    assert r["Canonical"] is False
    assert r["Image alt text"] is False
    assert r["html lang"] is False


def test_noindex_is_flagged(monkeypatch):
    html = '<html><head><meta name="robots" content="noindex, follow"></head><body></body></html>'
    monkeypatch.setattr(seo.requests, "get", lambda *a, **k: FakeResp(html))
    assert _results(seo.audit_page("https://x.test"))["Indexable"] is False


def test_unreachable_site_is_reported_not_guessed(monkeypatch):
    def boom(*a, **k):
        raise seo.requests.RequestException("dns failure")

    monkeypatch.setattr(seo.requests, "get", boom)
    audit = seo.run_audit("https://nope.test")
    assert audit["reachable"] is False
    assert "dns failure" in audit["error"]


def test_report_for_unreachable_site_claims_nothing(monkeypatch):
    def boom(*a, **k):
        raise seo.requests.RequestException("timeout")

    monkeypatch.setattr(seo.requests, "get", boom)
    text = seo_report.format_report(seo.run_audit("https://nope.test"), {"fixed": [], "broken": []})
    assert "could not be reached" in text
    assert "nothing below is measured" in text


def test_report_disclaims_data_it_cannot_see(monkeypatch):
    monkeypatch.setattr(seo.requests, "get", lambda *a, **k: FakeResp(GOOD_HTML))
    text = seo_report.format_report(seo.run_audit("https://babosayee.shop"),
                                    {"fixed": [], "broken": []})
    assert "Search Console" in text
    assert "not included" in text


def test_diff_detects_fixed_and_broken():
    previous = {"checks": [{"check": "Title", "pass": False}, {"check": "Canonical", "pass": True}]}
    current = {"checks": [{"check": "Title", "pass": True}, {"check": "Canonical", "pass": False}]}
    changes = seo_report.diff_checks(current, previous)
    assert changes["fixed"] == ["Title"]
    assert changes["broken"] == ["Canonical"]


def test_first_run_has_no_diff():
    assert seo_report.diff_checks({"checks": []}, None) == {"fixed": [], "broken": []}


def test_audit_is_saved_and_compared(db, monkeypatch):
    monkeypatch.setattr(seo.requests, "get", lambda *a, **k: FakeResp(BAD_HTML))
    first = seo_report.build_daily_report("https://babosayee.shop")
    assert first["changes"]["fixed"] == []

    monkeypatch.setattr(seo.requests, "get", lambda *a, **k: FakeResp(GOOD_HTML))
    second = seo_report.build_daily_report("https://babosayee.shop")
    assert "Title" in second["changes"]["fixed"]


# --- the trigger endpoint -------------------------------------------------------

def _client(monkeypatch):
    monkeypatch.setenv("HEALTH_TOKEN", "cron-token")
    import app.main as main

    return main.create_app(testing=True).test_client()


def test_trigger_requires_the_token(db, monkeypatch):
    assert _client(monkeypatch).post("/tasks/seo-report").status_code == 403


def test_trigger_rejects_a_wrong_token(db, monkeypatch):
    assert _client(monkeypatch).post("/tasks/seo-report?token=nope").status_code == 403


def test_trigger_fails_closed_without_a_configured_token(db, monkeypatch):
    monkeypatch.setenv("HEALTH_TOKEN", "")
    import app.main as main

    client = main.create_app(testing=True).test_client()
    assert client.post("/tasks/seo-report?token=").status_code == 403


def test_dry_run_does_not_post(db, monkeypatch):
    monkeypatch.setattr(seo.requests, "get", lambda *a, **k: FakeResp(GOOD_HTML))

    def explode(*a, **k):
        raise AssertionError("a dry run must not post to Slack")

    monkeypatch.setattr(tasks_api.slack, "post_message", explode)
    body = _client(monkeypatch).post("/tasks/seo-report?token=cron-token&dry=1").get_json()
    assert body["posted"] is False
    assert "preview" in body


def test_trigger_posts_the_report(db, monkeypatch):
    monkeypatch.setattr(seo.requests, "get", lambda *a, **k: FakeResp(GOOD_HTML))
    sent = {}
    monkeypatch.setattr(tasks_api.slack, "post_message",
                        lambda ch, text: sent.update(channel=ch, text=text) or {"ok": True})

    body = _client(monkeypatch).post("/tasks/seo-report?token=cron-token").get_json()
    assert body["posted"] is True
    assert sent["channel"] == "babosayee_seo"
    assert "Daily SEO check" in sent["text"]


def test_slack_failure_is_surfaced(db, monkeypatch):
    monkeypatch.setattr(seo.requests, "get", lambda *a, **k: FakeResp(GOOD_HTML))
    monkeypatch.setattr(tasks_api.slack, "post_message",
                        lambda ch, text: {"ok": False, "error": "not_in_channel"})

    body = _client(monkeypatch).post("/tasks/seo-report?token=cron-token").get_json()
    assert body["posted"] is False
    assert body["slack_error"] == "not_in_channel"

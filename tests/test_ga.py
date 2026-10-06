import pytest

import app.api.tasks as tasks_api
import app.services.ga4 as ga4
import app.tools.ga_report as ga_report

TOTALS = {"active_users": "412", "sessions": "530", "page_views": "1820",
          "avg_session_seconds": "95.4", "bounce_rate": "0.4231", "conversions": "7"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("GA4_PROPERTY_ID", raising=False)
    monkeypatch.delenv("GA4_SERVICE_ACCOUNT_JSON", raising=False)


# --- configuration --------------------------------------------------------------

def test_unconfigured_is_detected():
    assert ga4.is_configured() is False


def test_partial_configuration_is_not_configured(monkeypatch):
    monkeypatch.setenv("GA4_PROPERTY_ID", "123")
    assert ga4.is_configured() is False


def test_bad_json_gives_a_clear_error(monkeypatch):
    monkeypatch.setenv("GA4_PROPERTY_ID", "123")
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", "{not json")
    with pytest.raises(ga4.GA4NotConfigured, match="not valid JSON"):
        ga4._credentials()


def test_missing_settings_are_named(monkeypatch):
    assert ga4.missing_settings() == ["GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON"]


def test_measurement_id_instead_of_property_id_is_caught(monkeypatch):
    monkeypatch.setenv("GA4_PROPERTY_ID", "G-ABC123")
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", "{}")
    assert "numeric id" in ga4.missing_settings()[0]


def test_incomplete_key_names_the_missing_fields(monkeypatch):
    monkeypatch.setenv("GA4_PROPERTY_ID", "412345678")
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON", '{"client_email":"a@b.com"}')
    missing = " ".join(ga4.missing_settings())
    assert "private_key" in missing and "token_uri" in missing


def test_config_status_hides_the_key(monkeypatch):
    monkeypatch.setenv("GA4_PROPERTY_ID", "412345678")
    monkeypatch.setenv("GA4_SERVICE_ACCOUNT_JSON",
                       '{"client_email":"bot@p.iam.gserviceaccount.com",'
                       '"private_key":"SECRET","token_uri":"t"}')
    status = ga4.config_status()
    assert status["configured"] is True
    assert "SECRET" not in str(status), "the private key must never be echoed"


def test_report_names_the_missing_variable():
    text = ga_report.format_report({"ok": False, "error": "not configured: GA4_PROPERTY_ID"})
    assert "GA4_PROPERTY_ID" in text


def test_unconfigured_report_claims_nothing():
    text = ga_report.format_report({"ok": False, "error": "GA4 is not configured"})
    assert "Could not read Google Analytics" in text
    assert "none were retrieved" in text


# --- formatting -----------------------------------------------------------------

def test_report_renders_the_numbers():
    text = ga_report.format_report({
        "ok": True, "totals": TOTALS, "realtime": "12",
        "sources": [{"source": "Organic Search", "sessions": "300"}],
        "pages": [{"path": "/pricing", "views": "410"}],
    })
    assert "412 users" in text
    assert "530 sessions" in text
    assert "1m 35s" in text          # 95.4 seconds
    assert "42.3%" in text           # bounce rate as a percentage
    assert "12 active right now" in text
    assert "Organic Search — 300" in text
    assert "/pricing" in text


def test_report_says_nothing_is_estimated():
    text = ga_report.format_report({"ok": True, "totals": TOTALS})
    assert "Nothing here is estimated" in text


def test_missing_sections_are_flagged_not_zeroed():
    text = ga_report.format_report({
        "ok": True, "totals": TOTALS, "errors": {"pages": "403 forbidden"}})
    assert "Not retrieved: pages" in text
    assert "missing, not zero" in text


@pytest.mark.parametrize("raw,expected", [("95.4", "1m 35s"), ("0", "0m 0s"), ("x", "?")])
def test_duration_formatting(raw, expected):
    assert ga_report._duration(raw) == expected


@pytest.mark.parametrize("raw,expected", [("0.4231", "42.3%"), ("1", "100.0%"), ("x", "?")])
def test_percent_formatting(raw, expected):
    assert ga_report._percent(raw) == expected


# --- partial failure ------------------------------------------------------------

def test_one_failing_call_does_not_lose_the_rest(monkeypatch):
    monkeypatch.setattr(ga4, "missing_settings", lambda: [])
    monkeypatch.setattr(ga4, "totals", lambda: TOTALS)
    monkeypatch.setattr(ga4, "top_sources", lambda: (_ for _ in ()).throw(RuntimeError("403")))
    monkeypatch.setattr(ga4, "top_pages", lambda: [{"path": "/", "views": "10"}])
    monkeypatch.setattr(ga4, "realtime_users", lambda: "5")

    data = ga_report.collect()
    assert data["ok"] is True
    assert data["totals"] == TOTALS
    assert "sources" in data["errors"]
    assert data["pages"], "a failing call must not drop the others"


def test_total_failure_is_reported(monkeypatch):
    monkeypatch.setattr(ga4, "missing_settings", lambda: [])
    for name in ("totals", "top_sources", "top_pages", "realtime_users"):
        monkeypatch.setattr(ga4, name, lambda: (_ for _ in ()).throw(RuntimeError("no access")))
    assert ga_report.collect()["ok"] is False


# --- endpoint -------------------------------------------------------------------

def _client(monkeypatch):
    monkeypatch.setenv("HEALTH_TOKEN", "t")
    import app.main as main

    return main.create_app(testing=True).test_client()


def test_endpoint_requires_a_token(monkeypatch):
    assert _client(monkeypatch).post("/tasks/ga-report").status_code == 403


def test_dry_run_does_not_post(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("a dry run must not post")

    monkeypatch.setattr(tasks_api.slack, "post_message", explode)
    body = _client(monkeypatch).post("/tasks/ga-report?token=t&dry=1").get_json()
    assert body["posted"] is False
    assert "preview" in body


def test_report_posts_to_the_ga_channel(monkeypatch):
    monkeypatch.setenv("SLACK_GA_CHANNEL", "babosayee_seo")
    monkeypatch.setattr(ga_report, "collect", lambda: {"ok": True, "totals": TOTALS})
    sent = {}
    monkeypatch.setattr(tasks_api.slack, "post_message",
                        lambda ch, text: sent.update(channel=ch, text=text) or {"ok": True})

    body = _client(monkeypatch).post("/tasks/ga-report?token=t").get_json()
    assert body["posted"] is True
    assert sent["channel"] == "babosayee_seo"
    assert "412 users" in sent["text"]


def test_unconfigured_ga_still_posts_a_notice(monkeypatch):
    sent = {}
    monkeypatch.setattr(tasks_api.slack, "post_message",
                        lambda ch, text: sent.update(text=text) or {"ok": True})
    body = _client(monkeypatch).post("/tasks/ga-report?token=t").get_json()
    assert body["ok"] is False
    assert "Could not read Google Analytics" in sent["text"]

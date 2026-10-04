import hashlib
import hmac
import importlib
import json
import os
import tempfile
import time

import pytest

import app.api.slack as slack_api
import app.services.slack as slack_svc

SECRET = "testsigningsecret"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("DB_PATH", tempfile.mktemp(suffix=".db"))
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    import app.db.session as session

    importlib.reload(session)
    session.init_db()

    import app.main as main

    app = main.create_app(testing=True)
    # Run "background" work inline so assertions see the result.
    monkeypatch.setattr(slack_api, "_run_in_background", lambda fn, *a: fn(*a))
    return app.test_client()


def _sign(body: bytes, ts=None):
    ts = ts or str(int(time.time()))
    sig = "v0=" + hmac.new(SECRET.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def _form(**fields) -> bytes:
    from urllib.parse import urlencode

    return urlencode(fields).encode()


# --- security -------------------------------------------------------------------

def test_unsigned_command_is_rejected(client):
    body = _form(command="/team", user_id="U1", response_url="https://x")
    r = client.post("/slack/commands", data=body, content_type="application/x-www-form-urlencoded")
    assert r.status_code == 403


def test_tampered_body_is_rejected(client):
    body = _form(command="/team", user_id="U1", response_url="https://x")
    headers = _sign(body)
    tampered = _form(command="/team", user_id="UEVIL", response_url="https://x")
    r = client.post("/slack/commands", data=tampered, headers=headers,
                    content_type="application/x-www-form-urlencoded")
    assert r.status_code == 403


def test_replayed_request_is_rejected(client):
    body = _form(command="/team", user_id="U1", response_url="https://x")
    old = str(int(time.time()) - 60 * 60)
    r = client.post("/slack/commands", data=body, headers=_sign(body, ts=old),
                    content_type="application/x-www-form-urlencoded")
    assert r.status_code == 403


def test_missing_signing_secret_fails_closed(client, monkeypatch):
    body = _form(command="/team", user_id="U1", response_url="https://x")
    headers = _sign(body)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "")
    r = client.post("/slack/commands", data=body, headers=headers,
                    content_type="application/x-www-form-urlencoded")
    assert r.status_code == 403


def test_unsigned_event_is_rejected(client):
    r = client.post("/slack/events", json={"type": "url_verification", "challenge": "c"})
    assert r.status_code == 403


# --- events ---------------------------------------------------------------------

def test_url_verification_echoes_challenge(client):
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    r = client.post("/slack/events", data=body, headers=_sign(body), content_type="application/json")
    assert r.get_json()["challenge"] == "abc123"


def test_retry_is_ignored(client, monkeypatch):
    called = []
    monkeypatch.setattr(slack_api, "_ask_hermes", lambda *a: called.append(1) or "hi")

    body = json.dumps({"event": {"type": "message", "channel_type": "im",
                                 "text": "hello", "channel": "D1"}}).encode()
    headers = _sign(body)
    headers["X-Slack-Retry-Num"] = "1"
    r = client.post("/slack/events", data=body, headers=headers, content_type="application/json")

    assert r.status_code == 200
    assert called == [], "a retry must not produce a second answer"


def test_bot_messages_are_ignored(client, monkeypatch):
    called = []
    monkeypatch.setattr(slack_api, "_ask_hermes", lambda *a: called.append(1) or "hi")

    body = json.dumps({"event": {"type": "message", "channel_type": "im", "bot_id": "B1",
                                 "text": "loop", "channel": "D1"}}).encode()
    client.post("/slack/events", data=body, headers=_sign(body), content_type="application/json")
    assert called == [], "replying to a bot message would loop"


def test_dm_reaches_hermes_and_posts_back(client, monkeypatch):
    posted = {}
    monkeypatch.setattr(slack_api, "_ask_hermes", lambda o, q: f"answer to {q}")
    monkeypatch.setattr(slack_api.slack, "post_message",
                        lambda ch, text: posted.update(channel=ch, text=text) or {"ok": True})

    body = json.dumps({"event": {"type": "message", "channel_type": "im",
                                 "text": "what is Rafi doing?", "channel": "D1"}}).encode()
    client.post("/slack/events", data=body, headers=_sign(body), content_type="application/json")

    assert posted["channel"] == "D1"
    assert posted["text"] == "answer to what is Rafi doing?"


# --- slash commands -------------------------------------------------------------

def test_daily_saves_a_report(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(slack_api.slack, "respond",
                        lambda url, text, **k: sent.update(text=text))

    body = _form(command="/daily", text="shipped the pricing page", user_id="U1",
                 user_name="rafi", response_url="https://hooks.slack.com/x")
    r = client.post("/slack/commands", data=body, headers=_sign(body),
                    content_type="application/x-www-form-urlencoded")

    assert r.status_code == 200
    assert r.get_json()["text"] == slack_api.ACK_TEXT  # the fast ack

    from app.tools.reports import reports_for_user

    saved = reports_for_user("U1")
    assert saved[0]["text"] == "shipped the pricing page"
    assert "Report saved" in sent["text"]


def test_daily_without_text_shows_usage(client):
    body = _form(command="/daily", text="", user_id="U1", response_url="https://x")
    r = client.post("/slack/commands", data=body, headers=_sign(body),
                    content_type="application/x-www-form-urlencoded")
    assert "Usage:" in r.get_json()["text"]


def test_status_defaults_to_the_caller(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(slack_api.slack, "respond", lambda url, text, **k: sent.update(text=text))
    monkeypatch.setattr(slack_api.slack, "user_name", lambda uid: "rafi")

    from app.tools.reports import add_report

    add_report("U1", "rafi", "did the SEO audit")

    body = _form(command="/status", text="", user_id="U1", response_url="https://x")
    client.post("/slack/commands", data=body, headers=_sign(body),
                content_type="application/x-www-form-urlencoded")
    assert "did the SEO audit" in sent["text"]


def test_status_accepts_a_mentioned_user(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(slack_api.slack, "respond", lambda url, text, **k: sent.update(text=text))
    monkeypatch.setattr(slack_api.slack, "user_name", lambda uid: "sumi")

    from app.tools.reports import add_report

    add_report("U2", "sumi", "fixed the checkout bug")

    body = _form(command="/status", text="<@U2|sumi>", user_id="U1", response_url="https://x")
    client.post("/slack/commands", data=body, headers=_sign(body),
                content_type="application/x-www-form-urlencoded")
    assert "fixed the checkout bug" in sent["text"]


def test_team_lists_latest_per_person(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(slack_api.slack, "respond", lambda url, text, **k: sent.update(text=text))

    from app.tools.reports import add_report

    add_report("U1", "rafi", "old news")
    add_report("U1", "rafi", "latest from rafi")
    add_report("U2", "sumi", "latest from sumi")

    body = _form(command="/team", text="", user_id="U1", response_url="https://x")
    client.post("/slack/commands", data=body, headers=_sign(body),
                content_type="application/x-www-form-urlencoded")

    assert "latest from rafi" in sent["text"]
    assert "latest from sumi" in sent["text"]
    assert "old news" not in sent["text"]


def test_unknown_command_is_handled(client):
    body = _form(command="/nope", text="", user_id="U1", response_url="https://x")
    r = client.post("/slack/commands", data=body, headers=_sign(body),
                    content_type="application/x-www-form-urlencoded")
    assert "Unknown command" in r.get_json()["text"]


def test_rate_limited_chain_is_reported(client, monkeypatch):
    import app.services.models as models

    def boom(*a, **k):
        raise models.AllModelsUnavailable([{"model": "m", "status": 429, "error": "rate"}])

    monkeypatch.setattr(slack_api, "complete", boom)

    class FakeOrch:
        class memory:
            @staticmethod
            def search(_):
                return []

    assert slack_api._ask_hermes(FakeOrch(), "hi") == slack_api.RATE_LIMIT_TEXT

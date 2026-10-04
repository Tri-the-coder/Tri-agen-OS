import app.api.health as health
import app.services.models as models
from app.main import create_app

KEY_PAYLOAD = {
    "data": {
        "label": "sk-or-v1-725...e93",
        "limit": 5,
        "usage": 0.0,
        "expires_at": "2027-04-02T19:52:01.790Z",
        "free_model_daily_requests": {"used": 4, "limit": 1000, "remaining": 996},
    }
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def _client(monkeypatch, *, status=200, payload=None, token="s3cret"):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setenv("HEALTH_TOKEN", token)
    monkeypatch.setattr(
        health.requests, "get", lambda *a, **k: FakeResponse(status, payload or KEY_PAYLOAD)
    )
    return create_app(testing=True).test_client()


def test_public_health_is_ok_and_leaks_nothing(monkeypatch):
    client = _client(monkeypatch)
    response = client.get("/health")
    body = response.get_json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["detail"] == "public"
    assert body["openrouter"] == {"configured": True, "reachable": True, "key_valid": True}

    # None of the sensitive fields may appear without the token.
    flat = str(body)
    for secret in ("sk-or-v1", "credit_limit", "free_requests_today", "expires_at", "996"):
        assert secret not in flat
    assert "chain" not in body["models"]
    assert body["models"]["all_free"] is True


def test_token_unlocks_diagnostics(monkeypatch):
    client = _client(monkeypatch)
    body = client.get("/health?token=s3cret").get_json()

    assert body["detail"] == "full"
    assert body["openrouter"]["free_requests_today"]["remaining"] == 996
    assert body["openrouter"]["credit_limit"] == 5
    assert body["models"]["chain"] == models.get_model_chain()


def test_token_also_accepted_as_header(monkeypatch):
    client = _client(monkeypatch)
    body = client.get("/health", headers={"X-Health-Token": "s3cret"}).get_json()
    assert body["detail"] == "full"


def test_wrong_token_stays_public(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/health?token=nope").get_json()["detail"] == "public"


def test_unset_token_cannot_be_bypassed(monkeypatch):
    """An empty HEALTH_TOKEN must not let an empty query param unlock details."""
    client = _client(monkeypatch, token="")
    assert client.get("/health?token=").get_json()["detail"] == "public"


def test_rejected_key_reports_degraded(monkeypatch):
    client = _client(monkeypatch, status=401)
    body = client.get("/health").get_json()
    assert body["status"] == "degraded"
    assert body["openrouter"]["key_valid"] is False


def test_degraded_still_returns_200_by_default(monkeypatch):
    """Render must not restart a healthy process just because OpenRouter is down."""
    client = _client(monkeypatch, status=401)
    assert client.get("/health").status_code == 200


def test_strict_mode_returns_503_when_degraded(monkeypatch):
    client = _client(monkeypatch, status=401)
    assert client.get("/health?strict=1").status_code == 503


def test_missing_api_key_is_reported(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("HEALTH_TOKEN", "s3cret")
    client = create_app(testing=True).test_client()
    body = client.get("/health").get_json()
    assert body["openrouter"]["configured"] is False
    assert body["status"] == "degraded"


def test_uncapped_key_is_flagged(monkeypatch):
    payload = {"data": dict(KEY_PAYLOAD["data"], limit=None)}
    client = _client(monkeypatch, payload=payload)
    body = client.get("/health?token=s3cret").get_json()
    assert body["openrouter"]["warning"] == "this key has no spending cap"


def test_model_probe_is_opt_in(monkeypatch):
    """A plain health check must never spend free-model quota."""
    def exploding_complete(*a, **k):
        raise AssertionError("/health must not generate unless probe=model")

    monkeypatch.setattr("app.api.health.complete", exploding_complete)
    client = _client(monkeypatch)
    assert client.get("/health?token=s3cret").status_code == 200


def test_model_probe_reports_which_model_answered(monkeypatch):
    monkeypatch.setattr(
        "app.api.health.complete",
        lambda *a, **k: {"model": "nvidia/nemotron-3-ultra-550b-a55b:free", "content": "OK"},
    )
    client = _client(monkeypatch)
    probe = client.get("/health?token=s3cret&probe=model").get_json()["probe"]
    assert probe["ok"] is True
    assert probe["answered_by"] == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_model_probe_reports_rate_limit(monkeypatch):
    failures = [{"model": "m", "status": 429, "error": "rate limited"}]

    def rate_limited(*a, **k):
        raise models.AllModelsUnavailable(failures)

    monkeypatch.setattr("app.api.health.complete", rate_limited)
    client = _client(monkeypatch)
    probe = client.get("/health?token=s3cret&probe=model").get_json()["probe"]
    assert probe["ok"] is False
    assert probe["rate_limited"] is True


# --- slack token check ----------------------------------------------------------

def _slack_client(monkeypatch, auth_payload, *, token="xoxb-test", signing="s3cret"):
    import app.api.health as health_mod

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setenv("HEALTH_TOKEN", "s3cret-health")
    monkeypatch.setenv("SLACK_BOT_TOKEN", token)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", signing)
    monkeypatch.setattr(health_mod.requests, "get", lambda *a, **k: FakeResponse(200, KEY_PAYLOAD))
    monkeypatch.setattr(health_mod.requests, "post", lambda *a, **k: FakeResponse(200, auth_payload))
    return create_app(testing=True).test_client()


def test_valid_slack_token_reported(monkeypatch):
    client = _slack_client(monkeypatch, {"ok": True, "team": "Babosayee",
                                         "user_id": "U0BOT", "user": "secondbrain"})
    body = client.get("/health").get_json()
    assert body["slack"]["token_valid"] is True
    assert body["slack"]["signing_secret_set"] is True


def test_rotated_or_bad_slack_token_reported(monkeypatch):
    client = _slack_client(monkeypatch, {"ok": False, "error": "invalid_auth"})
    body = client.get("/health").get_json()
    assert body["slack"]["token_valid"] is False
    assert body["slack"]["error"] == "invalid_auth"


def test_missing_slack_token_reported(monkeypatch):
    client = _slack_client(monkeypatch, {"ok": True}, token="")
    body = client.get("/health").get_json()
    assert body["slack"]["configured"] is False


def test_public_health_hides_slack_workspace_details(monkeypatch):
    client = _slack_client(monkeypatch, {"ok": True, "team": "Babosayee",
                                         "user_id": "U0BOT", "user": "secondbrain"})
    body = client.get("/health").get_json()
    assert "team" not in body["slack"]
    assert "bot_user_id" not in body["slack"]
    assert "Babosayee" not in str(body)


def test_token_reveals_slack_workspace_details(monkeypatch):
    client = _slack_client(monkeypatch, {"ok": True, "team": "Babosayee",
                                         "user_id": "U0BOT", "user": "secondbrain"})
    body = client.get("/health?token=s3cret-health").get_json()
    assert body["slack"]["team"] == "Babosayee"
    assert body["slack"]["bot_user_id"] == "U0BOT"


# --- lead channel access probe --------------------------------------------------

def test_lead_channel_probe_reports_access(monkeypatch):
    import app.services.slack as slack_svc

    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if "scheduleMessage" in url:
            return FakeResponse(200, {"ok": True, "scheduled_message_id": "Q1", "channel": "C1"})
        return FakeResponse(200, {"ok": True})

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_svc.requests, "post", fake_post)

    result = slack_svc.can_post_to_lead_channel()
    assert result["can_post"] is True
    assert any("deleteScheduledMessage" in c for c in calls), "the probe must cancel itself"


def test_lead_channel_probe_detects_not_in_channel(monkeypatch):
    import app.services.slack as slack_svc

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_svc.requests, "post",
                        lambda *a, **k: FakeResponse(200, {"ok": False, "error": "not_in_channel"}))

    result = slack_svc.can_post_to_lead_channel()
    assert result["can_post"] is False
    assert result["error"] == "not_in_channel"


def test_lead_channel_probe_flags_failed_cleanup(monkeypatch):
    import app.services.slack as slack_svc

    def fake_post(url, **kwargs):
        if "scheduleMessage" in url:
            return FakeResponse(200, {"ok": True, "scheduled_message_id": "Q1", "channel": "C1"})
        return FakeResponse(200, {"ok": False, "error": "invalid_scheduled_message_id"})

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_svc.requests, "post", fake_post)

    result = slack_svc.can_post_to_lead_channel()
    assert result["can_post"] is True
    assert "cleanup_failed" in result, "a message that could not be cancelled must be surfaced"


# --- database status ------------------------------------------------------------

def test_healthy_sqlite_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _slack_client(monkeypatch, {"ok": True})
    body = client.get("/health").get_json()
    assert body["database"]["backend"] == "sqlite"
    assert body["database"]["connected"] is True


def test_unreachable_database_degrades_but_serves(monkeypatch):
    """A bad DATABASE_URL must still leave /health answerable."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:bad@127.0.0.1:1/none")
    monkeypatch.setenv("DB_SCHEMA", "tri_buddy")
    client = _slack_client(monkeypatch, {"ok": True})
    response = client.get("/health")

    assert response.status_code == 200, "the app must stay diagnosable"
    body = response.get_json()
    assert body["database"]["connected"] is False
    assert body["status"] == "degraded"


def test_bad_database_url_does_not_stop_boot(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:bad@127.0.0.1:1/none")
    monkeypatch.setenv("DB_SCHEMA", "tri_buddy")
    import app.main as main

    app = main.create_app(testing=True)  # must not raise
    assert app is not None


def test_public_view_hides_the_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _slack_client(monkeypatch, {"ok": True})
    assert "schema" not in client.get("/health").get_json()["database"]


def test_token_view_shows_the_schema(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:bad@127.0.0.1:1/none")
    monkeypatch.setenv("DB_SCHEMA", "tri_buddy")
    client = _slack_client(monkeypatch, {"ok": True})
    body = client.get("/health?token=s3cret-health").get_json()
    assert body["database"]["schema"] == "tri_buddy"

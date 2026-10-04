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

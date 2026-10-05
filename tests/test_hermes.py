import app.services.models as models
from app.agent.prompt import build_prompt
from app.main import RATE_LIMIT_REPLY, create_app


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise models.requests.HTTPError(f"status {self.status_code}")


def _reply(text: str):
    return FakeResponse(200, {"choices": [{"message": {"content": text}}]})


def test_default_chain_is_all_free(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODELS", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    chain = models.get_model_chain()
    assert chain == models.DEFAULT_MODEL_CHAIN
    assert all(models.is_free(model) for model in chain)


def test_paid_primary_is_ignored(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODELS", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-opus-4")
    assert models.get_model_chain() == models.DEFAULT_MODEL_CHAIN


def test_free_primary_is_prepended(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODELS", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL", "some/model:free")
    assert models.get_model_chain()[0] == "some/model:free"


def test_explicit_models_env_wins(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODELS", "one/a:free, two/b:free")
    assert models.get_model_chain() == ["one/a:free", "two/b:free"]


def test_falls_back_past_rate_limited_model(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODELS", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if len(calls) == 1:
            return FakeResponse(429, text="rate limited")
        return _reply("second model answered")

    monkeypatch.setattr(models.requests, "post", fake_post)
    result = models.complete("system", "user")

    assert result["content"] == "second model answered"
    assert result["model"] == models.DEFAULT_MODEL_CHAIN[1]
    assert calls == models.DEFAULT_MODEL_CHAIN[:2]


def test_empty_response_also_falls_through(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODELS", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    responses = [_reply("   "), _reply("real answer")]
    monkeypatch.setattr(
        models.requests, "post", lambda *a, **k: responses.pop(0)
    )
    assert models.complete("system", "user")["content"] == "real answer"


def test_exhausted_chain_reports_rate_limited(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODELS", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setattr(
        models.requests, "post", lambda *a, **k: FakeResponse(429, text="rate limited")
    )
    try:
        models.complete("system", "user")
    except models.AllModelsUnavailable as error:
        assert error.rate_limited is True
        assert len(error.failures) == len(models.DEFAULT_MODEL_CHAIN)
    else:
        raise AssertionError("expected AllModelsUnavailable")


def test_commands_run_before_the_model(monkeypatch):
    """"Add task:" must write to the database, not just get discussed by the model."""
    def exploding_complete(*args, **kwargs):
        raise AssertionError("the model must not be called for a known command")

    # app.main and app.services.models share the same `requests` module object, so the
    # model boundary is patched by name rather than through requests.post.
    monkeypatch.setattr("app.main.complete", exploding_complete)
    monkeypatch.setattr(models.requests, "post", lambda *a, **k: FakeResponse(200, {"ok": True}))

    client = create_app(testing=True).test_client()
    response = client.post(
        "/api/webhook/telegram",
        json={"message": {"text": "Add task: ship the webhook", "chat": {"id": 42}}},
    )

    assert response.status_code == 200
    assert "টাস্ক" in response.get_json()["message"]


def test_rate_limited_chain_tells_the_user(monkeypatch):
    failures = [
        {"model": model, "status": 429, "error": "rate limited"}
        for model in models.DEFAULT_MODEL_CHAIN
    ]

    def rate_limited_complete(*args, **kwargs):
        raise models.AllModelsUnavailable(failures)

    monkeypatch.setattr("app.main.complete", rate_limited_complete)
    monkeypatch.setattr(models.requests, "post", lambda *a, **k: FakeResponse(200, {"ok": True}))

    client = create_app(testing=True).test_client()
    response = client.post(
        "/api/webhook/telegram",
        json={"message": {"text": "give me a growth plan", "chat": {"id": 42}}},
    )

    assert response.get_json()["message"] == RATE_LIMIT_REPLY


def test_model_outage_falls_back_to_the_orchestrator(monkeypatch):
    """A non-rate-limit outage should still return the deterministic reply, not an error."""
    failures = [{"model": "m", "status": 500, "error": "server error"}]

    def broken_complete(*args, **kwargs):
        raise models.AllModelsUnavailable(failures)

    monkeypatch.setattr("app.main.complete", broken_complete)
    monkeypatch.setattr(models.requests, "post", lambda *a, **k: FakeResponse(200, {"ok": True}))

    client = create_app(testing=True).test_client()
    response = client.post(
        "/api/webhook/telegram",
        json={"message": {"text": "hello", "chat": {"id": 42}}},
    )

    assert response.get_json()["message"] == "হ্যালো! আমি ট্রাই বাডি ওএস 🚀"


def test_prompt_states_language_matching_and_free_only():
    prompt = build_prompt()
    assert "ব্যবসায়ী সুপার ইন্টেলিজেন্ট" in prompt
    assert "Bengali in, Bengali out" in prompt
    assert "free OpenRouter models" in prompt
    for model in models.DEFAULT_MODEL_CHAIN:
        assert model in prompt

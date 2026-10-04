import time

import pytest

import app.services.slack as slack


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _ok(display="", real="", name=""):
    return FakeResponse({"ok": True, "user": {
        "profile": {"display_name": display, "real_name": real},
        "real_name": real, "name": name,
    }})


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    slack._user_name_cache.clear()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    yield
    slack._user_name_cache.clear()


def test_resolves_display_name(monkeypatch):
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok(display="rafi"))
    assert slack.user_name("U1") == "rafi"


def test_prefers_display_then_real_then_handle(monkeypatch):
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok(real="Rafi Ahmed"))
    assert slack.user_name("U1") == "Rafi Ahmed"

    slack._user_name_cache.clear()
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok(name="rafi.a"))
    assert slack.user_name("U2") == "rafi.a"


def test_second_lookup_is_served_from_cache(monkeypatch):
    calls = []

    def counting_get(*a, **k):
        calls.append(1)
        return _ok(display="rafi")

    monkeypatch.setattr(slack.requests, "get", counting_get)
    assert slack.user_name("U1") == "rafi"
    assert slack.user_name("U1") == "rafi"
    assert len(calls) == 1, "a cached name must not hit the Slack API again"


def test_cache_expires_after_ttl(monkeypatch):
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok(display="old name"))
    assert slack.user_name("U1") == "old name"

    # Pretend the entry was fetched longer ago than the TTL allows.
    name, _ = slack._user_name_cache["U1"]
    slack._user_name_cache["U1"] = (name, time.time() - slack.USER_CACHE_TTL_SECONDS - 1)

    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok(display="new name"))
    assert slack.user_name("U1") == "new name", "a renamed user must refresh after the TTL"


def test_bot_ids_skip_the_api(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("users.info must not be called for a bot id")

    monkeypatch.setattr(slack.requests, "get", explode)
    assert slack.user_name("B123") == "B123"


def test_api_error_falls_back_to_the_id(monkeypatch):
    monkeypatch.setattr(slack.requests, "get",
                        lambda *a, **k: FakeResponse({"ok": False, "error": "user_not_found"}))
    assert slack.user_name("U404") == "U404"


def test_network_failure_falls_back_to_the_id(monkeypatch):
    def boom(*a, **k):
        raise slack.requests.RequestException("no network")

    monkeypatch.setattr(slack.requests, "get", boom)
    assert slack.user_name("U1") == "U1"


def test_missing_token_falls_back_to_the_id(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "")
    assert slack.user_name("U1") == "U1"


def test_failures_are_not_cached(monkeypatch):
    monkeypatch.setattr(slack.requests, "get",
                        lambda *a, **k: FakeResponse({"ok": False, "error": "ratelimited"}))
    slack.user_name("U1")
    assert "U1" not in slack._user_name_cache

    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok(display="rafi"))
    assert slack.user_name("U1") == "rafi", "a transient failure must not poison the cache"


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok(display="n"))
    for i in range(slack.MAX_CACHED_USERS + 30):
        slack._remember_name(f"U{i}", f"name{i}")
    assert len(slack._user_name_cache) <= slack.MAX_CACHED_USERS

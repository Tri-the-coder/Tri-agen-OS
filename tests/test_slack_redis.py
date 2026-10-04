import pytest

import app.services.slack as slack


class FakeRedis:
    def __init__(self, fail_on=None):
        self.store = {}
        self.fail_on = fail_on or set()
        self.gets = 0

    def ping(self):
        if "ping" in self.fail_on:
            raise ConnectionError("redis down")
        return True

    def get(self, key):
        self.gets += 1
        if "get" in self.fail_on:
            raise ConnectionError("redis down")
        return self.store.get(key)

    def setex(self, key, ttl, value):
        if "setex" in self.fail_on:
            raise ConnectionError("redis down")
        self.store[key] = value


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _ok(display):
    return FakeResponse({"ok": True, "user": {"profile": {"display_name": display}}})


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    slack._user_name_cache.clear()
    slack._redis_client = None
    slack._redis_resolved = False
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    yield
    slack._user_name_cache.clear()
    slack._redis_client = None
    slack._redis_resolved = False


def _use(monkeypatch, fake):
    class FakeLib:
        @staticmethod
        def from_url(url, **kwargs):
            return fake

    monkeypatch.setattr(slack, "redis_lib", FakeLib)


def test_name_is_written_to_redis(monkeypatch):
    fake = FakeRedis()
    _use(monkeypatch, fake)
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok("rafi"))

    assert slack.user_name("U1") == "rafi"
    assert fake.store["slack:user:U1"] == "rafi"


def test_redis_hit_skips_the_slack_api(monkeypatch):
    fake = FakeRedis()
    fake.store["slack:user:U1"] = "rafi-from-redis"
    _use(monkeypatch, fake)

    def explode(*a, **k):
        raise AssertionError("a Redis hit must not call users.info")

    monkeypatch.setattr(slack.requests, "get", explode)
    assert slack.user_name("U1") == "rafi-from-redis"


def test_redis_hit_populates_the_local_cache(monkeypatch):
    """A second lookup should not even touch Redis."""
    fake = FakeRedis()
    fake.store["slack:user:U1"] = "rafi"
    _use(monkeypatch, fake)
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok("unused"))

    slack.user_name("U1")
    before = fake.gets
    slack.user_name("U1")
    assert fake.gets == before


def test_unreachable_redis_does_not_break_lookups(monkeypatch):
    _use(monkeypatch, FakeRedis(fail_on={"ping"}))
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok("rafi"))
    assert slack.user_name("U1") == "rafi"


def test_redis_read_failure_falls_through_to_the_api(monkeypatch):
    _use(monkeypatch, FakeRedis(fail_on={"get"}))
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok("rafi"))
    assert slack.user_name("U1") == "rafi"


def test_redis_write_failure_still_returns_the_name(monkeypatch):
    _use(monkeypatch, FakeRedis(fail_on={"setex"}))
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok("rafi"))
    assert slack.user_name("U1") == "rafi"


def test_no_redis_url_uses_memory_only(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    _use(monkeypatch, FakeRedis())
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok("rafi"))

    assert slack.user_name("U1") == "rafi"
    assert slack.redis_client() is None


def test_missing_redis_package_is_tolerated(monkeypatch):
    monkeypatch.setattr(slack, "redis_lib", None)
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok("rafi"))

    assert slack.user_name("U1") == "rafi"
    assert slack.redis_client() is None


def test_connection_is_attempted_only_once(monkeypatch):
    attempts = []

    class CountingLib:
        @staticmethod
        def from_url(url, **kwargs):
            attempts.append(1)
            raise ConnectionError("down")

    monkeypatch.setattr(slack, "redis_lib", CountingLib)
    monkeypatch.setattr(slack.requests, "get", lambda *a, **k: _ok("rafi"))

    slack.user_name("U1")
    slack._user_name_cache.clear()
    slack.user_name("U2")
    assert len(attempts) == 1, "a dead Redis must not be retried on every lookup"

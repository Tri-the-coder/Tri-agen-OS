import importlib
import os
import tempfile

import pytest

from app.agent.prompt import (
    MAX_MEMORY_ITEMS,
    MAX_MEMORY_VALUE_CHARS,
    build_prompt,
)


@pytest.fixture()
def fresh_db(monkeypatch):
    """Point every DB helper at a throwaway file."""
    path = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", path)

    import app.db.session as session

    importlib.reload(session)
    yield session
    if os.path.exists(path):
        os.remove(path)


# --- persistence: the DROP TABLE regression -------------------------------------

def test_restart_does_not_wipe_tasks_and_leads(fresh_db):
    """init_db() runs on every app start, so it must never drop existing rows."""
    fresh_db.init_db()
    with fresh_db.get_db_connection() as conn:
        conn.execute("INSERT INTO tasks (title) VALUES ('ship the deploy')")
        conn.execute("INSERT INTO leads (name) VALUES ('ABC Store')")
        conn.commit()

    fresh_db.init_db()  # simulate a restart

    with fresh_db.get_db_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1


def test_init_db_is_idempotent(fresh_db):
    for _ in range(3):
        fresh_db.init_db()
    with fresh_db.get_db_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


# --- memory injection -----------------------------------------------------------

def test_stored_facts_reach_the_prompt():
    prompt = build_prompt([
        {"key": "default SaaS package", "value": "299 BDT"},
        {"key": "brand voice", "value": "direct, no hype"},
    ])
    assert "default SaaS package: 299 BDT" in prompt
    assert "brand voice: direct, no hype" in prompt


def test_no_facts_asks_the_team_to_save_some():
    prompt = build_prompt([])
    assert "Nothing stored yet" in prompt
    assert "Remember that my" in prompt


def test_long_values_are_truncated():
    prompt = build_prompt([{"key": "notes", "value": "x" * 1000}])
    assert "x" * MAX_MEMORY_VALUE_CHARS + "..." in prompt
    assert "x" * (MAX_MEMORY_VALUE_CHARS + 1) not in prompt


def test_memory_list_is_capped():
    facts = [{"key": f"k{i}", "value": f"v{i}"} for i in range(MAX_MEMORY_ITEMS + 25)]
    prompt = build_prompt(facts)
    assert f"k{MAX_MEMORY_ITEMS - 1}: " in prompt
    assert f"k{MAX_MEMORY_ITEMS}: " not in prompt


def test_blank_entries_are_skipped():
    prompt = build_prompt([{"key": "", "value": "orphan"}, {"key": "real", "value": "kept"}])
    assert "real: kept" in prompt
    assert "orphan" not in prompt


def test_build_prompt_still_works_with_no_argument():
    assert "Hermes" in build_prompt()


# --- capability honesty ---------------------------------------------------------

def test_prompt_forbids_inventing_metrics():
    prompt = build_prompt()
    assert "zero web access" in prompt
    assert "Never invent, estimate, assume or hallucinate any metric" in prompt
    assert "Never claim you checked, visited, crawled, measured or monitored anything." in prompt


def test_prompt_specifies_the_exact_missing_data_phrase():
    assert '"Not provided in the data you shared."' in build_prompt()


def test_prompt_defines_the_response_structure():
    prompt = build_prompt()
    for heading in ("What I can see from your data", "Key observations / analysis",
                    "Recommendations or drafted output",
                    "What additional data would make this stronger"):
        assert heading in prompt


def test_prompt_requires_separating_fact_from_interpretation():
    prompt = build_prompt()
    assert "Separate fact from interpretation" in prompt
    assert "never present reasoning" in prompt


def test_prompt_forbids_inventing_product_facts():
    prompt = build_prompt()
    assert "NEVER INVENT PRODUCT FACTS" in prompt
    assert "[FEATURES]" in prompt
    assert "Bangladesh uses VAT, not GST" in prompt


def test_prompt_covers_the_second_brain_jobs():
    prompt = build_prompt()
    for job in ("lead generation", "social media and branding", "PR review", "SEO",
                "interpreting analytics", "customer support"):
        assert job in prompt


def test_prompt_is_internal_facing_not_merchant_facing():
    prompt = build_prompt()
    assert "You work for that team, not for their customers." in prompt


# --- the webhook actually passes the facts through ------------------------------

def test_webhook_sends_stored_facts_to_the_model(monkeypatch):
    import app.main as main
    import app.services.models as models

    captured = {}

    def fake_complete(system_prompt, user_prompt):
        captured["system"] = system_prompt
        return {"model": "test/model:free", "content": "ok"}

    class FakeResponse:
        status_code = 200
        ok = True

        def json(self):
            return {"ok": True}

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.main.complete", fake_complete)
    monkeypatch.setattr(models.requests, "post", lambda *a, **k: FakeResponse())

    app = main.create_app(testing=True)
    app.config["agent_orchestrator"].memory.remember("pricing page", "needs a rewrite")

    client = app.test_client()
    client.post(
        "/api/webhook/telegram",
        json={"message": {"text": "what should I focus on?", "chat": {"id": 42}}},
    )

    assert "pricing page: needs a rewrite" in captured["system"]


def test_memory_failure_does_not_break_the_reply(monkeypatch):
    import app.main as main

    class Boom:
        def search(self, _):
            raise RuntimeError("db gone")

    class FakeOrchestrator:
        memory = Boom()

    assert main._stored_facts(FakeOrchestrator()) == []

import os
import tempfile

import pytest

from app import create_app
from services.memory import ConversationMemory


@pytest.fixture()
def client():
    app = create_app(testing=True)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.get_json()["service"] == "tri-buddy-agent"


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert "status" in response.get_json()


def test_memory_persists_conversation():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        memory = ConversationMemory(db_path=db_path)
        memory.add_message("session-1", "user", "hello")
        memory.add_message("session-1", "assistant", "hi")
        history = memory.get_history("session-1")
        assert len(history) == 2
        assert history[0]["content"] == "hello"
    finally:
        os.remove(db_path)

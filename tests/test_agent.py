from app.main import create_app
from app.agent.orchestrator import AgentOrchestrator
from app.db.session import init_db


def test_webhook_accepts_message_and_returns_plain_response():
    app = create_app(testing=True)
    client = app.test_client()
    response = client.post(
        "/api/webhook/zenzap",
        json={"message": "Add task: Launch landing page by Friday"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert "task" in payload["message"].lower()


def test_agent_adds_task_and_lead():
    init_db()
    orchestrator = AgentOrchestrator()

    task_response = orchestrator.handle_message("Add task: Launch landing page by Friday")
    assert task_response["ok"] is True
    assert "Launch landing page" in task_response["message"]

    lead_response = orchestrator.handle_message("Add lead: ABC Store, interested in WhatsApp SaaS")
    assert lead_response["ok"] is True
    assert "ABC Store" in lead_response["message"]

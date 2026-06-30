from app.main import create_app
from app.agent.orchestrator import AgentOrchestrator
from app.db.session import init_db


def test_team_command_and_approval_flow():
    init_db()
    app = create_app(testing=True)
    client = app.test_client()

    task_response = client.post(
        "/api/webhook/zenzap",
        json={"message": "Assign Rahim to design the homepage"},
    )
    assert task_response.status_code == 200

    approval_response = client.post(
        "/api/webhook/zenzap",
        json={"message": "Draft a proposal email for ABC Store"},
    )
    assert approval_response.status_code == 200
    payload = approval_response.get_json()
    assert payload["ok"] is True
    assert "approval" in payload["message"].lower()


def test_memory_round_trip():
    init_db()
    orchestrator = AgentOrchestrator()
    orchestrator.handle_message("Remember that my default SaaS package is 299 BDT")
    memory_response = orchestrator.handle_message("What do you know about my business?")
    assert memory_response["ok"] is True
    assert "299 BDT" in memory_response["message"]

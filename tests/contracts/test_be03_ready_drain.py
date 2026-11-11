from fastapi.testclient import TestClient

from agentforge.api.app import create_app
from agentforge.runtime.control import write_admission


def auth(settings):
    return {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}


def test_ready_reports_drain_and_mode(settings):
    write_admission(settings, "drain")
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/ready", headers=auth(settings))
        assert response.status_code == 503
        body = response.json()
        assert body["admission"] == "drain"
        assert body["acceptingWork"] is False
        assert body["runMode"] == "dev"
        assert body["status"] == "not_ready"


def test_capabilities_include_components(settings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/capabilities", headers=auth(settings))
        assert response.status_code == 200
        body = response.json()
        assert body["agentExecution"] is False
        assert isinstance(body["components"], list)
        assert any(item["id"] == "api" for item in body["components"])

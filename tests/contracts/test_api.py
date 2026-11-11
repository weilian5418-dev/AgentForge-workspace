import pytest
from fastapi.testclient import TestClient

from agentforge.api.app import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        yield client


def auth(settings):
    return {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}


def test_public_health_is_minimal(client):
    response = client.get("/api/v1/health")
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/v1/ready", "/api/v1/capabilities", "/api/v1/openapi.json"])
def test_private_endpoints_require_token(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert "requestId" in response.json()["error"]


def test_wrong_token_rejected(client):
    assert (
        client.get(
            "/api/v1/capabilities", headers={"Authorization": "Bearer incorrect"}
        ).status_code
        == 401
    )


def test_dns_rebinding_host_rejected(client, settings):
    response = client.get("/api/v1/capabilities", headers={**auth(settings), "Host": "evil.test"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "HOST_REJECTED"


def test_origin_rejected_even_with_valid_token(client, settings):
    response = client.get(
        "/api/v1/capabilities", headers={**auth(settings), "Origin": "https://evil.test"}
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_readiness_does_not_claim_unconfigured_database_is_ready(client, settings):
    response = client.get("/api/v1/ready", headers=auth(settings))
    assert response.status_code == 503
    assert response.json()["database"]["status"] == "unconfigured"


def test_capabilities_are_truthful_and_secret_free(client, settings):
    response = client.get("/api/v1/capabilities", headers=auth(settings))
    assert response.status_code == 200
    assert response.json()["agentExecution"] is False
    assert response.json()["schemaVersion"] == "1"
    assert settings.api_token.get_secret_value() not in response.text
    assert "databaseUrl" not in response.text


def test_exported_openapi_has_bearer_security(client, settings):
    schema = client.get("/api/v1/openapi.json", headers=auth(settings)).json()
    assert schema["openapi"].startswith("3.1")
    assert "ErrorResponse" in schema["components"]["schemas"]
    assert schema["paths"]["/api/v1/capabilities"]["get"]["security"]
    assert "security" not in schema["paths"]["/api/v1/health"]["get"]


def test_not_found_uses_error_envelope(client):
    response = client.get("/api/v1/no-such-resource")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"

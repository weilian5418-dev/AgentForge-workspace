"""D02 async probe returns 202 shape without requiring a live worker."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentforge.api.app import create_app
from agentforge.core.config import Settings


def test_async_probe_requires_database() -> None:
    settings = Settings(api_token=SecretStr("b" * 32), database_url=None)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/providers/prov_x/probe",
            headers={"Authorization": "Bearer " + "b" * 32},
            json={
                "authorizeNetwork": True,
                "grantId": "mgr_x",
                "asyncDispatch": True,
            },
        )
    assert response.status_code == 503


def test_operations_get_requires_auth() -> None:
    settings = Settings(api_token=SecretStr("b" * 32), database_url=None)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/operations/op_x")
    assert response.status_code == 401

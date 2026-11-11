"""BE-04 API surface: grant-only create shape; DB-less rejection."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentforge.api.app import create_app
from agentforge.core.config import Settings


def _client() -> TestClient:
    settings = Settings(api_token=SecretStr("a" * 32), database_url=None)
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


def test_create_project_requires_auth() -> None:
    with _client() as client:
        response = client.post("/api/v1/projects", json={"directoryGrantId": "grant_x"})
    assert response.status_code == 401


def test_create_project_without_database_is_503() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/projects",
            headers={"Authorization": "Bearer " + "a" * 32},
            json={"directoryGrantId": "grant_x"},
        )
    assert response.status_code == 503


def test_create_rejects_client_trust_and_paths() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/projects",
            headers={"Authorization": "Bearer " + "a" * 32},
            json={
                "directoryGrantId": "grant_x",
                "rootPath": "D:/code",
                "workspaceRoot": "D:/ws",
                "trustPolicy": {"allowShell": True},
            },
        )
    assert response.status_code == 422

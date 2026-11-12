"""D11.11: collaboration API routes, queries, and schema export coverage."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agentforge.api.app import create_app
from agentforge.contracts.export import SCHEMA_MODELS, build_contract_documents
from agentforge.driver.control import RunControlError
from agentforge.driver.queries import get_driver_status, list_run_waits


def auth(settings):
    return {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}


def test_collaboration_routes_registered(settings) -> None:
    app = create_app(settings)
    paths = set(app.openapi().get("paths", {}))
    assert "/api/v1/runs/{run_id}/actions" in paths
    assert "/api/v1/runs/{run_id}/waits" in paths
    assert "/api/v1/runs/{run_id}/driver" in paths
    assert "/api/v1/runs/{run_id}/recovery-checks" in paths
    assert "/api/v1/runs/{run_id}/recovery-cases" in paths
    assert "/api/v1/recovery-cases/{case_id}" in paths
    assert "/api/v1/recovery-cases/{case_id}/decisions" in paths


def test_actions_without_database_returns_503(settings) -> None:
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/runs/run_missing/actions",
            headers=auth(settings),
            json={
                "action": "pause",
                "expectedStatusVersion": 1,
                "idempotencyKey": "idem-1",
            },
        )
    assert response.status_code == 503


def test_list_run_waits_missing_run() -> None:
    session = MagicMock()
    session.get.return_value = None
    with pytest.raises(RunControlError) as exc:
        list_run_waits(session, "run_x")
    assert exc.value.code == "run_not_found"


def test_get_driver_status_without_thread() -> None:
    session = MagicMock()
    run = MagicMock(collaboration_state="running", last_sequence=3)
    session.get.return_value = run
    session.scalar.side_effect = [None, 2, 1]  # thread, open waits, pending wakes
    status = get_driver_status(session, "run_1")
    assert status.run_id == "run_1"
    assert status.collaboration_state == "running"
    assert status.thread is None
    assert status.open_wait_count == 2
    assert status.pending_wake_count == 1
    assert status.last_observed_sequence == 3


def test_schema_export_includes_d11_models() -> None:
    required = {
        "run-control-request.schema.json",
        "run-wait-list-response.schema.json",
        "driver-status-response.schema.json",
        "platform-admission.schema.json",
        "recovery-case.schema.json",
        "recovery-case-list-response.schema.json",
        "recovery-report.schema.json",
        "recovery-decision-request.schema.json",
        "handoff-manifest.schema.json",
    }
    missing = required - set(SCHEMA_MODELS)
    assert not missing
    docs = build_contract_documents()
    for name in required:
        assert name in docs
        assert "$defs" in docs[name] or "properties" in docs[name] or "title" in docs[name]
    openapi = docs["openapi.json"]
    paths = openapi.get("paths", {})
    assert "/api/v1/runs/{run_id}/actions" in paths
    assert "/api/v1/recovery-cases/{case_id}/decisions" in paths

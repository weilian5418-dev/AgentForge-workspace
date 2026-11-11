from fastapi.testclient import TestClient

from agentforge.api.app import create_app
from agentforge.runtime.instance import (
    PlatformInstance,
    current_process_key,
    write_runtime_record,
)


def auth(settings):
    return {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}


def _write_live_record(settings) -> PlatformInstance:
    import os

    record = PlatformInstance(
        instance_id="inst_test",
        pid=os.getpid(),
        started_at="2026-11-11T00:00:00Z",
        host=settings.host,
        port=settings.port,
        run_mode=settings.run_mode,
        stage="running",
        version="0.1.0",
        components=[],
        process_key=current_process_key(),
    )
    write_runtime_record(settings, record)
    return record


def test_shutdown_blocks_when_drain_incomplete(settings):
    """No DB → unknown coordination → 409 and no process exit callback."""
    record = _write_live_record(settings)
    exited = {"called": False}

    app = create_app(settings)
    app.state.request_shutdown = lambda: exited.__setitem__("called", True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/control/shutdown",
            headers=auth(settings),
            json={"instanceId": record.instance_id},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "drain_incomplete"
    assert body["drainComplete"] is False
    assert body["verification"] == "unknown"
    assert exited["called"] is False


def test_shutdown_force_exits_despite_incomplete(settings):
    record = _write_live_record(settings)
    exited = {"called": False}
    app = create_app(settings)
    app.state.request_shutdown = lambda: exited.__setitem__("called", True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/control/shutdown",
            headers=auth(settings),
            json={"instanceId": record.instance_id, "force": True},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "stopping"
    assert response.json()["drainComplete"] is False
    assert exited["called"] is True


def test_shutdown_platform_only_without_database(settings):
    record = _write_live_record(settings)
    exited = {"called": False}
    app = create_app(settings)
    app.state.request_shutdown = lambda: exited.__setitem__("called", True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/control/shutdown",
            headers=auth(settings),
            json={"instanceId": record.instance_id, "platformOnly": True},
        )
    assert response.status_code == 200
    assert response.json()["verification"] == "platform_only"
    assert exited["called"] is True

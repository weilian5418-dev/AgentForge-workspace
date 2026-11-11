import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def _wait_health(port: int, process: subprocess.Popen[bytes], timeout: float = 20) -> None:
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=0.5, trust_env=False) as client:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert process.poll() is None, "API process exited before startup"
            try:
                response = client.get("/api/v1/health")
                if response.status_code == 200:
                    assert response.json() == {"status": "ok"}
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise AssertionError("API did not become live")


def test_cli_starts_real_api_and_status_client(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = {
        **os.environ,
        "AGENTFORGE_HOST": "127.0.0.1",
        "AGENTFORGE_PORT": str(port),
        "AGENTFORGE_API_TOKEN": "e2e-" + "x" * 48,
        "AGENTFORGE_STATE_DIR": str(tmp_path / "state"),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "agentforge", "start"],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_health(port, process)
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=0.5, trust_env=False
        ) as client:
            assert client.get("/api/v1/capabilities").status_code == 401
        result = subprocess.run(
            [sys.executable, "-m", "agentforge", "status"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout is not None
        assert '"agentExecution": false' in result.stdout
        assert environment["AGENTFORGE_API_TOKEN"] not in result.stdout
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_cli_start_drain_stop_clears_runtime(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    state_dir = tmp_path / "state"
    environment = {
        **os.environ,
        "AGENTFORGE_HOST": "127.0.0.1",
        "AGENTFORGE_PORT": str(port),
        "AGENTFORGE_API_TOKEN": "e2e-stop-" + "x" * 40,
        "AGENTFORGE_STATE_DIR": str(state_dir),
    }
    # Deterministic: no DB → drain coordination is unknown, not success.
    environment.pop("AGENTFORGE_DATABASE_URL", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "agentforge", "start"],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_health(port, process)
        status = subprocess.run(
            [sys.executable, "-m", "agentforge", "status", "--json"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        assert status.returncode == 0, status.stderr
        assert '"state": "running"' in (status.stdout or "")

        drain = subprocess.run(
            [sys.executable, "-m", "agentforge", "drain", "--json"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        drain_payload = json.loads(drain.stdout or "{}")
        assert drain.returncode == 1, drain.stderr
        assert drain_payload.get("admission") == "drain"
        assert drain_payload.get("platformAdmissionSet") is True
        assert drain_payload.get("drainComplete") is False
        assert drain_payload.get("verification") == "unknown"

        # After unknown drain, force stop so the API exits; do not treat incomplete
        # coordination as a successful drain.
        stop = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentforge",
                "stop",
                "--force",
                "--timeout",
                "15",
                "--json",
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert stop.returncode == 0, stop.stderr
        payload = json.loads(stop.stdout or "{}")
        assert payload.get("state") == "stopped"
        assert process.wait(timeout=15) == 0
        record = Path(state_dir) / "runtime" / "instance.json"
        assert not record.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

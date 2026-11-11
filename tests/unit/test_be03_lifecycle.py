import os
import socket
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from agentforge.cli.main import app
from agentforge.core.config import Settings
from agentforge.core.credentials import initialize_token
from agentforge.runtime.control import read_admission, write_admission
from agentforge.runtime.doctor import _run_check_process
from agentforge.runtime.instance import (
    InstanceError,
    InstanceLock,
    RuntimeRecord,
    current_process_key,
    matches_managed_process,
    write_runtime_record,
)

runner = CliRunner()


def test_instance_lock_blocks_second_acquire(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = Settings(_env_file=None, state_dir=tmp_path / "state", port=port)
    initialize_token(settings)
    first = InstanceLock(settings)
    first.acquire()
    second = InstanceLock(settings)
    with pytest.raises(InstanceError):
        second.acquire()
    first.release()


def test_port_conflict_detected(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.listen(1)
        settings = Settings(_env_file=None, state_dir=tmp_path / "state", port=port)
        initialize_token(settings)
        lock = InstanceLock(settings)
        with pytest.raises(InstanceError, match="already in use"):
            lock.acquire()


def test_admission_drain_roundtrip(tmp_path):
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    assert read_admission(settings) == "open"
    write_admission(settings, "drain")
    assert read_admission(settings) == "drain"
    write_admission(settings, "open")
    assert read_admission(settings) == "open"


def test_admission_corrupt_file_is_fail_closed(tmp_path):
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    settings.control_path.write_text("{not-json", encoding="utf-8")
    assert read_admission(settings) == "blocked"
    settings.control_path.write_text('{"admission": "maybe"}\n', encoding="utf-8")
    assert read_admission(settings) == "blocked"


def test_auth_status_json_hides_token(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTFORGE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("AGENTFORGE_API_TOKEN", raising=False)
    assert runner.invoke(app, ["init"]).exit_code == 0
    result = runner.invoke(app, ["auth", "status", "--json"])
    assert result.exit_code == 0
    assert "credentialId" in result.stdout
    assert "digestHex" not in result.stdout
    token_file = tmp_path / "state" / "credentials" / "api-token"
    secret = token_file.read_text(encoding="utf-8").strip()
    assert secret not in result.stdout


def test_matches_managed_process_rejects_pid_reuse(tmp_path):
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    live_pid = os.getpid()
    fake = RuntimeRecord(
        instance_id="inst_fake",
        pid=live_pid,
        started_at="1999-01-01T00:00:00Z",
        host="127.0.0.1",
        port=8765,
        run_mode="dev",
        stage="running",
        version="0.1.0",
        components=[],
        process_key="win:1:0" if os.name == "nt" else "linux:1:0",
    )
    write_runtime_record(settings, fake)
    assert matches_managed_process(fake) is False
    real = RuntimeRecord(
        instance_id="inst_self",
        pid=live_pid,
        started_at="2026-11-10T00:00:00Z",
        host="127.0.0.1",
        port=8765,
        run_mode="dev",
        stage="running",
        version="0.1.0",
        components=[],
        process_key=current_process_key(live_pid),
    )
    assert matches_managed_process(real) is True


def test_doctor_check_respects_timeout_budget(monkeypatch):
    monkeypatch.setenv("AGENTFORGE_DOCTOR_BLOCK_SECONDS", "1.5")
    started = time.perf_counter()
    result = _run_check_process("inject_block", False, "inject_block", {}, 0.05)
    elapsed = time.perf_counter() - started
    assert result.status == "fail"
    assert "timed out" in result.message
    assert elapsed < 1.0


def test_doctor_cli_process_exits_after_injected_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    block_seconds = 5.0
    env = {
        **os.environ,
        "AGENTFORGE_STATE_DIR": str(tmp_path / "state"),
        "AGENTFORGE_API_TOKEN": "doctor-" + "x" * 48,
        "AGENTFORGE_DOCTOR_BLOCK_SECONDS": str(block_seconds),
    }
    env.pop("AGENTFORGE_DATABASE_URL", None)
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "agentforge", "doctor", "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    elapsed = time.perf_counter() - started
    assert result.returncode == 0, result.stderr
    assert "inject_block" in (result.stdout or "")
    assert "timed out" in (result.stdout or "")
    assert "inject block finished" not in (result.stdout or "")
    # Must exit well before the injected sleep would finish (child is killed).
    assert elapsed < block_seconds - 1.0

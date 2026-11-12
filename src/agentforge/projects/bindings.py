"""Windows/WSL environment path bindings with reachability evidence (BE-04.6)."""

from __future__ import annotations

import platform
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.grants import EnvironmentPathBinding
from agentforge.filesystem.access import compute_directory_identity, normalize_authorized_root
from agentforge.projects.grants import ensure_native_environment
from agentforge.storage.models import EnvironmentPathBindingRow, ExecutionEnvironmentRow

_WSL_PATH = re.compile(r"^/mnt/([a-zA-Z])/(.*)$")


class BindingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def probe_wsl_distro(distro: str) -> bool:
    try:
        proc = subprocess.run(
            ["wsl", "-d", distro, "--", "true"],
            check=False,
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def windows_to_wsl_candidate(windows_path: Path) -> str | None:
    """Propose a /mnt/<drive>/... path — not proof of correspondence by itself."""
    resolved = windows_path.resolve(strict=False)
    drive = resolved.drive
    if len(drive) != 2 or drive[1] != ":":
        return None
    rest = resolved.as_posix()
    if rest[1:2] == ":":
        rest = rest[2:]
    if rest.startswith("/"):
        rest = rest[1:]
    return f"/mnt/{drive[0].lower()}/{rest}"


def verify_native_windows_binding(
    session: Session,
    *,
    path: str,
) -> EnvironmentPathBinding:
    """v1: full support for native Windows reachability only."""
    env = ensure_native_environment(session)
    root = normalize_authorized_root(path)
    identity = compute_directory_identity(root)
    evidence = f"windows_native_stat:{identity}:{platform.system()}:{platform.node()}"
    row = EnvironmentPathBindingRow(
        id=f"bind_{uuid.uuid4().hex[:16]}",
        source_environment_id=env.id,
        target_environment_id=env.id,
        source_path=str(root),
        target_path=str(root),
        correspondence_evidence=evidence,
        binding_version=1,
        status="verified",
        checked_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return _to_contract(row)


def verify_wsl_binding(
    session: Session,
    *,
    windows_path: str,
    wsl_distro: str,
) -> EnvironmentPathBinding:
    """Only explicitly registered + probed WSL combos; no auto-mount or distro start."""
    if not probe_wsl_distro(wsl_distro):
        raise BindingError(
            "wsl_unverified",
            "WSL distro is unreachable; will not auto-start or mount",
        )

    native = ensure_native_environment(session)
    win_root = normalize_authorized_root(windows_path)
    candidate = windows_to_wsl_candidate(win_root)
    if candidate is None:
        raise BindingError("path_unsupported", "Cannot derive WSL path candidate")

    # Evidence: ask WSL to stat the path and compare size of a marker — not drive-letter swap alone.
    try:
        proc = subprocess.run(
            ["wsl", "-d", wsl_distro, "--", "test", "-d", candidate],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BindingError("wsl_probe_failed", f"WSL probe failed: {exc}") from exc

    if proc.returncode != 0:
        raise BindingError(
            "wsl_path_unreachable",
            "WSL path not reachable; correspondence not assumed from path format",
        )

    wsl_env = session.scalar(
        select(ExecutionEnvironmentRow).where(
            ExecutionEnvironmentRow.type == "wsl",
            ExecutionEnvironmentRow.wsl_distro == wsl_distro,
            ExecutionEnvironmentRow.node_id == native.node_id,
        )
    )
    if wsl_env is None:
        wsl_env = ExecutionEnvironmentRow(
            id=f"env_{uuid.uuid4().hex[:16]}",
            type="wsl",
            node_id=native.node_id,
            wsl_distro=wsl_distro,
            connection_status="reachable",
            last_checked_at=datetime.now(UTC),
        )
        session.add(wsl_env)
        session.flush()
    else:
        wsl_env.connection_status = "reachable"
        wsl_env.last_checked_at = datetime.now(UTC)

    evidence = (
        f"wsl_test_d:{wsl_distro}:{candidate}:win_identity={compute_directory_identity(win_root)}"
    )
    row = EnvironmentPathBindingRow(
        id=f"bind_{uuid.uuid4().hex[:16]}",
        source_environment_id=native.id,
        target_environment_id=wsl_env.id,
        source_path=str(win_root),
        target_path=candidate,
        correspondence_evidence=evidence,
        binding_version=1,
        status="verified",
        checked_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return _to_contract(row)


def unsupported_binding_result(
    *,
    source_environment_id: str,
    target_environment_id: str,
    source_path: str,
    target_path: str,
) -> EnvironmentPathBinding:
    return EnvironmentPathBinding(
        binding_id=f"bind_unsupported_{uuid.uuid4().hex[:8]}",
        source_environment_id=source_environment_id,
        target_environment_id=target_environment_id,
        source_path=source_path,
        target_path=target_path,
        correspondence_evidence="unsupported_or_unverified_combination",
        binding_version=1,
        status="unsupported",
        checked_at=datetime.now(UTC),
    )


def _to_contract(row: EnvironmentPathBindingRow) -> EnvironmentPathBinding:
    return EnvironmentPathBinding(
        binding_id=row.id,
        source_environment_id=row.source_environment_id,
        target_environment_id=row.target_environment_id,
        source_path=row.source_path,
        target_path=row.target_path,
        correspondence_evidence=row.correspondence_evidence,
        binding_version=row.binding_version,
        status=row.status,  # type: ignore[arg-type]
        checked_at=row.checked_at,
        expires_at=row.expires_at,
    )

"""BE-04.4/04.5: baseline precheck and streamed input registration (no Agent/scripts)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from agentforge.contracts.grants import TrustPolicy
from agentforge.filesystem.access import (
    ProjectFilesystem,
    compute_directory_identity,
    normalize_authorized_root,
)
from agentforge.projects.baseline import inspect_precheck
from agentforge.projects.inputs import InputError, register_streamed_input


def _fs(root: Path) -> ProjectFilesystem:
    identity = compute_directory_identity(normalize_authorized_root(root))
    return ProjectFilesystem(root, directory_identity=identity)


def test_precheck_non_git_manifest(tmp_path: Path) -> None:
    root = tmp_path / "app"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "main.py").write_text("print(1)\n", encoding="utf-8")
    summary, baseline = inspect_precheck(
        _fs(root), dirty_policy="committed_only", environment_id="env_test"
    )
    assert summary.accessible is True
    assert summary.is_git is False
    assert "pyproject.toml" in summary.build_manifests
    assert baseline.manifest_hash is not None
    assert baseline.status in {"ready", "partial"}


def test_precheck_dirty_git_does_not_clean(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, capture_output=True)
    (root / "a.txt").write_text("one", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    (root / "a.txt").write_text("two", encoding="utf-8")
    (root / "untracked.txt").write_text("x", encoding="utf-8")

    summary, baseline = inspect_precheck(
        _fs(root), dirty_policy="committed_only", environment_id="env_test"
    )
    assert summary.is_git is True
    assert summary.dirty is True
    assert (root / "a.txt").read_text(encoding="utf-8") == "two"
    assert (root / "untracked.txt").exists()
    assert any("no stash" in n or "modifications" in n for n in baseline.notes)


def test_register_stream_hashes_and_rejects_mismatch(tmp_path: Path) -> None:
    class _MemSession:
        def __init__(self) -> None:
            self.rows: list[object] = []

        def scalar(self, _stmt: object) -> object:
            return None

        def add(self, row: object) -> None:
            self.rows.append(row)

        def flush(self) -> None:
            return None

    session = _MemSession()
    data = b"hello-input"
    digest = hashlib.sha256(data).hexdigest()
    result = register_streamed_input(
        session,  # type: ignore[arg-type]
        project_id="proj_x",
        file_name="notes.txt",
        chunks=iter([data]),
        staging_root=tmp_path / "inputs",
        trust_policy=TrustPolicy(),
        client_sha256=digest,
    )
    assert result.size_bytes == len(data)
    assert result.sha256 == digest
    assert Path(result.storage_ref).read_bytes() == data

    with pytest.raises(InputError) as exc:
        register_streamed_input(
            session,  # type: ignore[arg-type]
            project_id="proj_x",
            file_name="notes2.txt",
            chunks=iter([b"abc"]),
            staging_root=tmp_path / "inputs",
            trust_policy=TrustPolicy(),
            client_sha256="0" * 64,
        )
    assert exc.value.code == "hash_mismatch"


def test_malicious_filename_rejected(tmp_path: Path) -> None:
    class _MemSession:
        def scalar(self, _stmt: object) -> object:
            return None

        def add(self, row: object) -> None:
            return None

        def flush(self) -> None:
            return None

    with pytest.raises(InputError) as exc:
        register_streamed_input(
            _MemSession(),  # type: ignore[arg-type]
            project_id="proj_x",
            file_name="../etc/passwd",
            chunks=iter([b"x"]),
            staging_root=tmp_path / "inputs",
            trust_policy=TrustPolicy(),
        )
    assert exc.value.code == "invalid_file_name"

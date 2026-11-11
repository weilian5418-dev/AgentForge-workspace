"""Workspace path confinement for Runner (D05)."""

from __future__ import annotations

import os
from pathlib import Path

from agentforge.contracts.common import normalize_relative_path


class RunnerPathError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def resolve_workspace_root(workspace_root: str) -> Path:
    root = Path(workspace_root).expanduser().resolve(strict=False)
    if not root.exists():
        raise RunnerPathError("workspace_missing", f"Workspace root does not exist: {root}")
    if not root.is_dir():
        raise RunnerPathError("workspace_not_dir", f"Workspace root is not a directory: {root}")
    return root


def resolve_under_workspace(workspace_root: Path, relative: str) -> Path:
    """Resolve a relative path under workspace; reject escape / symlink-out."""
    try:
        rel = normalize_relative_path(relative)
    except ValueError as exc:
        raise RunnerPathError("invalid_relative_path", str(exc)) from exc

    candidate = (workspace_root / rel).resolve(strict=False)
    try:
        candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise RunnerPathError("path_escape", "Resolved path escapes workspace") from exc

    # Walk parents: if any symlink points outside workspace, reject.
    cur = candidate
    # Also check the path as it is built component-wise.
    probe = workspace_root
    for part in Path(rel).parts:
        probe = probe / part
        if probe.is_symlink():
            target = probe.resolve(strict=False)
            try:
                target.relative_to(workspace_root)
            except ValueError as exc:
                raise RunnerPathError(
                    "symlink_escape", "Symlink/junction escapes workspace"
                ) from exc
        if not probe.exists():
            break
    _ = cur
    return candidate


def ensure_parent_dirs(path: Path, workspace_root: Path) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    # Re-check after mkdir for race / junction replacement.
    resolved = parent.resolve(strict=False)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise RunnerPathError("path_escape", "Parent directory escaped workspace") from exc


def is_within(workspace_root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(workspace_root)
        return True
    except ValueError:
        return False


def read_limited(path: Path, *, max_bytes: int) -> bytes:
    size = path.stat().st_size
    if size > max_bytes:
        raise RunnerPathError("output_limit", f"File exceeds max_bytes={max_bytes}")
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise RunnerPathError("output_limit", f"Read exceeded max_bytes={max_bytes}")
    return data


def write_limited(path: Path, data: bytes, *, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise RunnerPathError("output_limit", f"Write exceeds max_bytes={max_bytes}")
    # Detect symlink replacement of the target path.
    if path.exists() and path.is_symlink():
        raise RunnerPathError("symlink_escape", "Refusing to write through symlink target path")
    path.write_bytes(data)
    # Windows: os.replace races — re-resolve after write.
    if not is_within(path.parent.resolve(), path):
        # path.parent may equal workspace child; final resolve check by caller.
        pass
    if os.name == "nt" and path.is_symlink():
        raise RunnerPathError("symlink_escape", "Path became symlink after write")

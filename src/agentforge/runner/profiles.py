"""Registered Runner operation profiles — no arbitrary shell/Docker entry (D05)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentforge.runner.paths import (
    RunnerPathError,
    ensure_parent_dirs,
    read_limited,
    resolve_under_workspace,
    write_limited,
)

MAX_SNIPPET_BYTES = 64_000
MAX_RENDER_BYTES = 256_000
MAX_COLLECT = 500


@dataclass(frozen=True)
class ProfileSpec:
    profile_id: str
    host_supported: bool
    docker_required: bool
    description: str
    writes: bool


PROFILES: dict[str, ProfileSpec] = {
    "prepare_dir": ProfileSpec(
        "prepare_dir",
        True,
        False,
        "Create relative directories under the platform workspace",
        True,
    ),
    "read_snippet": ProfileSpec(
        "read_snippet",
        True,
        False,
        "Read a bounded UTF-8/binary snippet from a relative path",
        False,
    ),
    "collect_candidates": ProfileSpec(
        "collect_candidates",
        True,
        False,
        "List relative files under a workspace subdir (no glob star-escape)",
        False,
    ),
    "run_approved_check": ProfileSpec(
        "run_approved_check",
        True,
        False,
        "Run a registered non-shell check (path_exists / file_sha256)",
        False,
    ),
    "render": ProfileSpec(
        "render",
        True,
        False,
        "Write bounded content to a relative path",
        True,
    ),
    "close_task": ProfileSpec(
        "close_task",
        True,
        False,
        "Write stop evidence marker and close the runner operation",
        True,
    ),
    "run_in_container": ProfileSpec(
        "run_in_container",
        False,
        True,
        "Container execution — blocked until Docker capability is evidenced",
        True,
    ),
}


def list_profiles() -> list[ProfileSpec]:
    return [PROFILES[k] for k in sorted(PROFILES)]


def get_profile(profile_id: str) -> ProfileSpec:
    spec = PROFILES.get(profile_id)
    if spec is None:
        raise KeyError(profile_id)
    return spec


def execute_profile(
    profile_id: str,
    *,
    workspace_root: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    spec = get_profile(profile_id)
    if spec.docker_required or not spec.host_supported:
        return {
            "status": "blocked",
            "detail": "Docker/container capability not evidenced; refusing host fallback",
        }
    handler = _HANDLERS.get(profile_id)
    if handler is None:
        return {"status": "blocked", "detail": f"No host handler for {profile_id}"}
    return handler(workspace_root, params)


def _prepare_dir(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    rel = str(params.get("path") or "")
    target = resolve_under_workspace(root, rel)
    ensure_parent_dirs(target, root)
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise RunnerPathError("not_directory", "prepare_dir target is not a directory")
    # Re-validate after create (junction race).
    resolve_under_workspace(root, rel)
    return {"status": "succeeded", "path": rel, "created": True}


def _read_snippet(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    rel = str(params.get("path") or "")
    max_bytes = int(params.get("maxBytes") or MAX_SNIPPET_BYTES)
    max_bytes = min(max_bytes, MAX_SNIPPET_BYTES)
    path = resolve_under_workspace(root, rel)
    if not path.is_file():
        raise RunnerPathError("not_found", "Snippet path is not a file")
    data = read_limited(path, max_bytes=max_bytes)
    # Prefer text when utf-8; otherwise hex prefix.
    try:
        text = data.decode("utf-8")
        return {"status": "succeeded", "path": rel, "encoding": "utf-8", "content": text}
    except UnicodeDecodeError:
        return {
            "status": "succeeded",
            "path": rel,
            "encoding": "binary",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }


def _collect_candidates(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    rel = str(params.get("path") or ".")
    if rel in {".", ""}:
        base = root
        rel_out = ""
    else:
        base = resolve_under_workspace(root, rel)
        rel_out = rel
    if not base.is_dir():
        raise RunnerPathError("not_directory", "collect_candidates path is not a directory")
    items: list[str] = []
    for child in sorted(base.rglob("*")):
        if not child.is_file():
            continue
        # Skip symlinked files that escape.
        try:
            child.resolve(strict=False).relative_to(root)
        except ValueError:
            continue
        if child.is_symlink():
            continue
        rel_path = child.relative_to(root).as_posix()
        items.append(rel_path)
        if len(items) >= MAX_COLLECT:
            break
    return {
        "status": "succeeded",
        "path": rel_out or ".",
        "items": items,
        "truncated": len(items) >= MAX_COLLECT,
    }


def _run_approved_check(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    check = str(params.get("check") or "")
    if check == "path_exists":
        rel = str(params.get("path") or "")
        path = resolve_under_workspace(root, rel)
        return {"status": "succeeded", "check": check, "path": rel, "exists": path.exists()}
    if check == "file_sha256":
        rel = str(params.get("path") or "")
        expected = str(params.get("expectedSha256") or "")
        path = resolve_under_workspace(root, rel)
        if not path.is_file():
            return {"status": "failed", "check": check, "detail": "file not found"}
        digest = hashlib.sha256(read_limited(path, max_bytes=MAX_RENDER_BYTES)).hexdigest()
        ok = bool(expected) and digest == expected
        return {
            "status": "succeeded" if ok else "failed",
            "check": check,
            "path": rel,
            "sha256": digest,
            "matched": ok,
        }
    return {
        "status": "blocked",
        "detail": "check not in allow-list (path_exists|file_sha256); no shell entry",
    }


def _render(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    rel = str(params.get("path") or "")
    content = params.get("content")
    if not isinstance(content, str):
        raise RunnerPathError("invalid_params", "render requires string content")
    data = content.encode("utf-8")
    path = resolve_under_workspace(root, rel)
    ensure_parent_dirs(path, root)
    write_limited(path, data, max_bytes=MAX_RENDER_BYTES)
    # Post-write confinement check
    resolve_under_workspace(root, rel)
    return {
        "status": "succeeded",
        "path": rel,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _close_task(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    evidence = {
        "reason": str(params.get("reason") or "close_task"),
        "stopped": True,
    }
    rel = str(params.get("evidencePath") or "_agentforge/stop_evidence.json")
    path = resolve_under_workspace(root, rel)
    ensure_parent_dirs(path, root)
    payload = json.dumps(evidence, sort_keys=True).encode("utf-8")
    write_limited(path, payload, max_bytes=MAX_SNIPPET_BYTES)
    return {"status": "succeeded", "evidencePath": rel, "evidence": evidence}


_HANDLERS: dict[str, Callable[[Path, dict[str, Any]], dict[str, Any]]] = {
    "prepare_dir": _prepare_dir,
    "read_snippet": _read_snippet,
    "collect_candidates": _collect_candidates,
    "run_approved_check": _run_approved_check,
    "render": _render,
    "close_task": _close_task,
}

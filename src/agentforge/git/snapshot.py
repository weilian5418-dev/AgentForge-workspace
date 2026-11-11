"""Full recoverable workspace snapshots into D06 content store (D07)."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

from agentforge.artifacts.store import ArtifactStore, ArtifactStoreError
from agentforge.contracts.common import normalize_relative_path
from agentforge.git.safe import GitSafeError, run_git
from agentforge.runner.paths import RunnerPathError, resolve_under_workspace, resolve_workspace_root


class SnapshotError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _file_mode(path: Path) -> str:
    mode = path.stat().st_mode
    return oct(mode & 0o777)


def _is_git_meta(rel: str) -> bool:
    return rel == ".git" or rel.startswith(".git/")


def build_tree_manifest(root: Path) -> dict[str, Any]:
    """Enumerate regular files under root (no symlink follow-out; skip .git)."""
    root = resolve_workspace_root(str(root))
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        try:
            path.resolve(strict=False).relative_to(root)
        except ValueError:
            continue
        rel = path.relative_to(root).as_posix()
        if _is_git_meta(rel):
            continue
        data = path.read_bytes()
        entries.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "mode": _file_mode(path),
            }
        )
    manifest = {"entries": entries, "fileCount": len(entries)}
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_hash = hashlib.sha256(raw).hexdigest()
    return {"manifest": manifest, "manifestHash": manifest_hash, "raw": raw}


def pack_workspace_tar(root: Path) -> bytes:
    root = resolve_workspace_root(str(root))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                path.resolve(strict=False).relative_to(root)
            except ValueError:
                continue
            rel = path.relative_to(root).as_posix()
            if _is_git_meta(rel):
                continue
            tar.add(path, arcname=rel, recursive=False)
    return buf.getvalue()


def store_workspace_snapshot(store: ArtifactStore, root: Path) -> tuple[str, str, dict[str, Any]]:
    meta = build_tree_manifest(root)
    blob = pack_workspace_tar(root)
    try:
        content_hash = store.put_bytes(blob)
    except ArtifactStoreError as exc:
        raise SnapshotError(exc.code, exc.message) from exc
    return content_hash, str(meta["manifestHash"]), meta["manifest"]


def capture_selected_paths(
    store: ArtifactStore,
    source_root: Path,
    selected_paths: list[str],
) -> tuple[str, str, int]:
    """Capture only explicitly selected relative paths into an immutable tar."""
    root = resolve_workspace_root(str(source_root))
    buf = io.BytesIO()
    entries: list[dict[str, Any]] = []
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in selected_paths:
            try:
                normalize_relative_path(rel)
                path = resolve_under_workspace(root, rel)
            except (ValueError, RunnerPathError) as exc:
                raise SnapshotError("invalid_path", str(exc)) from exc
            if not path.is_file() or path.is_symlink():
                raise SnapshotError("path_not_file", f"Selected path is not a regular file: {rel}")
            data = path.read_bytes()
            tar.add(path, arcname=rel, recursive=False)
            entries.append(
                {
                    "path": rel,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "mode": _file_mode(path),
                }
            )
    manifest = {"entries": entries, "fileCount": len(entries), "kind": "dirty_selection"}
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    content_hash = store.put_bytes(buf.getvalue())
    return content_hash, manifest_hash, len(entries)


def extract_tar_to(store: ArtifactStore, content_hash: str, dest: Path) -> None:
    data = store.get_bytes(content_hash)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        # Safe extract: reject absolute / .. members
        for member in tar.getmembers():
            name = member.name.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise SnapshotError("unsafe_tar_member", f"Refusing tar member {name}")
            tar.extract(member, path=dest, filter="data")


def head_commit(repo: Path) -> str | None:
    result = run_git(repo, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def porcelain_status(repo: Path) -> dict[str, list[str]]:
    result = run_git(repo, ["status", "--porcelain=v1", "-uall"], check=True)
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        xy, path = line[:2], line[3:]
        if path.startswith('"'):
            path = path.strip('"')
        if xy == "??":
            untracked.append(path)
        else:
            if xy[0] != " " and xy[0] != "?":
                staged.append(path)
            if xy[1] != " " and xy[1] != "?":
                unstaged.append(path)
    return {"staged": staged, "unstaged": unstaged, "untracked": untracked}

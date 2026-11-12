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
from agentforge.git.safe import run_git
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


def _collect_regular_files(root: Path) -> list[tuple[str, bytes, str]]:
    """Single walk: refuse symlinks; omit .git; read each file once."""
    root = resolve_workspace_root(str(root))
    files: list[tuple[str, bytes, str]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if _is_git_meta(rel):
            continue
        if path.is_symlink():
            raise SnapshotError(
                "symlink_unsupported",
                f"Refusing symlink in snapshot scope: {rel}",
            )
        if not path.is_file():
            continue
        try:
            path.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise SnapshotError("path_escape", f"Path escapes workspace: {rel}") from exc
        data = path.read_bytes()
        files.append((rel, data, _file_mode(path)))
    return files


def build_tree_manifest_from_files(files: list[tuple[str, bytes, str]]) -> dict[str, Any]:
    entries = [
        {
            "path": rel,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mode": mode,
        }
        for rel, data, mode in files
    ]
    manifest = {
        "entries": entries,
        "fileCount": len(entries),
        "emptyDirsOmitted": True,
        "symlinks": "rejected",
    }
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_hash = hashlib.sha256(raw).hexdigest()
    return {"manifest": manifest, "manifestHash": manifest_hash, "raw": raw}


def build_tree_manifest(root: Path) -> dict[str, Any]:
    files = _collect_regular_files(root)
    return build_tree_manifest_from_files(files)


def pack_files_tar(files: list[tuple[str, bytes, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, data, mode in files:
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            try:
                info.mode = int(mode, 8)
            except ValueError:
                info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def store_workspace_snapshot(store: ArtifactStore, root: Path) -> tuple[str, str, dict[str, Any]]:
    """Single-pass snapshot: one read per file feeds both manifest and tar (no TOCTOU gap)."""
    files = _collect_regular_files(root)
    meta = build_tree_manifest_from_files(files)
    blob = pack_files_tar(files)
    # Archive ≡ manifest by construction; still verify member digests match entries.
    _assert_tar_matches_manifest(blob, meta["manifest"])
    try:
        content_hash = store.put_bytes(blob)
    except ArtifactStoreError as exc:
        raise SnapshotError(exc.code, exc.message) from exc
    return content_hash, str(meta["manifestHash"]), meta["manifest"]


def _assert_tar_matches_manifest(blob: bytes, manifest: dict[str, Any]) -> None:
    expected = {e["path"]: e for e in manifest["entries"]}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        if len(members) != len(expected):
            raise SnapshotError("manifest_archive_mismatch", "Tar member count ≠ manifest")
        for member in members:
            name = member.name.replace("\\", "/")
            if name not in expected:
                raise SnapshotError("manifest_archive_mismatch", f"Unexpected tar member {name}")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise SnapshotError("manifest_archive_mismatch", f"Cannot read {name}")
            data = extracted.read()
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected[name]["sha256"] or len(data) != expected[name]["size"]:
                raise SnapshotError(
                    "manifest_archive_mismatch",
                    f"Tar content diverges from manifest for {name}",
                )


def capture_selected_paths(
    store: ArtifactStore,
    source_root: Path,
    selected_paths: list[str],
) -> tuple[str, str, int]:
    """Capture only explicitly selected relative paths into an immutable tar."""
    root = resolve_workspace_root(str(source_root))
    files: list[tuple[str, bytes, str]] = []
    for rel in selected_paths:
        try:
            normalize_relative_path(rel)
            path = resolve_under_workspace(root, rel)
        except (ValueError, RunnerPathError) as exc:
            raise SnapshotError("invalid_path", str(exc)) from exc
        if path.is_symlink():
            raise SnapshotError("symlink_unsupported", f"Refusing symlink: {rel}")
        if not path.is_file():
            raise SnapshotError("path_not_file", f"Selected path is not a regular file: {rel}")
        data = path.read_bytes()
        files.append((rel, data, _file_mode(path)))
    meta = build_tree_manifest_from_files(files)
    meta["manifest"]["kind"] = "dirty_selection"
    raw = json.dumps(meta["manifest"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_hash = hashlib.sha256(raw).hexdigest()
    blob = pack_files_tar(files)
    _assert_tar_matches_manifest(blob, meta["manifest"])
    content_hash = store.put_bytes(blob)
    return content_hash, manifest_hash, len(files)


def extract_tar_to(store: ArtifactStore, content_hash: str, dest: Path) -> None:
    data = store.get_bytes(content_hash)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise SnapshotError("unsafe_tar_member", f"Refusing tar member {name}")
            tar.extract(member, path=dest, filter="data")


def list_tar_paths(store: ArtifactStore, content_hash: str) -> set[str]:
    data = store.get_bytes(content_hash)
    paths: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                paths.add(member.name.replace("\\", "/"))
    return paths


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

"""Read fixed Git objects and explicitly approved dirty snapshots, outside DB transactions."""

from __future__ import annotations

import re
import subprocess
import tarfile
import tempfile
from typing import BinaryIO, cast

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.common import normalize_relative_path
from agentforge.git.safe import _empty_hooks_path, _safe_env, git_executable
from agentforge.integration.builder import TreeEntry
from agentforge.integration.objects import MAX_FILE_BYTES, MAX_FILES, MAX_TREE_BYTES, validate_tree


def read_tar_tree(handle: BinaryIO, store: ArtifactStore) -> dict[str, TreeEntry]:
    tree: dict[str, TreeEntry] = {}
    total = 0
    with tarfile.open(fileobj=handle, mode="r:*") as archive:
        for member in archive:
            if member.isdir():
                continue
            if not member.isfile() or member.size > MAX_FILE_BYTES or len(tree) >= MAX_FILES:
                raise ValueError("unsupported_or_oversized_archive")
            path = normalize_relative_path(member.name)
            if path in tree:
                raise ValueError("duplicate_archive_path")
            total += member.size
            if total > MAX_TREE_BYTES:
                raise ValueError("tree_size_limit")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("archive_member_missing")
            data = source.read(MAX_FILE_BYTES + 1)
            tree[path] = TreeEntry(
                content_hash=store.put_bytes(data),
                mode="100755" if member.mode & 0o111 else "100644",
            )
    validate_tree(tree, store)
    return tree


def read_git_baseline(source_root: str, commit: str, store: ArtifactStore) -> dict[str, TreeEntry]:
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
        raise ValueError("fixed_commit_required")
    with tempfile.TemporaryFile() as archive:
        result = subprocess.run(
            [
                git_executable(),
                "-c",
                f"core.hooksPath={_empty_hooks_path()}",
                "-C",
                source_root,
                "archive",
                "--format=tar",
                commit,
            ],
            stdout=archive,
            stderr=subprocess.PIPE,
            env=_safe_env(allow_network=False),
            timeout=60,
            shell=False,
        )
        if result.returncode or archive.tell() > MAX_TREE_BYTES + 8 * 1024 * 1024:
            raise ValueError("baseline_unavailable_or_oversized")
        archive.seek(0)
        return read_tar_tree(cast(BinaryIO, archive), store)

"""Bounded, content-addressed integration trees; hashes always name stored bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agentforge.contracts.common import normalize_relative_path
from agentforge.integration.builder import TreeEntry, tree_manifest_hash

MAX_FILES = 5000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TREE_BYTES = 64 * 1024 * 1024


def validate_tree(tree: dict[str, TreeEntry], store: Any) -> None:
    if len(tree) > MAX_FILES:
        raise ValueError("tree_file_limit")
    total = 0
    folded: set[str] = set()
    for path, entry in tree.items():
        normalized = normalize_relative_path(path)
        if (
            normalized != path
            or path.casefold() in folded
            or path == ".git"
            or path.startswith(".git/")
        ):
            raise ValueError("unsafe_tree_path")
        folded.add(path.casefold())
        data = store.get_bytes(entry.content_hash)
        total += len(data)
        if (
            len(data) > MAX_FILE_BYTES
            or total > MAX_TREE_BYTES
            or hashlib.sha256(data).hexdigest() != entry.content_hash
        ):
            raise ValueError("tree_integrity_or_size")
        if entry.mode not in {"100644", "100755"}:
            raise ValueError("unsupported_tree_mode")


def store_tree(tree: dict[str, TreeEntry], store: Any) -> tuple[str, str]:
    validate_tree(tree, store)
    payload = {
        "schemaVersion": "1",
        "entries": [
            {"path": p, "contentHash": e.content_hash, "mode": e.mode}
            for p, e in sorted(tree.items())
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return store.put_bytes(raw), tree_manifest_hash(tree)


def load_tree(store: Any, content_hash: str, expected_tree_hash: str) -> dict[str, TreeEntry]:
    raw = store.get_bytes(content_hash)
    if len(raw) > 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != content_hash:
        raise ValueError("tree_manifest_corrupt")
    payload = json.loads(raw)
    if payload.get("schemaVersion") != "1":
        raise ValueError("unsupported_tree_manifest")
    entries = payload["entries"]
    tree = {e["path"]: TreeEntry(content_hash=e["contentHash"], mode=e["mode"]) for e in entries}
    if len(tree) != len(entries) or tree_manifest_hash(tree) != expected_tree_hash:
        raise ValueError("tree_manifest_mismatch")
    validate_tree(tree, store)
    return tree


def materialize_tree(tree: dict[str, TreeEntry], store: Any, destination: Path) -> None:
    validate_tree(tree, store)
    destination.mkdir(parents=True, exist_ok=False)
    for path, entry in tree.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(store.get_bytes(entry.content_hash))
        if entry.mode == "100755":
            target.chmod(target.stat().st_mode | 0o111)


def verify_materialized(tree: dict[str, TreeEntry], destination: Path) -> bool:
    actual = {}
    for path in destination.rglob("*"):
        if path.is_symlink() or path.is_junction():
            return False
        if path.is_file():
            rel = path.relative_to(destination).as_posix()
            if rel not in tree or path.stat().st_size > MAX_FILE_BYTES:
                return False
            actual[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return actual == {p: e.content_hash for p, e in tree.items()}

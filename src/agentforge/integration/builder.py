"""Baseline-relative tree delta merge (D12.9) — no full-tree last-write-wins."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agentforge.driver.binding import sha256_hex

DeltaKind = Literal["add", "modify", "delete"]
MAX_TREE_ENTRIES = 50_000
MAX_PATH_BYTES = 1024


@dataclass(frozen=True, slots=True)
class TreeEntry:
    content_hash: str
    mode: str = "100644"


# path -> TreeEntry
TreeManifest = dict[str, TreeEntry]


@dataclass(frozen=True, slots=True)
class TreeDeltaItem:
    path: str
    kind: DeltaKind
    expected_base_hash: str | None
    expected_base_mode: str | None
    new_hash: str | None
    new_mode: str | None
    source_ref: str


@dataclass(frozen=True, slots=True)
class TreeDelta:
    source_ref: str
    items: tuple[TreeDeltaItem, ...]


@dataclass(frozen=True, slots=True)
class MergeConflict:
    path: str
    reason: str
    sources: tuple[str, ...]


@dataclass
class MergePlan:
    base_manifest_hash: str
    applies: list[TreeDeltaItem] = field(default_factory=list)
    conflicts: list[MergeConflict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts


@dataclass(frozen=True, slots=True)
class BuildResult:
    status: Literal["succeeded", "conflicted", "failed", "blocked"]
    tree_manifest: TreeManifest
    tree_manifest_hash: str
    content_hash: str
    conflict_paths: tuple[str, ...] = ()
    error_code: str | None = None
    message: str | None = None


class BuilderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _norm_path(path: str) -> str:
    p = path.replace("\\", "/").strip("/")
    if not p or p.startswith("/") or ".." in p.split("/"):
        raise BuilderError("invalid_path", f"illegal path {path!r}")
    if len(p.encode()) > MAX_PATH_BYTES:
        raise BuilderError("invalid_path", "path too long")
    # Case-fold key for Windows collision detection (preserve display path separately)
    return p


def _case_key(path: str) -> str:
    return _norm_path(path).casefold()


def tree_manifest_hash(manifest: TreeManifest) -> str:
    if len(manifest) > MAX_TREE_ENTRIES:
        raise BuilderError("tree_too_large", f"manifest exceeds {MAX_TREE_ENTRIES} entries")
    items = [
        {
            "path": path,
            "contentHash": entry.content_hash,
            "mode": entry.mode,
        }
        for path, entry in sorted(manifest.items(), key=lambda kv: kv[0])
    ]
    return sha256_hex({"schemaVersion": "1", "entries": items})


def compute_tree_delta(
    base: TreeManifest,
    candidate: TreeManifest,
    *,
    source_ref: str,
) -> TreeDelta:
    """Diff candidate against fixed baseline — unchanged baseline files are not writes."""
    if len(base) > MAX_TREE_ENTRIES or len(candidate) > MAX_TREE_ENTRIES:
        raise BuilderError("tree_too_large", "tree exceeds entry limit")
    items: list[TreeDeltaItem] = []
    base_paths = set(base)
    cand_paths = set(candidate)
    for path in sorted(cand_paths - base_paths):
        entry = candidate[path]
        items.append(
            TreeDeltaItem(
                path=path,
                kind="add",
                expected_base_hash=None,
                expected_base_mode=None,
                new_hash=entry.content_hash,
                new_mode=entry.mode,
                source_ref=source_ref,
            )
        )
    for path in sorted(base_paths & cand_paths):
        b, c = base[path], candidate[path]
        if b.content_hash == c.content_hash and b.mode == c.mode:
            continue  # unchanged — not a write
        items.append(
            TreeDeltaItem(
                path=path,
                kind="modify",
                expected_base_hash=b.content_hash,
                expected_base_mode=b.mode,
                new_hash=c.content_hash,
                new_mode=c.mode,
                source_ref=source_ref,
            )
        )
    for path in sorted(base_paths - cand_paths):
        b = base[path]
        items.append(
            TreeDeltaItem(
                path=path,
                kind="delete",
                expected_base_hash=b.content_hash,
                expected_base_mode=b.mode,
                new_hash=None,
                new_mode=None,
                source_ref=source_ref,
            )
        )
    return TreeDelta(source_ref=source_ref, items=tuple(items))


def combine_approved_deltas(
    base: TreeManifest,
    inputs: tuple[TreeDelta, ...],
) -> MergePlan:
    """Combine role deltas vs baseline. Identical writes dedupe; differing writes conflict."""
    plan = MergePlan(base_manifest_hash=tree_manifest_hash(base))
    # casefold -> canonical path from first writer
    case_index: dict[str, str] = {_case_key(p): p for p in base}
    # path -> winning item (or conflict)
    pending: dict[str, TreeDeltaItem] = {}
    sources_by_path: dict[str, list[str]] = {}

    def _conflict(path: str, reason: str, *srcs: str) -> None:
        plan.conflicts.append(
            MergeConflict(path=path, reason=reason, sources=tuple(srcs))
        )
        pending.pop(path, None)

    for delta in inputs:
        for item in delta.items:
            path = _norm_path(item.path)
            ck = _case_key(path)
            if ck in case_index and case_index[ck] != path:
                _conflict(
                    path,
                    "case_collision",
                    case_index[ck],
                    item.source_ref,
                )
                continue
            case_index.setdefault(ck, path)
            sources_by_path.setdefault(path, []).append(item.source_ref)

            # Validate expected base when modifying/deleting
            if item.kind in {"modify", "delete"}:
                base_entry = base.get(path)
                if base_entry is None:
                    _conflict(path, "missing_base", item.source_ref)
                    continue
                if (
                    item.expected_base_hash is not None
                    and item.expected_base_hash != base_entry.content_hash
                ):
                    _conflict(path, "base_hash_mismatch", item.source_ref)
                    continue

            prior = pending.get(path)
            if prior is None:
                pending[path] = item
                continue

            # Same source repeating — last within source wins for that source only
            if prior.source_ref == item.source_ref:
                pending[path] = item
                continue

            # Two deletes → merge
            if prior.kind == "delete" and item.kind == "delete":
                continue

            # Write vs delete
            if {prior.kind, item.kind} == {"delete", "modify"} or {
                prior.kind,
                item.kind,
            } == {"delete", "add"}:
                _conflict(path, "write_delete", prior.source_ref, item.source_ref)
                continue

            # Identical new content → dedupe
            if (
                prior.kind == item.kind
                and prior.new_hash == item.new_hash
                and prior.new_mode == item.new_mode
            ):
                sources_by_path[path] = sorted(
                    set(sources_by_path.get(path, []) + [prior.source_ref, item.source_ref])
                )
                continue

            _conflict(path, "write_write", prior.source_ref, item.source_ref)

    if plan.conflicts:
        return plan
    plan.applies = [pending[p] for p in sorted(pending)]
    return plan


def apply_merge_plan_to_manifest(base: TreeManifest, plan: MergePlan) -> TreeManifest:
    if not plan.ok:
        raise BuilderError("merge_conflicted", "cannot apply conflicted plan")
    out = dict(base)
    for item in plan.applies:
        path = _norm_path(item.path)
        if item.kind == "delete":
            out.pop(path, None)
        elif item.kind in {"add", "modify"}:
            if not item.new_hash:
                raise BuilderError("invalid_delta", f"missing new hash for {path}")
            out[path] = TreeEntry(content_hash=item.new_hash, mode=item.new_mode or "100644")
    return out


def materialize_merge_plan(
    plan: MergePlan,
    *,
    base: TreeManifest,
    destination: Path,
    object_store: Any,
) -> BuildResult:
    """Write merged tree using content-addressed blobs only (no live role cwd scan)."""
    if not plan.ok:
        return BuildResult(
            status="conflicted",
            tree_manifest=dict(base),
            tree_manifest_hash=plan.base_manifest_hash,
            content_hash=plan.base_manifest_hash,
            conflict_paths=tuple(sorted({c.path for c in plan.conflicts})),
            error_code="integration_conflict",
            message=f"{len(plan.conflicts)} path conflicts",
        )
    try:
        merged = apply_merge_plan_to_manifest(base, plan)
        tm_hash = tree_manifest_hash(merged)
    except BuilderError as exc:
        return BuildResult(
            status="blocked",
            tree_manifest=dict(base),
            tree_manifest_hash=plan.base_manifest_hash,
            content_hash=plan.base_manifest_hash,
            error_code=exc.code,
            message=exc.message,
        )

    destination = Path(destination)
    if destination.exists():
        # Crash recovery: refuse to invent a new workspace; caller must reconcile.
        marker = destination / ".agentforge_build.json"
        if not marker.is_file():
            return BuildResult(
                status="failed",
                tree_manifest=merged,
                tree_manifest_hash=tm_hash,
                content_hash=tm_hash,
                error_code="workspace_identity_unknown",
                message="destination exists without build marker",
            )

    destination.mkdir(parents=True, exist_ok=True)
    # Clear previous materialization except marker/meta
    for child in list(destination.iterdir()):
        if child.name.startswith(".agentforge"):
            continue
        if child.is_file() or child.is_symlink():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            import shutil

            shutil.rmtree(child)

    try:
        for path, entry in merged.items():
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            data = object_store.get_bytes(entry.content_hash)
            target.write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        return BuildResult(
            status="failed",
            tree_manifest=merged,
            tree_manifest_hash=tm_hash,
            content_hash=tm_hash,
            error_code="materialize_failed",
            message=str(exc)[:500],
        )

    # contentHash: hash of sorted blob hashes (not archive timestamps)
    content_hash = sha256_hex(
        {
            "treeManifestHash": tm_hash,
            "blobs": [
                {"path": p, "contentHash": merged[p].content_hash}
                for p in sorted(merged)
            ],
        }
    )
    return BuildResult(
        status="succeeded",
        tree_manifest=merged,
        tree_manifest_hash=tm_hash,
        content_hash=content_hash,
    )

"""D12.9: tree delta merge rules — no full-tree last-write-wins."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentforge.artifacts.store import ArtifactStore
from agentforge.integration.builder import (
    TreeEntry,
    combine_approved_deltas,
    compute_tree_delta,
    materialize_merge_plan,
    tree_manifest_hash,
)
from agentforge.integration.workspaces import (
    build_workspace_root,
    verify_workspace_binding,
    write_build_marker,
)

H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


def test_unchanged_baseline_file_is_not_a_write() -> None:
    base = {"a.txt": TreeEntry(H1), "b.txt": TreeEntry(H2)}
    cand = {"a.txt": TreeEntry(H1), "b.txt": TreeEntry(H2)}
    delta = compute_tree_delta(base, cand, source_ref="role_a")
    assert delta.items == ()


def test_one_writer_keeps_change_when_other_keeps_baseline() -> None:
    base = {"a.txt": TreeEntry(H1)}
    d1 = compute_tree_delta(base, {"a.txt": TreeEntry(H2)}, source_ref="role_a")
    d2 = compute_tree_delta(base, {"a.txt": TreeEntry(H1)}, source_ref="role_b")
    plan = combine_approved_deltas(base, (d1, d2))
    assert plan.ok
    assert len(plan.applies) == 1
    assert plan.applies[0].new_hash == H2


def test_identical_writes_dedupe() -> None:
    base = {"a.txt": TreeEntry(H1)}
    d1 = compute_tree_delta(base, {"a.txt": TreeEntry(H2)}, source_ref="role_a")
    d2 = compute_tree_delta(base, {"a.txt": TreeEntry(H2)}, source_ref="role_b")
    plan = combine_approved_deltas(base, (d1, d2))
    assert plan.ok
    assert len(plan.applies) == 1


def test_write_write_conflict() -> None:
    base = {"a.txt": TreeEntry(H1)}
    d1 = compute_tree_delta(base, {"a.txt": TreeEntry(H2)}, source_ref="role_a")
    d2 = compute_tree_delta(base, {"a.txt": TreeEntry(H3)}, source_ref="role_b")
    plan = combine_approved_deltas(base, (d1, d2))
    assert not plan.ok
    assert plan.conflicts[0].reason == "write_write"


def test_write_delete_conflict() -> None:
    base = {"a.txt": TreeEntry(H1)}
    d1 = compute_tree_delta(base, {"a.txt": TreeEntry(H2)}, source_ref="role_a")
    d2 = compute_tree_delta(base, {}, source_ref="role_b")
    plan = combine_approved_deltas(base, (d1, d2))
    assert not plan.ok
    assert plan.conflicts[0].reason == "write_delete"


def test_both_delete_merges() -> None:
    base = {"a.txt": TreeEntry(H1)}
    d1 = compute_tree_delta(base, {}, source_ref="role_a")
    d2 = compute_tree_delta(base, {}, source_ref="role_b")
    plan = combine_approved_deltas(base, (d1, d2))
    assert plan.ok
    assert plan.applies[0].kind == "delete"


def test_case_collision_conflicts() -> None:
    base: dict[str, TreeEntry] = {}
    from agentforge.integration.builder import TreeDelta, TreeDeltaItem

    d1 = TreeDelta(
        source_ref="a",
        items=(
            TreeDeltaItem(
                path="Readme.md",
                kind="add",
                expected_base_hash=None,
                expected_base_mode=None,
                new_hash=H1,
                new_mode="100644",
                source_ref="a",
            ),
        ),
    )
    d2 = TreeDelta(
        source_ref="b",
        items=(
            TreeDeltaItem(
                path="readme.md",
                kind="add",
                expected_base_hash=None,
                expected_base_mode=None,
                new_hash=H2,
                new_mode="100644",
                source_ref="b",
            ),
        ),
    )
    plan = combine_approved_deltas(base, (d1, d2))
    assert not plan.ok
    assert plan.conflicts[0].reason == "case_collision"


def test_materialize_conflicted_does_not_write(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    base = {"a.txt": TreeEntry(H1)}
    d1 = compute_tree_delta(base, {"a.txt": TreeEntry(H2)}, source_ref="a")
    d2 = compute_tree_delta(base, {"a.txt": TreeEntry(H3)}, source_ref="b")
    plan = combine_approved_deltas(base, (d1, d2))
    dest = tmp_path / "out"
    result = materialize_merge_plan(plan, base=base, destination=dest, object_store=store)
    assert result.status == "conflicted"
    assert not dest.exists() or not any(dest.iterdir())


def test_materialize_succeeded_from_object_store(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "store")
    blob = b"hello-integration"
    content_hash = store.put_bytes(blob)
    base: dict[str, TreeEntry] = {}
    cand = {"hello.txt": TreeEntry(content_hash)}
    delta = compute_tree_delta(base, cand, source_ref="role_a")
    plan = combine_approved_deltas(base, (delta,))
    dest = tmp_path / "out"
    # Pre-create marker so materialize allows directory
    write_build_marker(
        dest,
        build_id="ibld_1",
        run_id="run_1",
        selection_id="isel_1",
        selection_hash="a" * 64,
        baseline_tree_hash="b" * 64,
        claim_token="claim",
    )
    result = materialize_merge_plan(plan, base=base, destination=dest, object_store=store)
    assert result.status == "succeeded"
    assert (dest / "hello.txt").read_bytes() == blob
    assert result.tree_manifest_hash == tree_manifest_hash(result.tree_manifest)


def test_workspace_binding_reject_mismatch(tmp_path: Path) -> None:
    root = build_workspace_root(tmp_path, run_id="run_1", build_id="ibld_1")
    write_build_marker(
        root,
        build_id="ibld_1",
        run_id="run_1",
        selection_id="isel_1",
        selection_hash="a" * 64,
        baseline_tree_hash="b" * 64,
        claim_token="claim",
    )
    verify_workspace_binding(
        root,
        build_id="ibld_1",
        run_id="run_1",
        selection_id="isel_1",
        selection_hash="a" * 64,
        claim_token="claim",
    )
    from agentforge.integration.workspaces import WorkspaceError

    with pytest.raises(WorkspaceError):
        verify_workspace_binding(
            root,
            build_id="ibld_1",
            run_id="run_1",
            selection_id="isel_1",
            selection_hash="a" * 64,
            claim_token="other",
        )

"""D12.8: integration selection from released handoff inputs."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentforge.contracts.integrations import IntegrationSelectionRequest
from agentforge.driver.binding import sha256_hex
from agentforge.integration.fingerprints import build_selection_hash
from agentforge.integration.selection import (
    SelectionError,
    create_selection,
    resolve_released_inputs,
    validate_tree_release_scope,
)
from agentforge.queries.authorization import AuthenticatedPrincipal

H = "a" * 64
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
PRINCIPAL = AuthenticatedPrincipal("c1", 1, "user:api")


def test_rejects_client_extra_snapshots_at_contract() -> None:
    with pytest.raises(Exception):
        IntegrationSelectionRequest.model_validate(
            {
                "handoffRef": "hof_1",
                "handoffHash": H,
                "idempotencyKey": "k1",
                "extraSnapshotIds": ["snap_x"],
            }
        )


def test_validate_tree_scope_blocks_code_without_tree() -> None:
    candidate = MagicMock(kind="code")
    with pytest.raises(SelectionError) as exc:
        validate_tree_release_scope(
            scope_kind="file",
            member={"candidateId": "c1", "contentHash": H},
            candidate=candidate,
        )
    assert exc.value.code == "release_scope_mismatch"


def test_resolve_released_inputs_rejects_cross_run_and_blocked() -> None:
    session = MagicMock()
    release = MagicMock(
        id="rel_1",
        stage_id="stage_1",
        shared_interface_still_blocked=True,
        dependency_satisfied_refs=[],
        candidate_versions=[{"candidateId": "cand_1", "contentHash": H, "workPackageId": "wp_1"}],
        work_package_ids=["wp_1"],
        scope_kind="document",
    )
    stage = MagicMock(id="stage_1", run_id="run_other")
    session.get.side_effect = lambda model, key: {  # noqa: ARG005
        "rel_1": release,
        "stage_1": stage,
    }.get(key)
    with pytest.raises(SelectionError) as cross:
        resolve_released_inputs(session, run_id="run_1", release_record_ids=["rel_1"])
    assert cross.value.code == "release_scope_mismatch"

    stage.run_id = "run_1"
    with pytest.raises(SelectionError) as blocked:
        resolve_released_inputs(session, run_id="run_1", release_record_ids=["rel_1"])
    assert "blocked" in blocked.value.message


def test_resolve_released_inputs_ok_for_frozen_file() -> None:
    session = MagicMock()
    release = MagicMock(
        id="rel_1",
        stage_id="stage_1",
        shared_interface_still_blocked=False,
        dependency_satisfied_refs=["dep_ok"],
        candidate_versions=[
            {
                "candidateId": "cand_1",
                "contentHash": H,
                "workPackageId": "wp_1",
            }
        ],
        work_package_ids=["wp_1"],
        scope_kind="document",
    )
    stage = MagicMock(id="stage_1", run_id="run_1")
    candidate = MagicMock(
        id="cand_1",
        content_hash=H,
        state="frozen",
        kind="markdown",
    )

    def _get(model, key):  # noqa: ANN001, ARG001
        return {"rel_1": release, "stage_1": stage, "cand_1": candidate}.get(key)

    session.get.side_effect = _get
    bindings = resolve_released_inputs(
        session, run_id="run_1", release_record_ids=["rel_1"]
    )
    assert len(bindings) == 1
    assert bindings[0]["snapshotId"] == "cand_1"
    assert bindings[0]["contentHash"] == H


def test_create_selection_idempotent_and_conflict() -> None:
    session = MagicMock()
    run = MagicMock(
        id="run_1",
        collaboration_state="ready_for_integration",
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        active_generation=1,
        baseline_id="bl_1",
    )
    handoff_hash = sha256_hex({"runId": "run_1"})
    op = MagicMock(
        status="succeeded",
        result_refs=["hof_1"],
        request_hash=handoff_hash,
        binding_json={
            "handoffId": "hof_1",
            "handoffHash": handoff_hash,
            "runId": "run_1",
            "planVersion": 1,
            "assignmentVersion": 1,
            "executionGeneration": 1,
            "releaseRecordIds": ["rel_1"],
            "contractHash": H,
        },
    )
    baseline = MagicMock(
        id="bl_1",
        status="ready",
        git_commit=None,
        manifest_hash=H,
        dirty_policy="clean",
    )
    release = MagicMock(
        id="rel_1",
        stage_id="stage_1",
        shared_interface_still_blocked=False,
        dependency_satisfied_refs=[],
        candidate_versions=[
            {"candidateId": "cand_1", "contentHash": H, "workPackageId": "wp_1"}
        ],
        work_package_ids=["wp_1"],
        scope_kind="document",
    )
    stage = MagicMock(id="stage_1", run_id="run_1")
    candidate = MagicMock(id="cand_1", content_hash=H, state="frozen", kind="markdown")
    plan = MagicMock(contract_hash=H)

    store: dict[str, object] = {}

    def _get(model, key, **kwargs):  # noqa: ANN001, ARG001
        name = getattr(model, "__name__", "")
        if name == "Run":
            return run
        if name == "ProjectBaselineRow":
            return baseline
        if name == "ReleaseRecordRow":
            return release
        if name == "StageRow":
            return stage
        if name == "ArtifactCandidateRow":
            return candidate
        if name == "Plan":
            return plan
        if name == "IntegrationSelectionRow":
            return store.get(key)
        return None

    session.get.side_effect = _get
    session.scalar.side_effect = [None, None]  # no existing selection twice

    def _scalars(stmt):  # noqa: ANN001, ARG001
        result = MagicMock()
        result.all.return_value = [op]
        return result

    session.scalars.side_effect = _scalars

    req = IntegrationSelectionRequest.model_validate(
        {
            "handoffRef": "hof_1",
            "handoffHash": handoff_hash,
            "idempotencyKey": "sel-1",
        }
    )

    added: list[object] = []

    def _add(row):  # noqa: ANN001
        added.append(row)
        store[row.id] = row
        row.created_at = NOW

    session.add.side_effect = _add
    with patch("agentforge.integration.selection.db_now", return_value=NOW):
        first = create_selection(
            session, principal=PRINCIPAL, run_id="run_1", request=req
        )
    assert first.selection_hash
    assert first.baseline_tree_hash == H
    assert len(first.source_bindings) == 1

    # Same key + same content → reuse
    existing = added[0]
    session.scalar.side_effect = [existing]
    session.scalars.side_effect = _scalars
    with patch("agentforge.integration.selection.db_now", return_value=NOW):
        second = create_selection(
            session, principal=PRINCIPAL, run_id="run_1", request=req
        )
    assert second.selection_id == first.selection_id

    # Same key + different content → conflict
    existing.selection_hash = "b" * 64
    session.scalar.side_effect = [existing]
    session.scalars.side_effect = _scalars
    with pytest.raises(SelectionError) as conflict:
        create_selection(session, principal=PRINCIPAL, run_id="run_1", request=req)
    assert conflict.value.code == "integration_conflict"


def test_mutable_head_baseline_rejected() -> None:
    from agentforge.integration.selection import _resolve_baseline_tree_hash

    session = MagicMock()
    run = MagicMock(baseline_id="bl_1")
    baseline = MagicMock(
        id="bl_1",
        status="ready",
        git_commit="HEAD",
        manifest_hash=None,
        dirty_policy="clean",
    )
    session.get.return_value = baseline
    with pytest.raises(SelectionError) as exc:
        _resolve_baseline_tree_hash(session, run)
    assert "HEAD" in exc.value.message or "mutable" in exc.value.message


def test_selection_hash_stable() -> None:
    a = build_selection_hash(
        run_id="run_1",
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        active_generation=1,
        handoff_ref="hof_1",
        handoff_hash=H,
        baseline_ref="bl_1",
        baseline_tree_hash=H,
        source_bindings=[{"releaseId": "r1", "snapshotId": "s1", "contentHash": H}],
        merge_policy_version="merge-policy-v1",
        contract_hash=H,
    )
    b = build_selection_hash(
        run_id="run_1",
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        active_generation=1,
        handoff_ref="hof_1",
        handoff_hash=H,
        baseline_ref="bl_1",
        baseline_tree_hash=H,
        source_bindings=[{"releaseId": "r1", "snapshotId": "s1", "contentHash": H}],
        merge_policy_version="merge-policy-v1",
        contract_hash=H,
    )
    assert a == b
    assert len(a) == 64

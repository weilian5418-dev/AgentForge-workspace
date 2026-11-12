"""Stage authority regression: no caller-supplied pass/stop/candidate facts."""

from datetime import UTC, datetime

import pytest

from agentforge.artifacts.store import ArtifactStore
from agentforge.contracts.stages import (
    GateEvaluationResult,
    ReviewReportRequest,
    StageSubmissionRequest,
)
from agentforge.stages.errors import StageError
from agentforge.stages.service import StageService
from agentforge.storage import models as m
from tests.support.collaboration import seed


@pytest.fixture
def setup(tmp_path, monkeypatch):
    s, spec = seed(tmp_path / "project")
    monkeypatch.setattr(
        "agentforge.projects.grants.require_active_grant",
        lambda session, ref: session.get(m.DirectoryGrantRow, ref),
    )
    stage = next(r for r in s.added if isinstance(r, m.StageRow))
    stage.status = "executing"
    wp = spec.work_packages[0]
    role = spec.roles[0]
    workspace = "workspace-" + role.role_id
    instance = m.AgentInstanceRow(
        id="agent",
        assignment_id="assignment-" + role.role_id,
        run_id="run",
        role_id=role.role_id,
        agent_profile_id="profile-" + role.role_id,
        plan_version=1,
        assignment_version=1,
        gate_policy_version=1,
        generation=1,
        workspace_id=workspace,
        lifecycle="stopped",
        status="stopped",
        activity="idle",
        current_attempt_id="attempt",
        session_ref="native",
        execution_environment="env",
    )
    s.add(instance)
    s.add(
        m.InstanceAttemptRow(
            id="attempt",
            instance_id=instance.id,
            run_id="run",
            work_package_id=wp.id,
            stage_id=stage.id,
            generation=1,
            workspace_id=workspace,
        )
    )
    store = ArtifactStore(tmp_path / "store")
    content = store.put_bytes(b"actual immutable candidate")
    s.add(
        m.ArtifactCandidateRow(
            id="candidate",
            project_id="project",
            workspace_id=workspace,
            relative_path=wp.deliverables[0].relative_path,
            kind="markdown",
            content_hash=content,
            manifest_hash="a" * 64,
            contract_id=wp.deliverables[0].id,
            contract_version=1,
            contract_hash="c" * 64,
            environment_hash="e" * 64,
            state="frozen",
            operation_id="stop",
            attempt_id="attempt",
            generation=1,
            stop_evidence_json={},
        )
    )
    s.add(
        m.InstanceOperationRow(
            id="stop",
            instance_id=instance.id,
            attempt_id="attempt",
            kind="request_stop",
            status="succeeded",
            payload_json={
                "stopProof": {
                    "writesQuiesced": True,
                    "generation": 1,
                    "workspaceId": workspace,
                    "attemptId": "attempt",
                    "sessionRef": "native",
                    "checkedAt": datetime.now(UTC).isoformat(),
                }
            },
        )
    )
    return StageService(s, store=store), s, spec, stage


def submit(svc, spec, stage, **updates):
    data = {
        "workPackageIds": [spec.work_packages[0].id],
        "attemptRefs": ["attempt"],
        "idempotencyKey": "submission",
    }
    data.update(updates)
    return svc.submit(
        stage.id, StageSubmissionRequest.model_validate(data), submitter_ref="user:test"
    )


def freeze(setup):
    svc, s, spec, stage = setup
    submit(svc, spec, stage)
    sub = next(r for r in s.added if isinstance(r, m.StageSubmissionRow))
    cycle = svc.complete_stop_and_freeze(sub.id)
    return cycle


def report(s, status="passed"):
    c = s.get(m.ArtifactCandidateRow, "candidate")
    s.add(
        m.ArtifactValidationReportRow(
            id="validation",
            candidate_id=c.id,
            validator_id="sha256_presence",
            validator_version="1",
            status=status,
            content_hash=c.content_hash,
            contract_hash=c.contract_hash,
            environment_hash=c.environment_hash,
            evidence_refs=["content:" + c.content_hash],
            source="validator_registry",
            message="Integrity only",
        )
    )


def test_submission_closes_writes_and_emits_durable_work(setup):
    svc, s, spec, stage = setup
    result = submit(svc, spec, stage)
    assert stage.write_access_closed and stage.status == "stopping"
    outbox = next(r for r in s.added if isinstance(r, m.Outbox))
    assert outbox.payload["operationId"] == result.operation_id


def test_idempotent_submission_checks_entire_payload(setup):
    svc, s, spec, stage = setup
    first = submit(svc, spec, stage)
    assert submit(svc, spec, stage).operation_id == first.operation_id
    with pytest.raises(StageError, match="changed"):
        submit(svc, spec, stage, note="different")


def test_caller_boolean_stop_shortcut_removed(setup):
    svc, s, spec, stage = setup
    with pytest.raises(TypeError):
        svc.submit(
            stage.id,
            StageSubmissionRequest(
                workPackageIds=[spec.work_packages[0].id], attemptRefs=["attempt"]
            ),
            submitter_ref="user:test",
            stop_verified=True,
        )


def test_no_stop_ledger_blocks_freeze(setup):
    svc, s, spec, stage = setup
    submit(svc, spec, stage)
    s.get(m.InstanceOperationRow, "stop").payload_json = {}
    sub = next(r for r in s.added if isinstance(r, m.StageSubmissionRow))
    with pytest.raises(StageError, match="not confirmed stopped"):
        svc.complete_stop_and_freeze(sub.id)


def test_missing_real_candidate_never_synthesized(setup):
    svc, s, spec, stage = setup
    submit(svc, spec, stage)
    s.delete(s.get(m.ArtifactCandidateRow, "candidate"))
    sub = next(r for r in s.added if isinstance(r, m.StageSubmissionRow))
    with pytest.raises(StageError, match="Missing required"):
        svc.complete_stop_and_freeze(sub.id)


def test_missing_reports_wait_and_cannot_be_forced_released(setup):
    svc, s, spec, stage = setup
    freeze(setup)
    assert svc.evaluate(stage.id).outcome == "waiting"
    with pytest.raises(TypeError):
        svc.evaluate(stage.id, validation_passed_required=True)
    with pytest.raises(StageError, match="validation_incomplete"):
        svc.commit_release(
            stage.id,
            GateEvaluationResult(
                outcome="release", reason="invented", allowedReleaseWorkPackageIds=["not-in-plan"]
            ),
        )


def test_matching_reports_release_only_fixed_members_and_save_refs(setup):
    svc, s, spec, stage = setup
    freeze(setup)
    report(s)
    evaluation = svc.evaluate(stage.id)
    assert evaluation.outcome == "release"
    decision = svc.commit_release(stage.id, evaluation)
    assert decision.validation_refs == ["validation"]
    assert stage.status == "released"
    release = next(r for r in s.added if isinstance(r, m.ReleaseRecordRow))
    assert release.candidate_versions[0]["candidateId"] == "candidate"


def test_report_hash_mismatch_cannot_pass(setup):
    svc, s, spec, stage = setup
    freeze(setup)
    report(s)
    s.get(m.ArtifactValidationReportRow, "validation").content_hash = "f" * 64
    assert svc.evaluate(stage.id).outcome == "waiting"


def test_old_evaluation_cannot_release_changed_evidence(setup):
    svc, s, spec, stage = setup
    freeze(setup)
    report(s)
    evaluation = svc.evaluate(stage.id)
    s.get(m.ArtifactValidationReportRow, "validation").status = "failed"
    with pytest.raises(StageError):
        svc.commit_release(stage.id, evaluation)


def test_old_plan_blocks_release(setup):
    svc, s, spec, stage = setup
    freeze(setup)
    report(s)
    s.get(m.Run, "run").plan_version = 2
    with pytest.raises(StageError, match="current executable"):
        svc.commit_release(stage.id)


def test_review_must_be_trusted_and_contract_bound(setup):
    svc, s, spec, stage = setup
    cycle = freeze(setup)
    body = ReviewReportRequest(
        reviewRequestId="missing",
        reviewCycleId=cycle.review_cycle_id,
        candidateSetId=cycle.candidate_set_id,
        manifestHash=cycle.candidate_set_hash,
        contractHash=cycle.contract_hash,
        findings=[],
        verdict="approve",
    )
    with pytest.raises(StageError, match="Trusted instance"):
        svc.submit_review(stage.id, body, reviewer_instance_id="forged-header")


def test_required_failure_survives_optional_pass_and_rework_is_closed(setup):
    svc, s, spec, stage = setup
    freeze(setup)
    report(s, "failed")
    assert svc.evaluate(stage.id).outcome == "rework"
    svc.apply_evaluation(stage.id)
    assert stage.status == "rework" and stage.write_access_closed
    order = next(r for r in s.added if isinstance(r, m.ReworkOrderRow))
    assert order.target_role_id == spec.work_packages[0].owner_role_id
    assert stage.repair_round == 0
    with pytest.raises(StageError):
        svc.accept_rework(order.id, workspace_id="missing")


def test_required_reviewer_cannot_disappear_by_not_creating_request(setup):
    svc, s, spec, stage = setup
    from agentforge.contracts.plans import PlanSpec
    from agentforge.planning.hashing import compute_plan_hash

    raw = spec.model_dump(mode="json", by_alias=True)
    reviewer = {
        **raw["roles"][0],
        "roleId": "reviewer",
        "writeScopes": [],
        "independentReview": True,
    }
    raw["roles"].append(reviewer)
    raw["stages"][0]["gatePolicy"]["requiredReviewRoles"] = ["reviewer"]
    new_spec = PlanSpec.model_validate(raw)
    plan = s.get(m.Plan, ("run", 1))
    plan.spec_json = new_spec.model_dump(mode="json", by_alias=True)
    plan.plan_hash = compute_plan_hash(new_spec)
    stage.gate_policy = raw["stages"][0]["gatePolicy"]
    s.add(
        m.AgentInstanceRow(
            id="reviewer-instance",
            run_id="run",
            role_id="reviewer",
            assignment_version=1,
            lifecycle="active",
            status="idle",
        )
    )
    freeze((svc, s, new_spec, stage))
    report(s)
    req = next(r for r in s.added if isinstance(r, m.ReviewRequestRow))
    s.delete(req)
    assert svc.evaluate(stage.id).reason == "required_reviewer_pending"


def test_candidate_set_tamper_invalidates_previous_pass(setup):
    svc, s, spec, stage = setup
    freeze(setup)
    report(s)
    cs = s.get(m.CandidateSetRow, stage.current_candidate_set_id)
    cs.members = [{**cs.members[0], "contentHash": "f" * 64}]
    with pytest.raises(StageError, match="fingerprint"):
        svc.evaluate(stage.id)


def test_repair_budget_exhaustion_blocks_instead_of_repeating(setup):
    svc, s, spec, stage = setup
    freeze(setup)
    report(s, "failed")
    stage.repair_round = spec.stages[0].gate_policy.max_repair_rounds
    assert svc.evaluate(stage.id).outcome == "blocked"
    svc.apply_evaluation(stage.id)
    assert stage.status == "blocked"

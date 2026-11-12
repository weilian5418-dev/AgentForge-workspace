"""D08.2: pure PlanCompiler positive/negative cases."""

from __future__ import annotations

from datetime import UTC, datetime

from agentforge.contracts.agents import RoleAssignment
from agentforge.contracts.plans import AssignmentSet, PlanSpec
from agentforge.planning.compiler import (
    CapabilityEvidenceFact,
    CompilerFacts,
    compile_plan,
)


def _deliverable(deliverable_id: str = "out_notes", path: str = "outputs/notes.md") -> dict:
    return {
        "id": deliverable_id,
        "kind": "markdown",
        "relativePath": path,
        "required": True,
        "criteria": [{"id": "sections", "description": "Required sections", "required": True}],
        "validators": [
            {
                "id": "markdown",
                "version": "1",
                "criterionIds": ["sections"],
                "required": True,
                "config": {},
            }
        ],
        "independentReview": False,
    }


def _role(
    role_id: str,
    *,
    write: list[str] | None = None,
    independent: bool = False,
    caps: list[str] | None = None,
) -> dict:
    return {
        "roleId": role_id,
        "responsibilities": "work",
        "requiredCapabilities": caps or ["write_files"],
        "readScopes": write or ["src/"],
        "writeScopes": write or ["src/", "outputs/"],
        "budget": {},
        "independentReview": independent,
        "requireUserDecisionOnGate": False,
    }


def _base_spec(**overrides: object) -> PlanSpec:
    data = {
        "goal": "ship notes",
        "constraints": [],
        "baselineId": "bl_1",
        "inputIds": ["input_1"],
        "routing": {
            "mode": "single",
            "reason": "one writer",
            "ruleVersion": "rules-v1",
            "modelReviewCount": 0,
        },
        "roles": [_role("role_writer")],
        "workPackages": [
            {
                "id": "wp_1",
                "stageId": "stg_impl",
                "objective": "write",
                "ownerRoleId": "role_writer",
                "dependsOn": [],
                "inputs": [],
                "deliverables": [_deliverable()],
                "readScopes": ["src/"],
                "writeScopes": ["outputs/"],
                "toolPolicyId": "restricted",
            }
        ],
        "stages": [
            {
                "stageId": "stg_impl",
                "dependencies": [],
                "requiredWorkPackages": ["wp_1"],
                "gatePolicy": {
                    "policy": "stage_atomic",
                    "version": 1,
                    "requiredReviewRoles": [],
                    "requireUserDecision": False,
                    "maxRepairRounds": 3,
                },
            }
        ],
        "gatePolicyVersion": 1,
        "resourceEstimate": {
            "basis": "heuristic",
            "uncertainties": ["model variance"],
        },
    }
    data.update(overrides)
    return PlanSpec.model_validate(data)


def test_valid_minimal_plan() -> None:
    report = compile_plan(
        _base_spec(),
        CompilerFacts(known_validator_ids=frozenset({"markdown"})),
    )
    assert report.status == "valid"
    assert report.issues == []


def test_work_package_cycle_invalid() -> None:
    spec = _base_spec(
        workPackages=[
            {
                "id": "wp_a",
                "stageId": "stg_impl",
                "objective": "a",
                "ownerRoleId": "role_writer",
                "dependsOn": ["wp_b"],
                "inputs": [],
                "deliverables": [_deliverable("out_a", "outputs/a.md")],
                "readScopes": [],
                "writeScopes": ["outputs/"],
                "toolPolicyId": "restricted",
            },
            {
                "id": "wp_b",
                "stageId": "stg_impl",
                "objective": "b",
                "ownerRoleId": "role_writer",
                "dependsOn": ["wp_a"],
                "inputs": [],
                "deliverables": [_deliverable("out_b", "outputs/b.md")],
                "readScopes": [],
                "writeScopes": ["outputs/"],
                "toolPolicyId": "restricted",
                "sharedWriteCoordination": "serialize",
            },
        ],
        stages=[
            {
                "stageId": "stg_impl",
                "dependencies": [],
                "requiredWorkPackages": ["wp_a", "wp_b"],
                "gatePolicy": {
                    "policy": "stage_atomic",
                    "version": 1,
                    "requiredReviewRoles": [],
                    "requireUserDecision": False,
                    "maxRepairRounds": 3,
                },
            }
        ],
    )
    # Also coordinate first package for shared write
    payload = spec.model_dump(mode="json", by_alias=True)
    payload["workPackages"][0]["sharedWriteCoordination"] = "serialize"
    spec = PlanSpec.model_validate(payload)
    report = compile_plan(spec, CompilerFacts(known_validator_ids=frozenset({"markdown"})))
    assert report.status == "invalid"
    assert any(i.code == "work_package_cycle" for i in report.issues)


def test_stage_cycle_invalid() -> None:
    spec = _base_spec(
        roles=[_role("role_writer")],
        workPackages=[
            {
                "id": "wp_1",
                "stageId": "stg_a",
                "objective": "a",
                "ownerRoleId": "role_writer",
                "dependsOn": [],
                "inputs": [],
                "deliverables": [_deliverable("out_a", "outputs/a.md")],
                "readScopes": [],
                "writeScopes": ["outputs/"],
                "toolPolicyId": "restricted",
            },
            {
                "id": "wp_2",
                "stageId": "stg_b",
                "objective": "b",
                "ownerRoleId": "role_writer",
                "dependsOn": [],
                "inputs": [],
                "deliverables": [_deliverable("out_b", "outputs/b.md")],
                "readScopes": [],
                "writeScopes": ["outputs/b/"],
                "toolPolicyId": "restricted",
            },
        ],
        stages=[
            {
                "stageId": "stg_a",
                "dependencies": ["stg_b"],
                "requiredWorkPackages": ["wp_1"],
                "gatePolicy": {
                    "policy": "stage_atomic",
                    "version": 1,
                    "requiredReviewRoles": [],
                    "requireUserDecision": False,
                    "maxRepairRounds": 3,
                },
            },
            {
                "stageId": "stg_b",
                "dependencies": ["stg_a"],
                "requiredWorkPackages": ["wp_2"],
                "gatePolicy": {
                    "policy": "stage_atomic",
                    "version": 1,
                    "requiredReviewRoles": [],
                    "requireUserDecision": False,
                    "maxRepairRounds": 3,
                },
            },
        ],
    )
    report = compile_plan(spec, CompilerFacts(known_validator_ids=frozenset({"markdown"})))
    assert report.status == "invalid"
    assert any(i.code == "stage_cycle" for i in report.issues)


def test_cross_graph_deadlock() -> None:
    spec = _base_spec(
        workPackages=[
            {
                "id": "wp_early",
                "stageId": "stg_a",
                "objective": "early",
                "ownerRoleId": "role_writer",
                "dependsOn": ["wp_late"],
                "inputs": [],
                "deliverables": [_deliverable("out_a", "outputs/a.md")],
                "readScopes": [],
                "writeScopes": ["outputs/a/"],
                "toolPolicyId": "restricted",
            },
            {
                "id": "wp_late",
                "stageId": "stg_b",
                "objective": "late",
                "ownerRoleId": "role_writer",
                "dependsOn": [],
                "inputs": [],
                "deliverables": [_deliverable("out_b", "outputs/b.md")],
                "readScopes": [],
                "writeScopes": ["outputs/b/"],
                "toolPolicyId": "restricted",
            },
        ],
        stages=[
            {
                "stageId": "stg_a",
                "dependencies": [],
                "requiredWorkPackages": ["wp_early"],
                "gatePolicy": {
                    "policy": "stage_atomic",
                    "version": 1,
                    "requiredReviewRoles": [],
                    "requireUserDecision": False,
                    "maxRepairRounds": 3,
                },
            },
            {
                "stageId": "stg_b",
                "dependencies": ["stg_a"],
                "requiredWorkPackages": ["wp_late"],
                "gatePolicy": {
                    "policy": "stage_atomic",
                    "version": 1,
                    "requiredReviewRoles": [],
                    "requireUserDecision": False,
                    "maxRepairRounds": 3,
                },
            },
        ],
    )
    report = compile_plan(spec, CompilerFacts(known_validator_ids=frozenset({"markdown"})))
    assert report.status == "invalid"
    assert any(i.code == "cross_graph_deadlock" for i in report.issues)


def test_shared_write_requires_coordination() -> None:
    spec = _base_spec(
        workPackages=[
            {
                "id": "wp_a",
                "stageId": "stg_impl",
                "objective": "a",
                "ownerRoleId": "role_writer",
                "dependsOn": [],
                "inputs": [],
                "deliverables": [_deliverable("out_a", "outputs/a.md")],
                "readScopes": [],
                "writeScopes": ["outputs/"],
                "toolPolicyId": "restricted",
            },
            {
                "id": "wp_b",
                "stageId": "stg_impl",
                "objective": "b",
                "ownerRoleId": "role_writer",
                "dependsOn": [],
                "inputs": [],
                "deliverables": [_deliverable("out_b", "outputs/b.md")],
                "readScopes": [],
                "writeScopes": ["outputs/"],
                "toolPolicyId": "restricted",
            },
        ],
        stages=[
            {
                "stageId": "stg_impl",
                "dependencies": [],
                "requiredWorkPackages": ["wp_a", "wp_b"],
                "gatePolicy": {
                    "policy": "stage_atomic",
                    "version": 1,
                    "requiredReviewRoles": [],
                    "requireUserDecision": False,
                    "maxRepairRounds": 3,
                },
            }
        ],
    )
    report = compile_plan(spec, CompilerFacts(known_validator_ids=frozenset({"markdown"})))
    assert report.status == "invalid"
    assert any(i.code == "shared_write_uncoordinated" for i in report.issues)


def test_reviewer_cannot_be_producer() -> None:
    spec = _base_spec(
        roles=[
            _role("role_writer"),
            _role("role_reviewer", write=["reviews/"], independent=True, caps=["review"]),
        ],
        stages=[
            {
                "stageId": "stg_impl",
                "dependencies": [],
                "requiredWorkPackages": ["wp_1"],
                "gatePolicy": {
                    "policy": "stage_atomic",
                    "version": 1,
                    "requiredReviewRoles": ["role_writer"],
                    "requireUserDecision": False,
                    "maxRepairRounds": 3,
                },
            }
        ],
    )
    report = compile_plan(spec, CompilerFacts(known_validator_ids=frozenset({"markdown"})))
    assert report.status == "invalid"
    codes = {i.code for i in report.issues}
    assert "reviewer_is_producer" in codes or "review_not_independent" in codes


def test_unknown_validator_blocked() -> None:
    report = compile_plan(
        _base_spec(),
        CompilerFacts(known_validator_ids=frozenset({"other"})),
    )
    assert report.status == "blocked"
    assert any(i.code == "unknown_validator" for i in report.issues)


def test_assignment_with_expired_capability_blocked() -> None:
    now = datetime.now(UTC)
    assignment = AssignmentSet(
        run_id="run_1",
        plan_version=1,
        assignment_version=1,
        items=[
            RoleAssignment(
                assignment_id="asg_1",
                run_id="run_1",
                plan_version=1,
                assignment_version=1,
                role_id="role_writer",
                agent_profile_id="aprof_1",
            )
        ],
        created_at=now,
    )
    report = compile_plan(
        _base_spec(),
        CompilerFacts(
            known_validator_ids=frozenset({"markdown"}),
            assignment=assignment,
            capability_evidence=(
                CapabilityEvidenceFact(
                    check_id="chk_1",
                    profile_id="aprof_1",
                    capabilities=frozenset({"write_files"}),
                    expired=True,
                ),
            ),
        ),
    )
    assert report.status == "blocked"
    assert any(i.code == "capability_evidence_unusable" for i in report.issues)

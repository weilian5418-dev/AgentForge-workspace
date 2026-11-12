"""D08.1: plan/contract hash stability and semantic sensitivity."""

from __future__ import annotations

from agentforge.contracts.plans import PlanSpec, PlanStageDefinition, PlanWorkPackageSpec
from agentforge.contracts.stages import StageGatePolicy
from agentforge.planning.hashing import (
    PLAN_HASH_ALGO,
    compute_contract_hash,
    compute_plan_hash,
)


def _role(role_id: str = "role_writer") -> dict:
    return {
        "roleId": role_id,
        "responsibilities": "implement",
        "requiredCapabilities": ["write_files"],
        "readScopes": ["src/"],
        "writeScopes": ["src/"],
        "budget": {},
        "independentReview": False,
        "requireUserDecisionOnGate": False,
    }


def _deliverable() -> dict:
    return {
        "id": "out_notes",
        "kind": "markdown",
        "relativePath": "outputs/notes.md",
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


def _spec(**overrides: object) -> PlanSpec:
    base = {
        "goal": "ship notes",
        "constraints": ["no network"],
        "baselineId": "bl_1",
        "inputIds": ["input_b", "input_a"],
        "routing": {
            "mode": "single",
            "reason": "one writer",
            "ruleVersion": "rules-v1",
            "modelReviewCount": 0,
        },
        "roles": [_role()],
        "workPackages": [
            {
                "id": "wp_1",
                "stageId": "stg_impl",
                "objective": "write notes",
                "ownerRoleId": "role_writer",
                "dependsOn": [],
                "inputs": [],
                "deliverables": [_deliverable()],
                "readScopes": ["src/"],
                "writeScopes": ["outputs/"],
                "toolPolicyId": "restricted",
                "maxRepairRounds": 3,
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
    }
    base.update(overrides)
    return PlanSpec.model_validate(base)


def test_plan_hash_stable_across_input_id_order() -> None:
    a = _spec(inputIds=["input_a", "input_b"])
    b = _spec(inputIds=["input_b", "input_a"])
    assert compute_plan_hash(a) == compute_plan_hash(b)
    assert len(compute_plan_hash(a)) == 64


def test_plan_hash_changes_when_goal_changes() -> None:
    a = _spec()
    b = _spec(goal="ship other notes")
    assert compute_plan_hash(a) != compute_plan_hash(b)


def test_plan_hash_changes_when_dependency_added() -> None:
    a = _spec()
    payload = a.model_dump(mode="json", by_alias=True)
    payload["workPackages"].append(
        {
            "id": "wp_2",
            "stageId": "stg_impl",
            "objective": "review",
            "ownerRoleId": "role_writer",
            "dependsOn": ["wp_1"],
            "inputs": [],
            "deliverables": [_deliverable()],
            "readScopes": ["outputs/"],
            "writeScopes": [],
            "toolPolicyId": "restricted",
            "maxRepairRounds": 1,
        }
    )
    b = PlanSpec.model_validate(payload)
    assert compute_plan_hash(a) != compute_plan_hash(b)


def test_contract_hash_order_independent() -> None:
    d1 = _deliverable()
    d2 = dict(d1)
    d2["id"] = "out_other"
    d2["relativePath"] = "outputs/other.md"
    assert compute_contract_hash([d1, d2]) == compute_contract_hash([d2, d1])


def test_stage_definition_rejects_self_dependency() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlanStageDefinition.model_validate(
            {
                "stageId": "stg_a",
                "dependencies": ["stg_a"],
                "requiredWorkPackages": ["wp_1"],
                "gatePolicy": StageGatePolicy().model_dump(mode="json", by_alias=True),
            }
        )


def test_work_package_spec_rejects_self_dependency() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlanWorkPackageSpec.model_validate(
            {
                "id": "wp_1",
                "stageId": "stg_impl",
                "objective": "x",
                "ownerRoleId": "role_writer",
                "dependsOn": ["wp_1"],
                "inputs": [],
                "deliverables": [_deliverable()],
                "readScopes": [],
                "writeScopes": [],
                "toolPolicyId": "restricted",
            }
        )


def test_hash_algo_constant() -> None:
    assert PLAN_HASH_ALGO == "plan-hash-v1"

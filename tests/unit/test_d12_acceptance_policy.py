"""D12.13: acceptance scope, dependency closure, authority rejection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentforge.contracts.delivery import AcceptanceDecisionRequest, DeliveryManifestPayload
from agentforge.delivery.acceptance import AcceptanceError, validate_member_dependency_closure

H = "a" * 64


def _payload(**kwargs: object) -> DeliveryManifestPayload:
    base: dict[str, object] = {
        "schemaVersion": "1",
        "runId": "run_1",
        "integrationVersion": "iv_1",
        "validationCycleId": "ivc_1",
        "manifestRevision": 1,
        "planVersion": 1,
        "assignmentVersion": 1,
        "gatePolicyVersion": 1,
        "selectionHash": H,
        "baselineHash": H,
        "treeManifestHash": H,
        "contractHash": H,
        "integrationPolicyHash": H,
        "environmentFingerprint": H,
        "members": [
            {
                "memberId": "tree:iv_1",
                "kind": "code_tree",
                "required": True,
                "safeRelativePath": ".",
                "objectHash": H,
                "dependsOnMemberIds": [],
                "applicableCheckIds": ["python_pytest"],
                "memberTechnicalStatus": "passed",
            },
            {
                "memberId": "docs",
                "kind": "markdown",
                "required": False,
                "safeRelativePath": "README.md",
                "objectHash": H,
                "dependsOnMemberIds": [],
                "applicableCheckIds": [],
                "memberTechnicalStatus": "passed",
            },
            {
                "memberId": "slides",
                "kind": "pptx",
                "required": False,
                "safeRelativePath": "deck.pptx",
                "objectHash": H,
                "dependsOnMemberIds": ["docs"],
                "applicableCheckIds": [],
                "memberTechnicalStatus": "passed",
            },
        ],
        "checks": [
            {
                "checkId": "python_pytest",
                "checkVersion": "v1",
                "configHash": H,
                "outcome": "passed",
            }
        ],
        "reviews": [],
        "knownRisks": [],
        "technicalStatus": "passed",
    }
    base.update(kwargs)
    return DeliveryManifestPayload.model_validate(base)


def test_accept_rejects_authority_fields() -> None:
    with pytest.raises(ValidationError):
        AcceptanceDecisionRequest.model_validate(
            {
                "integrationVersion": "iv_1",
                "manifestHash": H,
                "manifestRevision": 1,
                "expectedStatusVersion": 1,
                "decision": "accept",
                "technicalPassed": True,
            }
        )


def test_dependency_closure_required() -> None:
    payload = _payload()
    with pytest.raises(AcceptanceError) as ei:
        validate_member_dependency_closure(payload=payload, accepted_ids={"slides"})
    assert ei.value.code == "acceptance_scope_invalid"


def test_dependency_closure_ok_when_deps_included() -> None:
    payload = _payload()
    validate_member_dependency_closure(payload=payload, accepted_ids={"slides", "docs"})


def test_failed_shared_check_blocks_code_accept() -> None:
    payload = _payload(
        technicalStatus="failed",
        checks=[
            {
                "checkId": "python_pytest",
                "checkVersion": "v1",
                "configHash": H,
                "outcome": "failed",
            }
        ],
        members=[
            {
                "memberId": "tree:iv_1",
                "kind": "code_tree",
                "required": True,
                "safeRelativePath": ".",
                "objectHash": H,
                "applicableCheckIds": ["python_pytest"],
                "memberTechnicalStatus": "failed",
            },
            {
                "memberId": "docs",
                "kind": "markdown",
                "required": False,
                "safeRelativePath": "README.md",
                "objectHash": H,
                "applicableCheckIds": [],
                "memberTechnicalStatus": "passed",
            },
        ],
    )
    with pytest.raises(AcceptanceError) as ei:
        validate_member_dependency_closure(payload=payload, accepted_ids={"tree:iv_1"})
    assert ei.value.code == "required_check_not_passed"
    # Independent docs still ok
    validate_member_dependency_closure(payload=payload, accepted_ids={"docs"})


def test_partial_accept_requires_members() -> None:
    with pytest.raises(ValidationError):
        AcceptanceDecisionRequest.model_validate(
            {
                "integrationVersion": "iv_1",
                "manifestHash": H,
                "manifestRevision": 1,
                "expectedStatusVersion": 1,
                "decision": "partial_accept",
                "acceptedMemberIds": [],
            }
        )


def test_full_accept_cannot_reject_members() -> None:
    with pytest.raises(ValidationError):
        AcceptanceDecisionRequest.model_validate(
            {
                "integrationVersion": "iv_1",
                "manifestHash": H,
                "manifestRevision": 1,
                "expectedStatusVersion": 1,
                "decision": "accept",
                "rejectedMemberIds": ["docs"],
            }
        )

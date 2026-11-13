"""D12.12: immutable manifest hashing stability and blocked empty results."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentforge.contracts.delivery import DeliveryManifestPayload
from agentforge.delivery.manifest import canonical_manifest_bytes, compute_manifest_hash

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
                "memberTechnicalStatus": "passed",
            }
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


def test_hash_stable_across_repeated_construction() -> None:
    a = _payload()
    b = _payload()
    assert compute_manifest_hash(a) == compute_manifest_hash(b)
    assert canonical_manifest_bytes(a) == canonical_manifest_bytes(b)


def test_unordered_members_same_hash() -> None:
    m1 = {
        "memberId": "a",
        "kind": "markdown",
        "required": False,
        "safeRelativePath": "a.md",
        "objectHash": H,
        "memberTechnicalStatus": "passed",
    }
    m2 = {
        "memberId": "b",
        "kind": "markdown",
        "required": False,
        "safeRelativePath": "b.md",
        "objectHash": H,
        "memberTechnicalStatus": "passed",
    }
    left = _payload(members=[m1, m2])
    right = _payload(members=[m2, m1])
    assert compute_manifest_hash(left) == compute_manifest_hash(right)


def test_risk_change_changes_hash() -> None:
    base = compute_manifest_hash(_payload())
    changed = compute_manifest_hash(
        _payload(knownRisks=[{"kind": "manual_step", "detail": "needs_human", "memberIds": []}])
    )
    assert base != changed


def test_check_outcome_change_changes_hash() -> None:
    base = compute_manifest_hash(_payload())
    changed = compute_manifest_hash(
        _payload(
            technicalStatus="failed",
            checks=[
                {
                    "checkId": "python_pytest",
                    "checkVersion": "v1",
                    "configHash": H,
                    "outcome": "failed",
                }
            ],
        )
    )
    assert base != changed


def test_empty_members_cannot_be_passed() -> None:
    with pytest.raises(ValidationError):
        _payload(members=[], technicalStatus="passed")


def test_empty_checks_cannot_be_passed() -> None:
    with pytest.raises(ValidationError):
        _payload(checks=[], technicalStatus="passed")


def test_blocked_empty_is_allowed() -> None:
    p = _payload(
        members=[],
        checks=[],
        technicalStatus="blocked",
        knownRisks=[{"kind": "not_run", "detail": "incomplete", "memberIds": []}],
    )
    assert p.technical_status == "blocked"
    assert compute_manifest_hash(p)

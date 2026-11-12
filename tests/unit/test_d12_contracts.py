"""D12.1: snapshot / SSE / integration / delivery contract invariants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentforge.contracts.delivery import (
    AcceptanceDecisionRequest,
    DeliveryManifestPayload,
    ManifestMember,
)
from agentforge.contracts.event_stream import (
    SseControlFrame,
    assert_business_envelope_not_control,
)
from agentforge.contracts.events import EventEnvelope, EventType, parse_event_payload
from agentforge.contracts.integrations import (
    IntegrationEvaluation,
    IntegrationSelectionRequest,
    ValidationCycleRequest,
)
from agentforge.contracts.snapshots import AgentCounts, RunSnapshot

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
H = "a" * 64


def test_sse_control_frame_rejects_business_sequence() -> None:
    ok = SseControlFrame.model_validate(
        {
            "event": "resync_required",
            "reason": "cursor_expired",
            "snapshotUrl": "/api/v1/runs/run_1",
        }
    )
    assert ok.schema_version == "1"
    with pytest.raises(ValidationError):
        SseControlFrame.model_validate(
            {
                "event": "resync_required",
                "reason": "cursor_expired",
                "sequence": 12,
            }
        )
    with pytest.raises(ValidationError):
        SseControlFrame.model_validate(
            {
                "event": "resync_required",
                "reason": "cursor_expired",
                "eventId": "evt_1",
            }
        )


def test_resync_required_must_not_allocate_run_sequence() -> None:
    envelope = EventEnvelope.model_validate(
        {
            "eventId": "evt_1",
            "runId": "run_1",
            "sequence": 1,
            "type": "resync_required",
            "timestamp": NOW,
            "payload": {"reason": "cursor_expired"},
        }
    )
    with pytest.raises(ValueError, match="control frame"):
        assert_business_envelope_not_control(envelope)


def test_selection_rejects_client_passed_and_extra_snapshots() -> None:
    ok = IntegrationSelectionRequest.model_validate(
        {
            "handoffRef": "hof_1",
            "handoffHash": H,
            "idempotencyKey": "sel-1",
        }
    )
    assert ok.handoff_ref == "hof_1"
    with pytest.raises(ValidationError):
        IntegrationSelectionRequest.model_validate(
            {
                "handoffRef": "hof_1",
                "handoffHash": H,
                "idempotencyKey": "sel-1",
                "passed": True,
            }
        )
    with pytest.raises(ValidationError):
        IntegrationSelectionRequest.model_validate(
            {
                "handoffRef": "hof_1",
                "handoffHash": H,
                "idempotencyKey": "sel-1",
                "extraSnapshotIds": ["snap_x"],
            }
        )


def test_validation_cycle_rejects_outcome_claims() -> None:
    with pytest.raises(ValidationError):
        ValidationCycleRequest.model_validate(
            {"policyRef": "policy_default", "idempotencyKey": "k1", "passed": True}
        )


def test_acceptance_rejects_technical_passed_and_invalid_sets() -> None:
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
    with pytest.raises(ValidationError):
        AcceptanceDecisionRequest.model_validate(
            {
                "integrationVersion": "iv_1",
                "manifestHash": H,
                "manifestRevision": 1,
                "expectedStatusVersion": 1,
                "decision": "partial_accept",
                "acceptedMemberIds": ["m1"],
                "rejectedMemberIds": ["m1"],
            }
        )
    ok = AcceptanceDecisionRequest.model_validate(
        {
            "integrationVersion": "iv_1",
            "manifestHash": H,
            "manifestRevision": 1,
            "expectedStatusVersion": 28,
            "decision": "partial_accept",
            "acceptedMemberIds": ["member_docs"],
            "rejectedMemberIds": ["member_code"],
            "note": "docs first",
        }
    )
    assert ok.decision == "partial_accept"


def test_passed_manifest_requires_members_and_checks() -> None:
    with pytest.raises(ValidationError):
        DeliveryManifestPayload.model_validate(
            {
                "runId": "run_1",
                "integrationVersion": "iv_1",
                "validationCycleId": "ic_1",
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
                "members": [],
                "checks": [],
                "technicalStatus": "passed",
            }
        )
    blocked = DeliveryManifestPayload.model_validate(
        {
            "runId": "run_1",
            "integrationVersion": "iv_1",
            "validationCycleId": "ic_1",
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
            "members": [],
            "checks": [],
            "technicalStatus": "blocked",
        }
    )
    assert blocked.technical_status == "blocked"


def test_empty_required_checks_cannot_evaluate_passed() -> None:
    with pytest.raises(ValidationError):
        IntegrationEvaluation.model_validate(
            {
                "cycleId": "ic_1",
                "versionId": "iv_1",
                "status": "passed",
                "requiredChecksTotal": 0,
                "requiredChecksSatisfied": 0,
                "requiredReviewsTotal": 0,
                "requiredReviewsSatisfied": 0,
            }
        )


def test_d12_event_payloads_roundtrip() -> None:
    built = parse_event_payload(
        "integration.built",
        {
            "buildId": "ib_1",
            "selectionId": "sel_1",
            "selectionHash": H,
            "status": "succeeded",
            "versionId": "iv_1",
        },
    )
    assert built.build_id == "ib_1"  # type: ignore[attr-defined]
    decided = parse_event_payload(
        "acceptance.decided",
        {
            "decisionId": "ad_1",
            "decision": "accept",
            "manifestHash": H,
            "versionId": "iv_1",
            "deliveryRevision": 2,
            "acceptanceStatus": "accepted",
            "isCurrent": True,
        },
    )
    assert decided.is_current is True  # type: ignore[attr-defined]
    assert "acceptance.decided" in EventType.__args__  # type: ignore[attr-defined]


def test_agent_counts_and_snapshot_shapes() -> None:
    counts = AgentCounts.model_validate(
        {
            "environmentId": "env_local",
            "discoveredProductCount": 2,
            "schedulableProfileCount": 1,
            "activeInstanceCount": 1,
            "inventoryRevision": 3,
            "asOf": NOW,
        }
    )
    assert counts.discovered_product_count == 2
    # RunSnapshot requires nested objects — ensure extras forbidden at top level
    with pytest.raises(ValidationError):
        RunSnapshot.model_validate({"schemaVersion": "1", "leaseToken": "x"})


def test_manifest_member_required_fields() -> None:
    member = ManifestMember.model_validate(
        {
            "memberId": "member_code",
            "kind": "code_tree",
            "required": True,
            "safeRelativePath": "src/",
            "objectHash": H,
            "memberTechnicalStatus": "passed",
        }
    )
    assert member.required is True

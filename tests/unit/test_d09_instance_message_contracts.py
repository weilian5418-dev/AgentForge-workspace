"""D09.1: instance/session/delivery/protocol/event contract facets."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentforge.contracts.agents import (
    AgentInstance,
    CurrentExecutionRef,
    ExecutionEligibility,
    InstanceOperation,
    NativeSession,
)
from agentforge.contracts.events import parse_event_payload
from agentforge.contracts.messages import (
    DeliveryAttempt,
    MessageDelivery,
    NativeEventReceipt,
    ProtocolRequest,
)
from agentforge.instances.status import (
    activity_may_accept_work,
    lifecycle_implies_active_session,
    split_legacy_instance_status,
)
from agentforge.messaging.delivery_status import (
    reply_must_not_downgrade_transmission,
    split_legacy_delivery_status,
)


def test_legacy_running_splits_into_active_busy() -> None:
    facets = split_legacy_instance_status("running")
    assert facets.lifecycle == "active"
    assert facets.activity == "busy"
    assert activity_may_accept_work(facets.activity) is False
    assert lifecycle_implies_active_session(facets.lifecycle) is True


def test_idle_is_not_stopped_and_not_stage_release() -> None:
    facets = split_legacy_instance_status("stopped")
    assert facets.lifecycle == "stopped"
    assert facets.activity == "idle"
    assert activity_may_accept_work("idle") is True
    assert lifecycle_implies_active_session("stopped") is False


def test_agent_instance_accepts_legacy_status_only() -> None:
    inst = AgentInstance.model_validate(
        {
            "instanceId": "inst_1",
            "assignmentId": "asg_1",
            "runId": "run_1",
            "roleId": "role_dev",
            "agentProfileId": "prof_1",
            "status": "waiting_input",
            "executionEnvironment": "windows_native",
        }
    )
    assert inst.lifecycle == "active"
    assert inst.activity == "waiting_input"
    assert inst.status == "waiting_input"


def test_agent_instance_rejects_contradictory_legacy_status() -> None:
    with pytest.raises(ValidationError):
        AgentInstance.model_validate(
            {
                "instanceId": "inst_1",
                "assignmentId": "asg_1",
                "runId": "run_1",
                "roleId": "role_dev",
                "agentProfileId": "prof_1",
                "lifecycle": "active",
                "activity": "idle",
                "status": "stopped",
                "executionEnvironment": "windows_native",
            }
        )


def test_current_execution_syncs_attempt_id() -> None:
    inst = AgentInstance.model_validate(
        {
            "instanceId": "inst_1",
            "assignmentId": "asg_1",
            "runId": "run_1",
            "roleId": "role_dev",
            "agentProfileId": "prof_1",
            "lifecycle": "active",
            "activity": "busy",
            "executionEnvironment": "windows_native",
            "currentExecution": {
                "workPackageId": "wp_1",
                "stageId": "stage_1",
                "attemptId": "att_1",
                "generation": 1,
            },
        }
    )
    assert inst.current_attempt_id == "att_1"
    assert isinstance(inst.current_execution, CurrentExecutionRef)


def test_native_session_and_operation_are_distinct_objects() -> None:
    now = datetime(2026, 11, 12, tzinfo=UTC)
    session = NativeSession.model_validate(
        {
            "nativeSessionId": "ns_1",
            "instanceId": "inst_1",
            "productId": "cursor",
            "profileId": "prof_1",
            "configVersion": "cfg_1",
            "environmentId": "windows_native",
            "sessionRef": "sess_abc",
            "createOperationId": "op_1",
            "bindingStatus": "active",
            "createdAt": now.isoformat(),
        }
    )
    op = InstanceOperation.model_validate(
        {
            "operationId": "op_1",
            "instanceId": "inst_1",
            "kind": "create_session",
            "status": "succeeded",
            "preparedAt": now.isoformat(),
            "completedAt": now.isoformat(),
            "nativeRef": "sess_abc",
        }
    )
    assert session.create_operation_id == op.operation_id
    assert session.binding_status == "active"
    assert op.kind == "create_session"


def test_delivery_splits_awaiting_reply_without_erasing_received() -> None:
    facets = split_legacy_delivery_status("awaiting_reply")
    assert facets.transmission == "received"
    assert facets.reply == "awaiting_reply"
    assert reply_must_not_downgrade_transmission("received", "awaiting_reply") is True
    assert reply_must_not_downgrade_transmission("sent", "awaiting_reply") is False


def test_message_delivery_rejects_reply_before_received() -> None:
    with pytest.raises(ValidationError):
        MessageDelivery.model_validate(
            {
                "deliveryId": "del_1",
                "messageId": "msg_1",
                "recipientInstanceId": "inst_1",
                "attemptId": "datt_1",
                "transmissionStatus": "sent",
                "replyStatus": "awaiting_reply",
            }
        )


def test_message_delivery_accepts_legacy_status_only() -> None:
    delivery = MessageDelivery.model_validate(
        {
            "deliveryId": "del_1",
            "messageId": "msg_1",
            "recipientInstanceId": "inst_1",
            "attemptId": "datt_1",
            "status": "dispatching",
        }
    )
    assert delivery.transmission_status == "claimed"
    assert delivery.reply_status == "not_required"
    assert delivery.status == "dispatching"


def test_delivery_attempt_and_protocol_request_contracts() -> None:
    now = datetime(2026, 11, 12, tzinfo=UTC)
    attempt = DeliveryAttempt.model_validate(
        {
            "deliveryAttemptId": "da_1",
            "deliveryId": "del_1",
            "operationId": "op_2",
            "transmissionStatus": "unknown",
            "startedAt": now.isoformat(),
        }
    )
    req = ProtocolRequest.model_validate(
        {
            "requestId": "req_1",
            "requestType": "control.stop_request",
            "allowedResponseTypes": ["control.stop_response", "control.shutdown_approved"],
            "instanceId": "inst_1",
            "status": "pending",
            "initiatedBy": "platform",
            "legalResponderRefs": ["inst_1"],
            "createdAt": now.isoformat(),
            "deadlineAt": now.isoformat(),
        }
    )
    assert attempt.transmission_status == "unknown"
    assert req.status == "pending"
    assert "control.shutdown_approved" in req.allowed_response_types


def test_native_event_receipt_and_eligibility() -> None:
    now = datetime(2026, 11, 12, tzinfo=UTC)
    receipt = NativeEventReceipt.model_validate(
        {
            "receiptId": "rcpt_1",
            "instanceId": "inst_1",
            "source": "adapter.cursor",
            "dedupeKey": "evt:1",
            "cursor": "c-10",
            "eventType": "fact.idle",
            "receivedAt": now.isoformat(),
            "authentic": True,
        }
    )
    eligibility = ExecutionEligibility.model_validate(
        {
            "runId": "run_1",
            "allowedWorkPackageIds": ["wp_1"],
            "allowedStageIds": ["stage_1"],
            "planVersion": 1,
            "assignmentVersion": 1,
            "gateDecisionRefs": [],
            "blockedReason": None,
        }
    )
    assert receipt.authentic is True
    assert eligibility.allowed_work_package_ids == ["wp_1"]


def test_d09_event_payloads_parse() -> None:
    matched = parse_event_payload(
        "protocol.request_matched",
        {
            "requestId": "req_1",
            "requestType": "control.stop_request",
            "instanceId": "inst_1",
            "status": "matched",
        },
    )
    ingested = parse_event_payload(
        "native_event.ingested",
        {
            "receiptId": "rcpt_1",
            "instanceId": "inst_1",
            "dedupeKey": "evt:1",
            "eventType": "fact.turn_finished",
            "cursor": "c-11",
        },
    )
    assert matched.model_dump(by_alias=True)["status"] == "matched"
    assert ingested.model_dump(by_alias=True)["eventType"] == "fact.turn_finished"

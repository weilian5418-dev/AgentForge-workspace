"""D09: prepare / session / message / protocol / stop acceptance (mem session)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agentforge.contracts.agents import AgentInstanceActionRequest, ExecutionRequest
from agentforge.contracts.messages import MessageCreateRequest
from agentforge.instances.actions import InstanceActionService
from agentforge.instances.eligibility import EligibilityService
from agentforge.instances.errors import InstanceError
from agentforge.instances.event_ingestor import NativeEventIngestor
from agentforge.instances.preparation import ExecutionPreparationService
from agentforge.instances.session_coordinator import SessionCoordinator
from agentforge.messaging.delivery import DeliveryCoordinator
from agentforge.messaging.protocol import ProtocolRouter
from agentforge.messaging.service import MessageService
from agentforge.storage.models import (
    AgentInstanceRow,
    AgentProfileRow,
    DirectoryGrantRow,
    InstanceOperationRow,
    MessageDeliveryRow,
    Plan,
    Project,
    RoleAssignmentRow,
    Run,
)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _MemSession:
    def __init__(self) -> None:
        self.store: dict[tuple[type, Any], Any] = {}
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)
        if isinstance(row, Plan):
            self.store[(Plan, (row.run_id, row.version))] = row
        elif isinstance(row, Run):
            self.store[(Run, row.id)] = row
        else:
            key = getattr(row, "id", None)
            if key is not None:
                self.store[(type(row), key)] = row

    def flush(self) -> None:
        return None

    def get(self, model: type, key: Any) -> Any | None:
        return self.store.get((model, key))

    def scalar(self, stmt: Any) -> Any | None:
        text = str(stmt).lower()
        rows = list(self.added)
        if "role_assignments" in text:
            matches = [r for r in rows if isinstance(r, RoleAssignmentRow)]
            return matches[0] if matches else None
        if "native_sessions" in text and "active" in text:
            from agentforge.storage.models import NativeSessionRow

            for r in rows:
                if isinstance(r, NativeSessionRow) and r.binding_status == "active":
                    return r
            return None
        if "native_sessions" in text and "create_operation" in text:
            from agentforge.storage.models import NativeSessionRow

            for r in rows:
                if isinstance(r, NativeSessionRow):
                    return r
            return None
        if "native_event_receipts" in text:
            from agentforge.storage.models import NativeEventReceiptRow

            for r in rows:
                if isinstance(r, NativeEventReceiptRow):
                    return r
            return None
        if "agent_instances" in text:
            for r in rows:
                if isinstance(r, AgentInstanceRow):
                    return r
            return None
        if "execution_requests" in text:
            from agentforge.storage.models import ExecutionRequestRow

            for r in rows:
                if isinstance(r, ExecutionRequestRow):
                    return r
            return None
        if "message_deliveries" in text:
            for r in rows:
                if isinstance(r, MessageDeliveryRow):
                    return r
            return None
        if "instance_operations" in text:
            for r in rows:
                if isinstance(r, InstanceOperationRow):
                    return r
            return None
        return None

    def scalars(self, stmt: Any) -> _Result:
        text = str(stmt).lower()
        if "role_assignments" in text:
            return _Result([r for r in self.added if isinstance(r, RoleAssignmentRow)])
        if "agent_instances" in text:
            return _Result([r for r in self.added if isinstance(r, AgentInstanceRow)])
        if "stages" in text:
            return _Result([])
        if "gate_decisions" in text:
            return _Result([])
        if "collaboration_messages" in text:
            from agentforge.storage.models import CollaborationMessageRow

            return _Result([r for r in self.added if isinstance(r, CollaborationMessageRow)])
        if "message_deliveries" in text:
            return _Result([r for r in self.added if isinstance(r, MessageDeliveryRow)])
        if "instance_operations" in text:
            return _Result([r for r in self.added if isinstance(r, InstanceOperationRow)])
        return _Result([])


class _OkAdapter:
    adapter_id = "fake"
    adapter_version = "1"

    def create_session(self, *, profile_id: str, workspace_id: str, operation_id: str) -> dict:
        return {
            "support": "supported",
            "sessionRef": f"sess_{operation_id}",
            "workspaceId": workspace_id,
            "productId": "product_a",
        }

    def deliver_message(self, *, session_ref: str, message: dict) -> dict:
        return {"support": "supported", "receiptRef": f"rcpt_{session_ref}", "received": True}

    def request_stop(self, *, session_ref: str, operation_id: str) -> dict:
        return {"support": "supported", "accepted": True, "nativeRequestId": "nstop_1"}

    def inspect_execution(self, *, session_ref: str) -> dict:
        return {"running": False}

    def close_session(self, *, session_ref: str) -> dict:
        return {"support": "supported"}

    def resume_or_handoff(self, *, session_ref: str, context: dict) -> dict:
        return {"support": "unsupported"}


class _UnknownCreateAdapter(_OkAdapter):
    def create_session(self, *, profile_id: str, workspace_id: str, operation_id: str) -> dict:
        return {"support": "unknown"}


class _StopStillRunningAdapter(_OkAdapter):
    def inspect_execution(self, *, session_ref: str) -> dict:
        return {"running": True}


PLAN_HASH = "a" * 64


def _seed(session: _MemSession, *, two_roles: bool = False) -> None:
    now = datetime.now(UTC)
    session.add(
        DirectoryGrantRow(
            id="gr_1",
            authorized_user="local",
            environment_id="env_local",
            normalized_root="D:/proj",
            directory_identity="dir_1",
            allowed_operations=["read", "write"],
            grant_version=1,
            status="active",
            created_at=now,
            revoked_at=None,
        )
    )
    session.add(
        Project(
            id="prj_1",
            directory_grant_id="gr_1",
            display_name="demo",
            root_path="D:/proj",
            workspace_root="D:/proj",
            trust_policy={},
            environment_id="env_local",
            grant_version=1,
            current_baseline_id="bl_1",
            grant_status="active",
            revision=1,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        Run(
            id="run_1",
            project_id="prj_1",
            goal="ship",
            mode="team",
            status="preparing",
            status_version=3,
            active_generation=1,
            last_sequence=0,
            plan_version=1,
            assignment_version=1,
            baseline_id="bl_1",
            execution_profile="host_authorized",
            failure_policy="dependency-aware",
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        Plan(
            run_id="run_1",
            version=1,
            contract_hash="b" * 64,
            plan_hash=PLAN_HASH,
            status="approved",
            summary="ok",
            gate_policy_version=1,
            created_at=now,
        )
    )
    session.add(
        AgentProfileRow(
            id="prof_a",
            installation_id="instl_a",
            adapter_id="product_a",
            adapter_version="1",
            config_version="cfg_1",
            config_hash="c" * 64,
            auth_status="authenticated",
            access_level="limited_auto",
            created_at=now,
        )
    )
    session.add(
        RoleAssignmentRow(
            id="asg_dev",
            run_id="run_1",
            plan_version=1,
            assignment_version=1,
            role_id="role_dev",
            agent_profile_id="prof_a",
            profile_config_version="cfg_1",
            confirmed_at=now,
            created_at=now,
        )
    )
    if two_roles:
        session.add(
            AgentProfileRow(
                id="prof_b",
                installation_id="instl_b",
                adapter_id="product_b",
                adapter_version="1",
                config_version="cfg_1",
                config_hash="d" * 64,
                auth_status="authenticated",
                access_level="limited_auto",
                created_at=now,
            )
        )
        session.add(
            RoleAssignmentRow(
                id="asg_rev",
                run_id="run_1",
                plan_version=1,
                assignment_version=1,
                role_id="role_reviewer",
                agent_profile_id="prof_b",
                profile_config_version="cfg_1",
                confirmed_at=now,
                created_at=now,
            )
        )


def _exec_req() -> ExecutionRequest:
    return ExecutionRequest.model_validate(
        {
            "planHash": PLAN_HASH,
            "planVersion": 1,
            "assignmentVersion": 1,
            "gatePolicyVersion": 1,
        }
    )


def test_unapproved_or_stale_plan_does_not_prepare() -> None:
    session = _MemSession()
    _seed(session)
    plan = session.get(Plan, ("run_1", 1))
    assert plan is not None
    plan.status = "proposed"
    with pytest.raises(InstanceError) as exc:
        ExecutionPreparationService(session).prepare(
            "run_1", _exec_req(), actor="test", idempotency_key="k1"
        )
    assert exc.value.code == "plan_not_approved"


def test_prepare_two_roles_separate_instances() -> None:
    session = _MemSession()
    _seed(session, two_roles=True)
    accepted = ExecutionPreparationService(session).prepare(
        "run_1", _exec_req(), actor="test", idempotency_key="k2"
    )
    assert accepted.status == "accepted"
    instances = [r for r in session.added if isinstance(r, AgentInstanceRow)]
    assert len(instances) == 2
    assert {i.role_id for i in instances} == {"role_dev", "role_reviewer"}
    assert instances[0].id != instances[1].id
    # Idempotent replay
    again = ExecutionPreparationService(session).prepare(
        "run_1", _exec_req(), actor="test", idempotency_key="k2"
    )
    assert again.operation_id == accepted.operation_id
    assert len([r for r in session.added if isinstance(r, AgentInstanceRow)]) == 2


def test_session_create_unknown_enters_reconciliation_not_second_session() -> None:
    session = _MemSession()
    _seed(session)
    ExecutionPreparationService(session).prepare(
        "run_1", _exec_req(), actor="test", idempotency_key="k3"
    )
    coord = SessionCoordinator(
        session, adapter_factory=lambda _aid: _UnknownCreateAdapter()
    )
    results = coord.process_create_claims(owner="test")
    assert results
    assert results[0]["status"] in ("unknown", "needs_reconciliation", "failed")
    from agentforge.storage.models import NativeSessionRow

    assert not [r for r in session.added if isinstance(r, NativeSessionRow)]


def test_session_create_success_binds_once() -> None:
    session = _MemSession()
    _seed(session)
    ExecutionPreparationService(session).prepare(
        "run_1", _exec_req(), actor="test", idempotency_key="k4"
    )
    coord = SessionCoordinator(session, adapter_factory=lambda _aid: _OkAdapter())
    results = coord.process_create_claims(owner="test")
    assert results[0]["status"] == "succeeded"
    inst = next(r for r in session.added if isinstance(r, AgentInstanceRow))
    assert inst.session_ref
    assert inst.lifecycle == "active"
    # Second create_session op must not open another active session.
    op2 = InstanceOperationRow(
        id="iop_dup",
        instance_id=inst.id,
        kind="create_session",
        status="claimed",
        claim_owner="test",
        claim_token="tok",
        prepared_at=datetime.now(UTC),
    )
    session.add(op2)
    with pytest.raises(InstanceError) as exc:
        coord._complete_create_session("iop_dup", owner="test", token="tok")
    assert exc.value.code == "active_session_exists"


def test_eligibility_blocks_later_stage_without_gate() -> None:
    session = _MemSession()
    _seed(session)
    result = EligibilityService(session).check(
        run_id="run_1", stage_id="stage_later", work_package_id="wp_1"
    )
    assert result.blocked_reason == "gate_decision_missing"
    with pytest.raises(InstanceError) as exc:
        EligibilityService(session).assert_may_dispatch(
            run_id="run_1", stage_id="stage_later", work_package_id="wp_1"
        )
    assert exc.value.code == "execution_blocked"


def test_message_delivery_and_assignment_revoke() -> None:
    session = _MemSession()
    _seed(session)
    ExecutionPreparationService(session).prepare(
        "run_1", _exec_req(), actor="test", idempotency_key="k5"
    )
    SessionCoordinator(session, adapter_factory=lambda _aid: _OkAdapter()).process_create_claims(
        owner="test"
    )
    inst = next(r for r in session.added if isinstance(r, AgentInstanceRow))
    msg = MessageService(session).create(
        "run_1",
        MessageCreateRequest.model_validate(
            {
                "recipientRefs": [inst.id],
                "payload": {
                    "type": "clarification.request",
                    "question": "Which API?",
                },
            }
        ),
        sender_ref="user:test",
        assignment_version=1,
    )
    assert msg.message_id
    deliveries = [r for r in session.added if isinstance(r, MessageDeliveryRow)]
    assert len(deliveries) == 1
    DeliveryCoordinator(session, adapter_factory=lambda _aid: _OkAdapter()).process_claims(
        owner="test"
    )
    assert deliveries[0].transmission_status in ("sent", "received")

    # Revoke assignment version on recipient → old delivery rejected
    deliveries[0].transmission_status = "pending"
    deliveries[0].status = "pending"
    deliveries[0].claim_owner = None
    deliveries[0].assignment_version = 1
    inst.assignment_version = 2
    out = DeliveryCoordinator(
        session, adapter_factory=lambda _aid: _OkAdapter()
    ).process_claims(owner="test2")
    assert out[0]["status"] == "failed"
    assert out[0].get("error") == "assignment_revoked"


def test_protocol_rejects_wrong_type_and_control_not_to_model() -> None:
    session = _MemSession()
    _seed(session)
    ExecutionPreparationService(session).prepare(
        "run_1", _exec_req(), actor="test", idempotency_key="k6"
    )
    inst = next(r for r in session.added if isinstance(r, AgentInstanceRow))
    action = InstanceActionService(session).request_action(
        inst.id,
        AgentInstanceActionRequest.model_validate({"action": "request_stop"}),
        actor="test",
    )
    from agentforge.storage.models import ProtocolRequestRow

    preq = next(r for r in session.added if isinstance(r, ProtocolRequestRow))
    router = ProtocolRouter(session)
    assert router.route_inbound("control.shutdown_approved", {})["deliverToModel"] is False
    with pytest.raises(InstanceError) as exc:
        router.match_response(
            request_id=preq.id,
            response_type="fact.idle",
            responder_ref=inst.id,
            instance_id=inst.id,
            response_ref="resp_1",
        )
    assert exc.value.code == "illegal_response_type"
    matched = router.match_response(
        request_id=preq.id,
        response_type="control.shutdown_approved",
        responder_ref=inst.id,
        instance_id=inst.id,
        response_ref="resp_2",
    )
    assert matched["status"] == "matched"
    assert matched["impliesStopped"] is False
    assert action.operation_id


def test_stop_accepted_but_running_stays_unknown() -> None:
    session = _MemSession()
    _seed(session)
    ExecutionPreparationService(session).prepare(
        "run_1", _exec_req(), actor="test", idempotency_key="k7"
    )
    SessionCoordinator(session, adapter_factory=lambda _aid: _OkAdapter()).process_create_claims(
        owner="test"
    )
    inst = next(r for r in session.added if isinstance(r, AgentInstanceRow))
    accepted = InstanceActionService(session).request_action(
        inst.id,
        AgentInstanceActionRequest.model_validate({"action": "request_stop"}),
        actor="test",
    )
    svc = InstanceActionService(
        session, adapter_factory=lambda _aid: _StopStillRunningAdapter()
    )
    result = svc.execute_stop_handshake(accepted.operation_id)
    assert result["status"] == "unknown"
    assert result.get("running") is True
    assert inst.lifecycle == "stop_requested"


def test_message_cannot_forge_stage_release_or_set_authentic() -> None:
    session = _MemSession()
    _seed(session)
    with pytest.raises(Exception):
        MessageCreateRequest.model_validate(
            {
                "recipientRefs": ["inst_1"],
                "payload": {
                    "type": "stage.release",
                    "stageId": "s1",
                    "gateDecisionId": "g1",
                    "candidateId": "c1",
                    "manifestHash": "a" * 64,
                },
            }
        )
    with pytest.raises(InstanceError) as exc:
        NativeEventIngestor(session).ingest(
            instance_id="missing",
            source="adapter",
            dedupe_key="d1",
            event_type="fact.idle",
            payload={},
            authentic=True,
            service_authenticated=False,
        )
    assert exc.value.code == "untrusted_authentic_flag"


def test_unauthenticated_idle_is_not_trusted_boundary() -> None:
    session = _MemSession()
    _seed(session)
    ExecutionPreparationService(session).prepare(
        "run_1", _exec_req(), actor="test", idempotency_key="k8"
    )
    inst = next(r for r in session.added if isinstance(r, AgentInstanceRow))
    inst.lifecycle = "active"
    inst.activity = "busy"
    out = NativeEventIngestor(session).ingest(
        instance_id=inst.id,
        source="adapter",
        dedupe_key="idle-1",
        event_type="fact.idle",
        payload={"text": "idle"},
        authentic=False,
        service_authenticated=True,
    )
    assert out["trusted"] is False
    assert inst.activity == "busy"
    trusted = NativeEventIngestor(session).ingest(
        instance_id=inst.id,
        source="adapter",
        dedupe_key="idle-2",
        event_type="fact.idle",
        payload={},
        authentic=True,
        service_authenticated=True,
    )
    assert trusted["trusted"] is True
    assert inst.activity == "idle"

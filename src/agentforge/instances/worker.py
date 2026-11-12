"""Durable collaboration operations: commit claim, external I/O, commit receipt.

Once native dispatch may have happened, redelivery never invokes it again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from agentforge.adapters.registry import get_adapter
from agentforge.contracts.plans import PlanSpec
from agentforge.instances.bindings import require_profile
from agentforge.instances.context_loader import ExecutionContextLoader
from agentforge.instances.eligibility import EligibilityService
from agentforge.instances.errors import InstanceError
from agentforge.instances.lanes import native_lane
from agentforge.storage.event_log import append_fact
from agentforge.storage.models import (
    AgentInstanceRow,
    AgentProfileRow,
    CollaborationMessageRow,
    DeliveryAttemptRow,
    InstanceAttemptRow,
    InstanceOperationRow,
    MessageDeliveryRow,
    NativeSessionRow,
    Plan,
    Run,
    StageRow,
)
from agentforge.storage.required import required
from agentforge.storage.uow import unit_of_work_connection


def now() -> Any:
    return datetime.now(UTC)


def _wake_instance_operation(session: Any, run_id: str, op: InstanceOperationRow) -> None:
    from agentforge.driver.signals import record_wake_signal

    version = op.completed_at.isoformat() if op.completed_at else op.status
    record_wake_signal(
        session,
        run_id=run_id,
        source_kind="instance_operation",
        source_ref=f"{op.id}:{version}",
        binding={"operationId": op.id, "kind": op.kind, "status": op.status},
        priority=60,
    )


def process_instance_operation(
    engine: Any, operation_id: str, *, adapter_factory: Any = None
) -> Any:
    with unit_of_work_connection(engine) as uow:
        op = required(uow.session, InstanceOperationRow, operation_id)
        if op is None:
            raise InstanceError("operation_not_found", operation_id)
        instance_id = op.instance_id
    with native_lane(engine, instance_id):
        return _process_instance_operation(engine, operation_id, adapter_factory=adapter_factory)


def _process_instance_operation(
    engine: Any, operation_id: str, *, adapter_factory: Any = None
) -> Any:
    with unit_of_work_connection(engine) as uow:
        session = uow.session
        hint = required(session, InstanceOperationRow, operation_id)
        if hint is None:
            raise InstanceError("operation_not_found", operation_id)
        instance = required(session, AgentInstanceRow, hint.instance_id)
        run = required(session, Run, instance.run_id, with_for_update=True, populate_existing=True)
        op = required(
            session,
            InstanceOperationRow,
            operation_id,
            with_for_update=True,
            populate_existing=True,
        )
        if op.kind == "create_session" and run.status not in {"preparing", "running"}:
            raise InstanceError("execution_blocked", "Run does not admit new sessions")
        if op.status != "prepared":
            return {"status": op.status, "operationId": op.id}
        stopping = op.kind in {"request_stop", "close"}
        if stopping and any(
            op.payload_json.get(key) != expected
            for key, expected in {
                "sessionRef": instance.session_ref,
                "nativeSessionId": instance.native_session_id,
                "generation": instance.generation,
            }.items()
        ):
            raise InstanceError(
                "stale_stop_request", "Old stop request cannot target a replacement session"
            )
        ctx = ExecutionContextLoader(session).load(
            instance_id=instance.id,
            operation_id=op.id,
            require_session=op.kind != "create_session",
            historical_stop=stopping,
        )
        profile = required(session, AgentProfileRow, instance.agent_profile_id)
        if profile is None:
            raise InstanceError("profile_not_found", instance.agent_profile_id)
        plan = required(session, Plan, (run.id, instance.plan_version))
        spec = PlanSpec.model_validate(plan.spec_json)
        role = next(r for r in spec.roles if r.role_id == instance.role_id)
        if not stopping:
            require_profile(session, profile.id, set(role.required_capabilities))
        if op.kind == "close" and instance.lifecycle != "stopped":
            raise InstanceError("not_stopped", "Stop before close")
        token = uuid.uuid4().hex
        op.status = "unknown"
        op.claim_token = token
        prior_payload = dict(op.payload_json or {})
        op.payload_json = {
            **prior_payload,
            "dispatchStartedAt": now().isoformat(),
            "context": ctx.as_adapter_payload(),
        }
        resume_binding = (
            str(prior_payload.get("sessionRef") or ctx.session_ref or ""),
            str(prior_payload.get("nativeSessionId") or instance.native_session_id or ""),
        )
        snapshot = (op.kind, profile.adapter_id, ctx, token, resume_binding)
    kind, adapter_id, ctx, token, resume_binding = snapshot
    try:
        adapter = (adapter_factory or get_adapter)(adapter_id)
        if kind == "create_session":
            result = adapter.create_session(
                profile_id=ctx.profile_id,
                workspace_id=ctx.workspace_id,
                operation_id=ctx.operation_id,
                context=ctx.as_adapter_payload(),
            )
        elif kind == "request_stop":
            result = adapter.request_stop(
                session_ref=ctx.session_ref, operation_id=ctx.operation_id
            )
            inspection = adapter.inspect_execution(session_ref=ctx.session_ref)
            # Require explicit child and write quiescence; running=False alone is insufficient.
            result = {
                "stop": result,
                "inspection": inspection,
                "status": "confirmed"
                if (
                    inspection.get("running") is False
                    and inspection.get("childrenRunning") is False
                    and inspection.get("writesQuiesced") is True
                    and inspection.get("sessionRef") == ctx.session_ref
                    and inspection.get("workspaceId") == ctx.workspace_id
                )
                else "unknown",
            }
        elif kind == "close":
            result = adapter.close_session(session_ref=ctx.session_ref)
        elif kind == "resume":
            from agentforge.recovery.resume import classify_resume_result

            raw_resume = adapter.resume_or_handoff(
                session_ref=ctx.session_ref, context=ctx.as_adapter_payload()
            )
            if not isinstance(raw_resume, dict):
                raw_resume = {"status": "unknown", "detail": "non-dict resume result"}
            classified = classify_resume_result(
                raw_resume,
                expected_session_ref=resume_binding[0],
                expected_native_session_id=resume_binding[1],
            )
            # Map coordinator kinds onto ledger statuses without inventing success.
            ledger_status = {
                "resumed_existing": "succeeded",
                "handoff_required": "failed",
                "unsupported": "failed",
                "failed": "failed",
                "unknown": "unknown",
            }.get(classified.kind, "unknown")
            result = {
                "status": ledger_status,
                "resumeKind": classified.kind,
                "resume": classified.model_dump(mode="json", by_alias=True),
                "raw": raw_resume,
            }
        else:
            result = {"status": "unsupported"}
    except Exception:
        result = {"status": "unknown", "detail": "Native outcome requires reconciliation"}
    with unit_of_work_connection(engine) as uow:
        session = uow.session
        op = required(
            session,
            InstanceOperationRow,
            operation_id,
            with_for_update=True,
            populate_existing=True,
        )
        instance = required(session, AgentInstanceRow, ctx.instance_id)
        run = required(session, Run, instance.run_id)
        if op.claim_token != token:
            return {"status": "stale_result"}
        op.payload_json = {**op.payload_json, "nativeResult": result}
        if kind == "create_session" and (
            run.plan_version != instance.plan_version
            or run.assignment_version != ctx.assignment_version
            or run.active_generation != ctx.generation
        ):
            op.error_code = "stale_native_result"
            _wake_instance_operation(session, run.id, op)
            return {"status": "unknown"}
        support = result.get("status") or result.get("support")
        if kind == "create_session":
            ref = result.get("sessionRef")
            if (
                support not in {"succeeded", "confirmed", "acked"}
                or not ref
                or result.get("workspaceId") != ctx.workspace_id
                or result.get("cwd") != ctx.workspace_root
            ):
                op.error_code = "native_session_unverified"
                _wake_instance_operation(session, run.id, op)
                return {"status": "unknown"}
            native = NativeSessionRow(
                id="ns_" + uuid.uuid4().hex,
                instance_id=instance.id,
                product_id=result.get("productId") or adapter_id,
                profile_id=ctx.profile_id,
                config_version=required(session, AgentProfileRow, ctx.profile_id).config_version,
                environment_id=ctx.environment_id,
                session_ref=ref,
                create_operation_id=op.id,
                binding_status="active",
                workspace_id=ctx.workspace_id,
                evidence_json=result,
            )
            session.add(native)
            session.flush()
            instance.native_session_id = native.id
            instance.session_ref = ref
            instance.lifecycle = "active"
            instance.activity = "idle"
            instance.status = "idle"
        elif kind == "request_stop" and support == "confirmed":
            op.payload_json = {
                **op.payload_json,
                "stopProof": {
                    "source": "adapter_host",
                    "writesQuiesced": True,
                    "workspaceId": ctx.workspace_id,
                    "attemptId": ctx.attempt_id,
                    "generation": ctx.generation,
                    "sessionRef": ctx.session_ref,
                    "checkedAt": now().isoformat(),
                    "inspection": result["inspection"],
                },
            }
            instance.lifecycle = "stopped"
            instance.activity = "idle"
            instance.status = "stopped"
        elif kind == "close" and support in {"confirmed", "acked", "succeeded"}:
            native = required(session, NativeSessionRow, instance.native_session_id)
            native.binding_status = "closed"
            native.closed_at = now()
            instance.lifecycle = "closed"
            instance.status = "closed"
        elif kind == "resume":
            resume_kind = result.get("resumeKind")
            if resume_kind == "resumed_existing" and support == "succeeded":
                instance.lifecycle = "active"
                instance.activity = "idle"
                instance.status = "idle"
                op.status = "succeeded"
                op.completed_at = now()
                op.claim_token = None
                _wake_instance_operation(session, run.id, op)
                return {"status": "succeeded", "resumeKind": resume_kind}
            if resume_kind in {"handoff_required", "unsupported", "failed"}:
                op.status = "failed"
                op.error_code = str(resume_kind)
                op.completed_at = now()
                op.claim_token = None
                _wake_instance_operation(session, run.id, op)
                return {"status": "failed", "resumeKind": resume_kind}
            op.error_code = "native_outcome_unknown"
            _wake_instance_operation(session, run.id, op)
            return {"status": "unknown", "resumeKind": resume_kind}
        else:
            op.error_code = "native_outcome_unknown"
            _wake_instance_operation(session, run.id, op)
            return {"status": "unknown"}
        if kind == "create_session" and op.payload_json.get("executionRequestId"):
            from agentforge.storage.models import ExecutionRequestRow

            request = required(session, ExecutionRequestRow, op.payload_json["executionRequestId"])
            pending = list(
                session.scalars(
                    select(InstanceOperationRow).where(
                        InstanceOperationRow.kind == "create_session",
                        InstanceOperationRow.status == "prepared",
                    )
                ).all()
            )
            if not [
                other
                for other in pending
                if other.id != op.id and other.payload_json.get("executionRequestId") == request.id
            ]:
                request.status = "succeeded"
        op.status = "succeeded"
        op.completed_at = now()
        op.claim_token = None
        instance.updated_at = now()
        event = "instance.started" if kind == "create_session" else "instance.stopped"
        if kind != "close":
            append_fact(
                session,
                run.id,
                event,
                {
                    "instanceId": instance.id,
                    "roleId": instance.role_id,
                    "agentProfileId": instance.agent_profile_id,
                    "stageId": instance.stage_id,
                    "sessionRef": instance.session_ref,
                },
            )
        _wake_instance_operation(session, run.id, op)
        return {"status": "succeeded", "operationId": op.id}


def process_delivery(engine: Any, delivery_id: str, *, adapter_factory: Any = None) -> Any:
    with unit_of_work_connection(engine) as uow:
        row = required(uow.session, MessageDeliveryRow, delivery_id)
        if row is None:
            raise InstanceError("delivery_not_found", delivery_id)
        instance_id = row.recipient_instance_id
    with native_lane(engine, instance_id):
        return _process_delivery(engine, delivery_id, adapter_factory=adapter_factory)


def _process_delivery(engine: Any, delivery_id: str, *, adapter_factory: Any = None) -> Any:
    with unit_of_work_connection(engine) as uow:
        session = uow.session
        hint = required(session, MessageDeliveryRow, delivery_id)
        if hint is None:
            raise InstanceError("delivery_not_found", delivery_id)
        message = required(session, CollaborationMessageRow, hint.message_id)
        run = required(session, Run, message.run_id, with_for_update=True, populate_existing=True)
        delivery = required(
            session, MessageDeliveryRow, delivery_id, with_for_update=True, populate_existing=True
        )
        if delivery.transmission_status not in {"pending"}:
            return {"status": delivery.transmission_status}
        instance = required(
            session,
            AgentInstanceRow,
            delivery.recipient_instance_id,
            with_for_update=True,
            populate_existing=True,
        )
        if (
            run.status not in {"preparing", "running"}
            or delivery.plan_version != run.plan_version
            or delivery.assignment_version != run.assignment_version
            or instance.assignment_version != run.assignment_version
            or instance.lifecycle != "active"
        ):
            delivery.transmission_status = "failed"
            delivery.status = "failed"
            delivery.error_code = "stale_delivery"
            return {"status": "failed"}
        if delivery.delivery_mode == "manual_assisted":
            return {"status": "manual_assisted"}
        if delivery.delivery_mode == "next_safe_boundary" and instance.activity not in {
            "idle",
            "waiting_input",
        }:
            raise InstanceError("boundary_not_ready", "Delivery awaits a trusted safe boundary")
        if message.message_type == "task.assignment":
            stage_id = message.stage_id or message.payload.get("stageId")
            wp_id = message.work_package_id or message.payload.get("workPackageId")
            reserved_attempt_id = message.payload.get("attemptId") or message.payload.get(
                "attempt_id"
            )
            if not stage_id or not wp_id:
                raise InstanceError("execution_blocked", "Task must bind stage and work package")
            if not reserved_attempt_id:
                raise InstanceError(
                    "execution_blocked",
                    "Task must reference a reserved InstanceAttempt (D11 dispatch)",
                )
            if run.status not in {"preparing", "running"}:
                delivery.transmission_status = "failed"
                delivery.status = "failed"
                delivery.error_code = "run_control_blocked"
                return {"status": "failed"}
            EligibilityService(session).assert_may_dispatch(
                run_id=run.id, stage_id=stage_id, work_package_id=wp_id
            )
            plan = required(session, Plan, (run.id, run.plan_version))
            spec = PlanSpec.model_validate(plan.spec_json)
            wp = next(w for w in spec.work_packages if w.id == wp_id)
            if wp.owner_role_id != instance.role_id:
                raise InstanceError("illegal_recipient", "Task belongs to a different role")
            if instance.activity == "busy":
                raise InstanceError("instance_busy", "Current execution has not stopped")
            attempt = required(
                session,
                InstanceAttemptRow,
                reserved_attempt_id,
                with_for_update=True,
                populate_existing=True,
            )
            if (
                attempt.run_id != run.id
                or attempt.work_package_id != wp_id
                or attempt.instance_id != instance.id
                or attempt.generation != run.active_generation
            ):
                raise InstanceError("stale_attempt", "Reserved attempt does not match delivery")
            if attempt.status == "scheduled":
                attempt.status = "dispatching"
                attempt.dispatch_delivery_id = delivery.id
                attempt.started_at = attempt.started_at or now()
            elif attempt.status in {"dispatching", "running", "unknown"}:
                # Crash before native send: reuse the same reserved slot; never mint another.
                if attempt.dispatch_delivery_id not in {None, delivery.id}:
                    raise InstanceError(
                        "attempt_bound_elsewhere",
                        "Reserved attempt already bound to another delivery",
                    )
                attempt.dispatch_delivery_id = delivery.id
            else:
                raise InstanceError(
                    "illegal_attempt_status",
                    f"Attempt status {attempt.status} cannot be dispatched",
                )
            instance.current_attempt_id = attempt.id
            instance.stage_id = stage_id
            instance.current_work_package_id = wp_id
            stage = required(session, StageRow, stage_id)
            stage.status = "executing"
        ctx = ExecutionContextLoader(session).load(
            instance_id=instance.id, operation_id=delivery.id, require_session=True
        )
        token = uuid.uuid4().hex
        delivery.claim_token = token
        delivery.transmission_status = "unknown"
        delivery.status = "unknown"
        instance.activity = "busy"
        instance.status = "running"
        attempt_row = DeliveryAttemptRow(
            id="da_" + uuid.uuid4().hex,
            delivery_id=delivery.id,
            operation_id=delivery.id,
            transmission_status="unknown",
            started_at=now(),
        )
        session.add(attempt_row)
        envelope = {
            "messageId": message.id,
            "type": message.message_type,
            "payload": message.payload,
            "source": message.sender_ref,
            "planVersion": run.plan_version,
            "assignmentVersion": run.assignment_version,
            "context": ctx.as_adapter_payload(),
            "deliveryId": delivery.id,
        }
        if message.request_id:
            from agentforge.storage.models import (
                ArtifactCandidateRow,
                CandidateSetRow,
                ReviewRequestRow,
            )

            review = session.get(ReviewRequestRow, message.request_id)
            if review is not None:
                if instance.id not in review.legal_responder_refs or review.status != "pending":
                    raise InstanceError(
                        "illegal_reviewer", "Review request is not assigned to this receiver"
                    )
                from agentforge.artifacts.store import ArtifactStore
                from agentforge.core.config import Settings
                from agentforge.core.service_identity import issue_ticket

                configuration = Settings()
                store = ArtifactStore(configuration.state_dir / "artifact_store")
                cs = required(session, CandidateSetRow, review.candidate_set_id)
                excerpts = []
                size = 0
                for member in cs.members:
                    candidate = required(session, ArtifactCandidateRow, member["candidateId"])
                    content = store.get_bytes(candidate.content_hash)
                    size += len(content)
                    if size > 256_000:
                        raise InstanceError(
                            "review_context_too_large", "Review requires bounded retrieval support"
                        )
                    try:
                        text = content.decode("utf-8")
                    except UnicodeDecodeError:
                        text = None
                    excerpts.append(
                        {
                            "candidateId": candidate.id,
                            "contentHash": candidate.content_hash,
                            "path": candidate.relative_path,
                            "text": text,
                            "binaryContentUnavailable": text is None,
                        }
                    )
                envelope["reviewContext"] = {
                    "requestId": review.id,
                    "candidateSetHash": cs.manifest_hash,
                    "contractHash": cs.contract_hash,
                    "members": excerpts,
                    "responseTicket": issue_ticket(
                        configuration,
                        subject=instance.id,
                        audience="stage-review",
                        request_id=review.id,
                        ttl=300,
                    ),
                }
        profile = required(session, AgentProfileRow, instance.agent_profile_id)
        if instance.stage_id:
            bound_stage = required(session, StageRow, instance.stage_id)
            plan = required(session, Plan, (run.id, run.plan_version))
            role = next(
                r
                for r in PlanSpec.model_validate(plan.spec_json).roles
                if r.role_id == instance.role_id
            )
            if bound_stage.write_access_closed and role.write_scopes:
                raise InstanceError(
                    "stage_writes_closed",
                    "No new development input is allowed during submission/review",
                )
        adapter_id = profile.adapter_id
        require_profile(session, profile.id, set())
    try:
        adapter = (adapter_factory or get_adapter)(adapter_id)
        if envelope["type"] == "task.assignment":
            result = adapter.submit_task(session_ref=ctx.session_ref, payload=envelope)
        else:
            result = adapter.deliver_message(session_ref=ctx.session_ref, message=envelope)
    except Exception:
        result = {"status": "unknown"}
    with unit_of_work_connection(engine) as uow:
        d = required(
            uow.session,
            MessageDeliveryRow,
            delivery_id,
            with_for_update=True,
            populate_existing=True,
        )
        if d.claim_token != token:
            return {"status": "stale_result"}
        status = result.get("status") or result.get("support")
        receipt = result.get("receiptRef")
        if status in {"received", "acked", "confirmed"} and receipt:
            d.transmission_status = "received"
            d.status = "received"
            d.received_at = now()
            d.native_receipt_ref = receipt
        elif status == "sent" and receipt:
            d.transmission_status = "sent"
            d.status = "sent"
            d.native_receipt_ref = receipt
        elif status == "unsupported":
            d.transmission_status = "failed"
            d.status = "failed"
        d.claim_token = None
        d.updated_at = now()
        row = required(uow.session, DeliveryAttemptRow, attempt_row.id)
        row.transmission_status = d.transmission_status
        row.finished_at = now()
        row.native_receipt_ref = receipt
        if message.message_type == "task.assignment":
            reserved_id = message.payload.get("attemptId") or message.payload.get("attempt_id")
            if reserved_id:
                ia = uow.session.get(InstanceAttemptRow, reserved_id)
                if ia is not None and ia.status in {"dispatching", "scheduled"}:
                    if d.transmission_status in {"received", "sent"}:
                        ia.status = "running"
                    elif d.transmission_status == "failed":
                        ia.status = "failed"
                        ia.error_code = d.error_code or "delivery_failed"
                    else:
                        # Native side effect may have occurred — occupy the slot as unknown.
                        ia.status = "unknown"
        append_fact(
            uow.session,
            message.run_id,
            "message.delivery_unknown"
            if d.transmission_status == "unknown"
            else "message.delivered",
            {
                "messageId": d.message_id,
                "deliveryId": d.id,
                "recipientInstanceId": d.recipient_instance_id,
                "status": d.status,
                "transmissionStatus": d.transmission_status,
                "replyStatus": d.reply_status,
            },
        )
        from agentforge.driver.signals import record_wake_signal

        result_version = d.transmission_status or d.status or "unknown"
        record_wake_signal(
            uow.session,
            run_id=message.run_id,
            source_kind="delivery",
            source_ref=f"{d.id}:{result_version}",
            binding={"deliveryId": d.id, "status": result_version},
            priority=70,
        )
        return {"status": d.transmission_status, "deliveryId": d.id}

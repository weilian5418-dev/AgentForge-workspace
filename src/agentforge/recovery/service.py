"""RecoveryCase open + apply_decision with version/hash fencing (D11.9)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.recovery import (
    RecoveryCase,
    RecoveryDecision,
    RecoveryDecisionRequest,
    RecoveryRecommendation,
    RecoveryReport,
)
from agentforge.contracts.runs import OperationAccepted
from agentforge.driver.binding import sha256_hex
from agentforge.recovery.errors import RecoveryError
from agentforge.recovery.policy import RecoveryContext, decide_recovery
from agentforge.storage.event_log import append_fact
from agentforge.storage.models import (
    AgentInstanceRow,
    InstanceAttemptRow,
    InstanceOperationRow,
    NativeSessionRow,
    Outbox,
    RecoveryCaseRow,
    RecoveryDecisionRow,
    RecoveryObservationRow,
    Run,
)
from agentforge.storage.uow import db_now


def _now(session: Session | None = None) -> datetime:
    if session is not None:
        try:
            value = db_now(session)
            if getattr(value, "tzinfo", None) is None:
                return value.replace(tzinfo=UTC)
            return value
        except Exception:
            pass
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


AUTO_APPLY_ACTIONS = frozenset(
    {
        "observe_existing",
        "continue_stage_processing",
        "await_reconciliation",
        "await_stop_verification",
        "wait_authorization",
        "rebuild_projection",
    }
)


def _to_case(row: RecoveryCaseRow) -> RecoveryCase:
    return RecoveryCase.model_validate(
        {
            "caseId": row.id,
            "runId": row.run_id,
            "requestOperationId": row.request_operation_id,
            "contextHash": row.context_hash,
            "caseVersion": row.case_version,
            "status": row.status,
            "proposedAction": row.proposed_action,
            "blockerCodes": list(row.blocker_codes or []),
            "planVersion": row.plan_version,
            "assignmentVersion": row.assignment_version,
            "createdAt": row.created_at,
            "updatedAt": row.updated_at,
        }
    )


class RecoveryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def open_case(
        self,
        run_id: str,
        *,
        reason: str,
        request_operation_id: str | None = None,
        work_package_id: str | None = None,
        emit_outbox: bool = True,
    ) -> RecoveryCase:
        """Fix inspection context and enqueue probe — no adapter I/O here."""
        run = self.session.get(Run, run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise RecoveryError("run_not_found", run_id)

        if request_operation_id:
            prior = self.session.scalar(
                select(RecoveryCaseRow).where(
                    RecoveryCaseRow.request_operation_id == request_operation_id,
                    RecoveryCaseRow.status.in_(
                        ["open", "observing", "awaiting_decision", "applying", "blocked"]
                    ),
                )
            )
            if prior is not None:
                return _to_case(prior)

        instances = list(
            self.session.scalars(
                select(AgentInstanceRow).where(AgentInstanceRow.run_id == run_id)
            ).all()
        )
        attempts = list(
            self.session.scalars(
                select(InstanceAttemptRow).where(InstanceAttemptRow.run_id == run_id)
            ).all()
        )
        sessions = []
        pending_ops = []
        for inst in instances:
            ns = self.session.scalar(
                select(NativeSessionRow).where(
                    NativeSessionRow.instance_id == inst.id,
                    NativeSessionRow.binding_status == "active",
                )
            )
            if ns is not None:
                sessions.append(
                    {
                        "nativeSessionId": ns.id,
                        "instanceId": inst.id,
                        "sessionRef": ns.session_ref,
                        "workspaceId": ns.workspace_id,
                        "bindingStatus": ns.binding_status,
                    }
                )
            for op in self.session.scalars(
                select(InstanceOperationRow).where(
                    InstanceOperationRow.instance_id == inst.id,
                    InstanceOperationRow.status.in_(["prepared", "unknown", "claimed"]),
                )
            ).all():
                pending_ops.append(
                    {
                        "operationId": op.id,
                        "kind": op.kind,
                        "status": op.status,
                        "instanceId": inst.id,
                    }
                )

        context = {
            "runId": run.id,
            "reason": reason,
            "workPackageId": work_package_id,
            "planVersion": run.plan_version,
            "assignmentVersion": run.assignment_version,
            "gatePolicyVersion": run.gate_policy_version,
            "activeGeneration": run.active_generation,
            "statusVersion": run.status_version,
            "runStatus": run.status,
            "instances": [
                {
                    "instanceId": i.id,
                    "roleId": i.role_id,
                    "lifecycle": i.lifecycle,
                    "activity": i.activity,
                    "workspaceId": i.workspace_id,
                    "nativeSessionId": i.native_session_id,
                    "sessionRef": i.session_ref,
                    "generation": i.generation,
                    "currentAttemptId": i.current_attempt_id,
                }
                for i in instances
            ],
            "attempts": [
                {
                    "attemptId": a.id,
                    "workPackageId": a.work_package_id,
                    "instanceId": a.instance_id,
                    "status": a.status,
                    "stageId": a.stage_id,
                    "workspaceId": a.workspace_id,
                    "nativeSessionId": a.native_session_id,
                    "generation": a.generation,
                }
                for a in attempts
            ],
            "nativeSessions": sessions,
            "pendingOperations": pending_ops,
        }
        digest = sha256_hex(context)
        case_id = _id("rcv")
        row = RecoveryCaseRow(
            id=case_id,
            run_id=run.id,
            request_operation_id=request_operation_id,
            context_hash=digest,
            context_json=context,
            case_version=1,
            status="open",
            proposed_action=None,
            blocker_codes=[],
            plan_version=run.plan_version,
            assignment_version=run.assignment_version,
            created_at=_now(self.session),
            updated_at=_now(self.session),
        )
        self.session.add(row)
        self.session.flush()
        append_fact(
            self.session,
            run.id,
            "recovery.started",
            {"caseId": case_id, "status": "open", "action": None, "blockerCodes": []},
        )
        if emit_outbox:
            self.session.add(
                Outbox(
                    id=_id("obx"),
                    run_id=run.id,
                    event_id=None,
                    intent_type="recovery.inspect",
                    target="recovery",
                    payload={
                        "operationId": request_operation_id or case_id,
                        "caseId": case_id,
                        "runId": run.id,
                        "taskVersion": 1,
                    },
                    idempotency_key=f"recovery.inspect:{case_id}",
                    delivery_status="pending",
                    attempt_count=0,
                    created_at=_now(self.session),
                    updated_at=_now(self.session),
                )
            )
        return _to_case(row)

    def get_case(self, case_id: str) -> RecoveryCase:
        row = self.session.get(RecoveryCaseRow, case_id)
        if row is None:
            raise RecoveryError("case_not_found", case_id)
        return _to_case(row)

    def list_cases(self, run_id: str, *, limit: int = 100) -> list[RecoveryCase]:
        run = self.session.get(Run, run_id)
        if run is None:
            raise RecoveryError("run_not_found", run_id)
        rows = self.session.scalars(
            select(RecoveryCaseRow)
            .where(RecoveryCaseRow.run_id == run_id)
            .order_by(RecoveryCaseRow.created_at.desc())
            .limit(min(limit, 100))
        ).all()
        return [_to_case(row) for row in rows]

    def get_report(self, case_id: str) -> RecoveryReport:
        from agentforge.contracts.recovery import RecoveryObservation

        case = self.get_case(case_id)
        obs_rows = self.list_observations(case_id)
        observations = [
            RecoveryObservation.model_validate(
                {
                    "observationId": o.id,
                    "caseId": o.case_id,
                    "subjectType": o.subject_type,
                    "subjectRef": o.subject_ref,
                    "observedAt": o.observed_at,
                    "support": o.support,
                    "resultCode": o.result_code,
                    "evidenceHash": o.evidence_hash,
                    "evidenceRef": o.evidence_ref,
                    "bindingJson": o.binding_json or {},
                    "summary": o.summary,
                }
            )
            for o in obs_rows
        ]
        recommendation = None
        if case.status not in {"applied", "superseded", "canceled"} or observations:
            try:
                recommendation = self.recommend(case_id)
            except RecoveryError:
                recommendation = None
        applied = None
        decision_row = self.session.scalar(
            select(RecoveryDecisionRow)
            .where(RecoveryDecisionRow.case_id == case_id)
            .order_by(RecoveryDecisionRow.created_at.desc())
            .limit(1)
        )
        if decision_row is not None:
            applied = RecoveryDecision.model_validate(
                {
                    "decisionId": decision_row.id,
                    "caseId": decision_row.case_id,
                    "caseVersion": decision_row.case_version,
                    "decisionKey": decision_row.decision_key,
                    "contextHash": decision_row.context_hash,
                    "decision": decision_row.decision,
                    "actor": decision_row.actor,
                    "reason": decision_row.reason,
                    "evidenceRefs": list(decision_row.evidence_refs or []),
                    "createdAt": decision_row.created_at,
                }
            )
        return RecoveryReport(
            case=case,
            observations=observations,
            recommendation=recommendation,
            applied_decision=applied,
        )

    def list_observations(self, case_id: str) -> list[RecoveryObservationRow]:
        return list(
            self.session.scalars(
                select(RecoveryObservationRow).where(RecoveryObservationRow.case_id == case_id)
            ).all()
        )

    def recommend(self, case_id: str) -> RecoveryRecommendation:
        from agentforge.contracts.recovery import (
            NativeObservation,
            OperationObservation,
            RecoveryObservation,
            WorkspaceObservation,
        )

        row = self.session.get(RecoveryCaseRow, case_id)
        if row is None:
            raise RecoveryError("case_not_found", case_id)
        obs_rows = self.list_observations(case_id)
        observations = tuple(
            RecoveryObservation.model_validate(
                {
                    "observationId": o.id,
                    "caseId": o.case_id,
                    "subjectType": o.subject_type,
                    "subjectRef": o.subject_ref,
                    "observedAt": o.observed_at,
                    "support": o.support,
                    "resultCode": o.result_code,
                    "evidenceHash": o.evidence_hash,
                    "evidenceRef": o.evidence_ref,
                    "bindingJson": o.binding_json or {},
                    "summary": o.summary,
                }
            )
            for o in obs_rows
        )
        native = None
        workspace = None
        operations: list[OperationObservation] = []
        for o in observations:
            binding = o.binding_json or {}
            if o.subject_type == "native_session" and binding.get("nativeObservation"):
                native = NativeObservation.model_validate(binding["nativeObservation"])
            if o.subject_type == "workspace" and binding.get("workspaceObservation"):
                workspace = WorkspaceObservation.model_validate(binding["workspaceObservation"])
            if o.subject_type == "instance_operation" and binding.get("operationObservation"):
                operations.append(
                    OperationObservation.model_validate(binding["operationObservation"])
                )
        ctx_json = row.context_json or {}
        attempt_status = None
        for a in ctx_json.get("attempts") or []:
            if isinstance(a, dict) and a.get("status") == "unknown":
                attempt_status = "unknown"
                break
            if isinstance(a, dict) and a.get("status") in {
                "submitted",
                "awaiting_stop_verification",
                "stopped",
            }:
                attempt_status = a.get("status")
        context = RecoveryContext(
            run_id=row.run_id,
            case_id=row.id,
            context_hash=row.context_hash,
            plan_version=row.plan_version,
            assignment_version=row.assignment_version,
            generation=int(ctx_json.get("activeGeneration") or 1),
            status_version=int(ctx_json.get("statusVersion") or 1),
            reason=ctx_json.get("reason"),
            work_package_id=ctx_json.get("workPackageId"),
            extras={"attemptStatus": attempt_status},
        )
        return decide_recovery(
            context,
            observations,
            native=native,
            workspace=workspace,
            operations=tuple(operations),
        )

    def apply_decision(
        self,
        case_id: str,
        body: RecoveryDecisionRequest,
        *,
        actor: str,
    ) -> OperationAccepted:
        """Re-check caseVersion/contextHash/statusVersion before applying."""
        case = self.session.get(
            RecoveryCaseRow, case_id, with_for_update=True, populate_existing=True
        )
        if case is None:
            raise RecoveryError("case_not_found", case_id)
        run = self.session.get(Run, case.run_id, with_for_update=True, populate_existing=True)
        if run is None:
            raise RecoveryError("run_not_found", case.run_id)

        if int(body.case_version) != int(case.case_version):
            raise RecoveryError("recovery_evidence_stale", "caseVersion mismatch")
        if body.context_hash != case.context_hash:
            raise RecoveryError("context_changed", "contextHash mismatch")
        expected_sv = int(body.expected_status_version)
        if expected_sv != int(run.status_version):
            raise RecoveryError("status_version_conflict", "Run statusVersion mismatch")
        if case.status in {"applied", "superseded", "canceled"}:
            raise RecoveryError("illegal_status", f"case already {case.status}")

        prior = self.session.scalar(
            select(RecoveryDecisionRow).where(
                RecoveryDecisionRow.case_id == case_id,
                RecoveryDecisionRow.decision_key == body.decision_key,
            )
        )
        if prior is not None:
            if prior.decision != body.decision or prior.context_hash != body.context_hash:
                raise RecoveryError(
                    "idempotency_conflict", "decision key reused with different body"
                )
            return OperationAccepted(operation_id=prior.id, status="accepted", run_id=run.id)

        recommendation = self.recommend(case_id)
        if body.decision != recommendation.action:
            raise RecoveryError(
                "decision_mismatch",
                "non-user actor may only apply the current recommendation",
            )
        if body.decision not in AUTO_APPLY_ACTIONS and not actor.startswith("user:"):
            raise RecoveryError(
                "handoff_confirmation_required",
                "this decision requires an explicit user actor",
            )
        if recommendation.requires_user_decision and not actor.startswith("user:"):
            raise RecoveryError(
                "handoff_confirmation_required",
                "recommendation requires user decision",
            )

        decision_id = _id("rcd")
        self.session.add(
            RecoveryDecisionRow(
                id=decision_id,
                case_id=case.id,
                case_version=case.case_version,
                decision_key=body.decision_key,
                context_hash=body.context_hash,
                decision=body.decision,
                actor=actor,
                reason=body.reason,
                evidence_refs=list(body.evidence_refs or []),
                created_at=_now(self.session),
            )
        )
        if body.decision in {
            "await_reconciliation",
            "await_stop_verification",
            "wait_authorization",
            "block_or_rework",
        }:
            case.status = "blocked"
        else:
            case.status = "applying"
        self.session.add(
            Outbox(
                id=_id("obx"),
                run_id=run.id,
                intent_type="recovery.apply",
                target="recovery",
                payload={"operationId": decision_id, "caseId": case.id},
                idempotency_key=f"recovery.apply:{decision_id}",
                delivery_status="pending",
                attempt_count=0,
            )
        )
        case.proposed_action = body.decision
        case.case_version = int(case.case_version) + 1
        case.updated_at = _now(self.session)
        append_fact(
            self.session,
            run.id,
            "recovery.observed",
            {
                "caseId": case.id,
                "status": case.status,
                "action": body.decision,
                "blockerCodes": list(recommendation.reason_codes),
            },
        )
        # observe_existing / continue_stage_processing do not invent stop or expand auth.
        return OperationAccepted(operation_id=decision_id, status="accepted", run_id=run.id)

    def record_observation(
        self,
        case_id: str,
        *,
        subject_type: str,
        subject_ref: str,
        support: str,
        result_code: str,
        evidence_hash: str,
        evidence_ref: str | None = None,
        binding_json: dict[str, Any] | None = None,
        summary: str | None = None,
    ) -> RecoveryObservationRow:
        case = self.session.get(RecoveryCaseRow, case_id, with_for_update=True)
        if case is None:
            raise RecoveryError("case_not_found", case_id)
        row = RecoveryObservationRow(
            id=_id("rob"),
            case_id=case_id,
            subject_type=subject_type,
            subject_ref=subject_ref,
            observed_at=_now(self.session),
            support=support,
            result_code=result_code,
            evidence_hash=evidence_hash,
            evidence_ref=evidence_ref,
            binding_json=dict(binding_json or {}),
            summary=summary,
            created_at=_now(self.session),
        )
        self.session.add(row)
        if case.status == "open":
            case.status = "observing"
        case.updated_at = _now(self.session)
        return row

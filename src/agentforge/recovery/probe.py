"""Transaction-external recovery probes — claim → inspect → save observations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agentforge.adapters.registry import get_adapter
from agentforge.contracts.recovery import (
    NativeObservation,
    OperationObservation,
    RecoveryReport,
    WorkspaceObservation,
)
from agentforge.core.config import Settings
from agentforge.driver.binding import sha256_hex
from agentforge.driver.signals import record_wake_signal
from agentforge.recovery.errors import RecoveryError
from agentforge.recovery.normalize import normalize_native_observation
from agentforge.recovery.service import RecoveryService, _to_case
from agentforge.storage.event_log import append_fact
from agentforge.storage.models import (
    AgentInstanceRow,
    AgentProfileRow,
    DirectoryGrantRow,
    GitWorkspaceRow,
    InstanceOperationRow,
    Project,
    RecoveryCaseRow,
    Run,
)
from agentforge.storage.uow import db_now, unit_of_work_connection


def _now(session: Session | None = None) -> datetime:
    if session is not None:
        try:
            value = db_now(session)
            if getattr(value, "tzinfo", None) is None:
                return value.replace(tzinfo=UTC)  # type: ignore[union-attr]
            return value  # type: ignore[return-value]
        except Exception:
            pass
    return datetime.now(UTC)


def inspect_instance(
    adapter: Any,
    *,
    session_ref: str,
    instance_id: str,
    attempt_id: str | None,
    generation: int | None,
    workspace_id: str | None,
    native_session_id: str | None,
) -> NativeObservation:
    try:
        raw = adapter.inspect_execution(session_ref=session_ref)
    except Exception as exc:
        raw = {
            "support": "unknown",
            "detail": f"inspect raised: {type(exc).__name__}",
            "running": None,
            "childrenRunning": None,
            "writesQuiesced": None,
        }
    if not isinstance(raw, dict):
        raw = {"support": "unknown"}
    return normalize_native_observation(
        raw,
        instance_id=instance_id,
        attempt_id=attempt_id,
        generation=generation,
        workspace_id=workspace_id,
        native_session_id=native_session_id,
        expected_session_ref=session_ref,
    )


def inspect_workspace(
    session: Session,
    *,
    workspace_id: str,
    project_id: str,
) -> WorkspaceObservation:
    ws = session.get(GitWorkspaceRow, workspace_id)
    observed_at = _now(session)
    if ws is None:
        return WorkspaceObservation.model_validate(
            {
                "workspaceId": workspace_id,
                "support": "unknown",
                "identityMatched": None,
                "observedAt": observed_at,
            }
        )
    project = session.get(Project, project_id)
    grant = (
        session.get(DirectoryGrantRow, project.directory_grant_id) if project is not None else None
    )
    path = Path(ws.worktree_path)
    exists = path.exists()
    identity_matched: bool | None
    support: str
    if not exists:
        identity_matched = False
        support = "supported"
    elif grant is None or grant.status != "active":
        identity_matched = None
        support = "unknown"
    else:
        # Path under grant root is necessary but not sufficient alone.
        try:
            resolved = path.resolve()
            root = Path(grant.normalized_root).resolve()
            under = str(resolved).startswith(str(root))
        except Exception:
            under = False
        identity_matched = bool(under and ws.project_id == project_id)
        support = "supported"
    return WorkspaceObservation.model_validate(
        {
            "workspaceId": workspace_id,
            "expectedPath": ws.worktree_path,
            "actualPath": str(path) if exists else None,
            "identityMatched": identity_matched,
            "gitCommonDir": None,
            "headRef": ws.head_commit,
            "baselineRef": ws.baseline_id,
            "support": support,
            "observedAt": observed_at,
        }
    )


def inspect_pending_operations(
    session: Session, *, instance_ids: list[str]
) -> list[OperationObservation]:
    if not instance_ids:
        return []
    rows = list(
        session.scalars(
            select(InstanceOperationRow).where(
                InstanceOperationRow.instance_id.in_(instance_ids),
                InstanceOperationRow.status.in_(["prepared", "unknown", "claimed"]),
            )
        ).all()
    )
    out: list[OperationObservation] = []
    for op in rows:
        possibly = op.status in {"unknown", "claimed"} or (
            op.status == "prepared" and bool((op.payload_json or {}).get("dispatchStartedAt"))
        )
        out.append(
            OperationObservation.model_validate(
                {
                    "operationId": op.id,
                    "kind": op.kind,
                    "ledgerStatus": op.status,
                    "nativeRef": op.native_ref,
                    "possiblyExecuted": possibly,
                    "support": "unknown" if possibly else "supported",
                    "observedAt": _now(session),
                }
            )
        )
    return out


def probe_case(engine: Engine, settings: Settings, case_id: str) -> dict[str, Any]:
    """Claim case → probe outside txn → persist immutable observations + wake."""
    with unit_of_work_connection(engine) as uow:
        case = uow.session.get(
            RecoveryCaseRow, case_id, with_for_update=True, populate_existing=True
        )
        if case is None:
            raise RecoveryError("case_not_found", case_id)
        if case.status in {"applied", "superseded", "canceled"}:
            return {"status": case.status, "caseId": case_id}
        case.status = "observing"
        case.updated_at = _now(uow.session)
        context = dict(case.context_json or {})
        run = uow.session.get(Run, case.run_id)
        project_id = run.project_id if run else None

    # --- outside transaction ---
    native_results: list[NativeObservation] = []
    workspace_results: list[WorkspaceObservation] = []
    operation_results: list[OperationObservation] = []

    with unit_of_work_connection(engine) as uow:
        session = uow.session
        instance_ids = [
            str(i["instanceId"])
            for i in (context.get("instances") or [])
            if isinstance(i, dict) and i.get("instanceId")
        ]
        operation_results = inspect_pending_operations(session, instance_ids=instance_ids)

        for inst_data in context.get("instances") or []:
            if not isinstance(inst_data, dict):
                continue
            instance_id = str(inst_data.get("instanceId"))
            session_ref = inst_data.get("sessionRef")
            workspace_id = inst_data.get("workspaceId")
            if workspace_id and project_id:
                workspace_results.append(
                    inspect_workspace(
                        session, workspace_id=str(workspace_id), project_id=project_id
                    )
                )
            if not session_ref:
                native_results.append(
                    NativeObservation.model_validate(
                        {
                            "instanceId": instance_id,
                            "attemptId": inst_data.get("currentAttemptId"),
                            "generation": inst_data.get("generation"),
                            "workspaceId": workspace_id,
                            "nativeSessionId": inst_data.get("nativeSessionId"),
                            "running": None,
                            "childrenRunning": None,
                            "writesQuiesced": None,
                            "observedAt": _now(session),
                            "support": "unknown",
                        }
                    )
                )
                continue
            instance = session.get(AgentInstanceRow, instance_id)
            if instance is None:
                continue
            profile = session.get(AgentProfileRow, instance.agent_profile_id)
            if profile is None:
                continue
            adapter = get_adapter(profile.adapter_id)
            native_results.append(
                inspect_instance(
                    adapter,
                    session_ref=str(session_ref),
                    instance_id=instance_id,
                    attempt_id=inst_data.get("currentAttemptId"),
                    generation=inst_data.get("generation"),
                    workspace_id=workspace_id,
                    native_session_id=inst_data.get("nativeSessionId"),
                )
            )

    # --- persist observations ---
    with unit_of_work_connection(engine) as uow:
        svc = RecoveryService(uow.session)
        case = uow.session.get(
            RecoveryCaseRow, case_id, with_for_update=True, populate_existing=True
        )
        if case is None:
            raise RecoveryError("case_not_found", case_id)

        for native in native_results:
            payload = native.model_dump(mode="json", by_alias=True)
            svc.record_observation(
                case_id,
                subject_type="native_session",
                subject_ref=native.native_session_id or native.instance_id,
                support=native.support,
                result_code="native_probe",
                evidence_hash=sha256_hex(payload),
                evidence_ref=native.evidence_ref,
                binding_json={"nativeObservation": payload},
                summary=f"support={native.support}",
            )
        for ws in workspace_results:
            payload = ws.model_dump(mode="json", by_alias=True)
            svc.record_observation(
                case_id,
                subject_type="workspace",
                subject_ref=ws.workspace_id,
                support=ws.support,
                result_code="workspace_probe",
                evidence_hash=sha256_hex(payload),
                evidence_ref=ws.evidence_ref,
                binding_json={"workspaceObservation": payload},
                summary=f"matched={ws.identity_matched}",
            )
        for op in operation_results:
            payload = op.model_dump(mode="json", by_alias=True)
            svc.record_observation(
                case_id,
                subject_type="instance_operation",
                subject_ref=op.operation_id,
                support=op.support,
                result_code=op.ledger_status,
                evidence_hash=sha256_hex(payload),
                evidence_ref=op.evidence_ref,
                binding_json={"operationObservation": payload},
                summary=f"possiblyExecuted={op.possibly_executed}",
            )

        recommendation = svc.recommend(case_id)
        case.proposed_action = recommendation.action
        case.blocker_codes = list(recommendation.reason_codes)
        if recommendation.requires_user_decision:
            case.status = "awaiting_decision"
        elif recommendation.action in {
            "await_reconciliation",
            "await_stop_verification",
            "wait_authorization",
        }:
            case.status = "blocked"
        else:
            case.status = "observing"
        case.updated_at = _now(uow.session)

        append_fact(
            uow.session,
            case.run_id,
            "recovery.observed"
            if case.status != "blocked"
            else "recovery.blocked",
            {
                "caseId": case.id,
                "status": case.status,
                "action": recommendation.action,
                "blockerCodes": list(recommendation.reason_codes),
            },
        )
        record_wake_signal(
            uow.session,
            run_id=case.run_id,
            source_kind="recovery",
            source_ref=f"{case.id}:{case.case_version}:{case.status}",
            binding={"caseId": case.id, "action": recommendation.action},
            priority=50,
        )
        return {
            "status": case.status,
            "caseId": case.id,
            "action": recommendation.action,
            "requiresUserDecision": recommendation.requires_user_decision,
        }


def get_report(session: Session, case_id: str) -> RecoveryReport:
    svc = RecoveryService(session)
    case = session.get(RecoveryCaseRow, case_id)
    if case is None:
        raise RecoveryError("case_not_found", case_id)
    from agentforge.contracts.recovery import RecoveryObservation

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
        for o in svc.list_observations(case_id)
    ]
    return RecoveryReport(
        case=_to_case(case),
        observations=observations,
        recommendation=svc.recommend(case_id),
        applied_decision=None,
    )

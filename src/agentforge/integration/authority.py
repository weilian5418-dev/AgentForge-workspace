"""Current approved binding and released input checks shared by all delivery writes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.driver.binding import sha256_hex
from agentforge.integration.selection import resolve_released_inputs
from agentforge.storage.models import (
    AgentInstanceRow,
    InstanceAttemptRow,
    IntegrationSelectionRow,
    Plan,
    Run,
)


class DeliveryAuthorityError(ValueError):
    pass


def assert_selection_current(
    session: Session, run: Run, selection: IntegrationSelectionRow | None
) -> Plan:
    if selection is None or selection.run_id != run.id or selection.baseline_ref != run.baseline_id:
        raise DeliveryAuthorityError("selection_stale")
    from agentforge.integration.selection import _load_handoff_op

    handoff = _load_handoff_op(session, run_id=run.id, handoff_ref=selection.handoff_ref)
    if (
        handoff is None
        or handoff.run_id != run.id
        or handoff.kind != "integration_handoff"
        or handoff.status != "succeeded"
    ):
        raise DeliveryAuthorityError("handoff_invalid")
    binding = handoff.binding_json or {}
    for key, value in [
        ("planVersion", run.plan_version),
        ("assignmentVersion", run.assignment_version),
        ("gatePolicyVersion", run.gate_policy_version),
        ("executionGeneration", run.active_generation),
    ]:
        if binding.get(key) != value:
            raise DeliveryAuthorityError("selection_stale")
    if (
        handoff.request_hash != selection.handoff_hash
        or sha256_hex({k: v for k, v in binding.items() if k not in {"handoffId", "handoffHash"}})
        != handoff.request_hash
    ):
        raise DeliveryAuthorityError("handoff_hash_mismatch")
    plan = session.get(Plan, (run.id, run.plan_version))
    if (
        plan is None
        or plan.status != "approved"
        or binding.get("contractHash") != plan.contract_hash
    ):
        raise DeliveryAuthorityError("plan_not_confirmed")
    current = resolve_released_inputs(
        session, run_id=run.id, release_record_ids=list(binding.get("releaseRecordIds") or [])
    )
    if current != selection.source_bindings:
        raise DeliveryAuthorityError("release_scope_changed")
    return plan


def assert_run_deliverable(session: Session, run: Run) -> None:
    if (
        run.status not in {"running", "awaiting_acceptance", "blocked"}
        or run.collaboration_state != "ready_for_integration"
    ):
        raise DeliveryAuthorityError("run_not_deliverable")
    active = session.scalar(
        select(InstanceAttemptRow).where(
            InstanceAttemptRow.run_id == run.id,
            InstanceAttemptRow.status.in_(
                [
                    "scheduled",
                    "dispatching",
                    "running",
                    "submitted",
                    "awaiting_stop_verification",
                    "unknown",
                ]
            ),
        )
    )
    if active:
        raise DeliveryAuthorityError("pending_execution")
    from agentforge.storage.models import (
        InstanceOperationRow,
    )

    instances = list(
        session.scalars(select(AgentInstanceRow).where(AgentInstanceRow.run_id == run.id)).all()
    )
    for instance in instances:
        if instance.activity == "busy" or instance.lifecycle in {
            "starting",
            "stop_requested",
            "unknown",
            "needs_reconciliation",
        }:
            raise DeliveryAuthorityError("pending_execution")
        if session.scalar(
            select(InstanceOperationRow).where(
                InstanceOperationRow.instance_id == instance.id,
                InstanceOperationRow.status.in_(
                    ["prepared", "claimed", "native_invoked", "unknown", "needs_reconciliation"]
                ),
            )
        ):
            raise DeliveryAuthorityError("pending_side_effect")
    return None

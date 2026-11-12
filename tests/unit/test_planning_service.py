"""D08.5: planning service confirmation protocol (unit / mem session)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agentforge.contracts.agents import RoleAssignmentPutItem
from agentforge.contracts.plans import PlanDecisionRequest, PlanProposalRequest
from agentforge.contracts.runs import RunCreateRequest
from agentforge.planning.service import PlanningError, PlanningService
from agentforge.storage.models import (
    DirectoryGrantRow,
    Plan,
    PlanConfirmationRequestRow,
    PlanDecisionRow,
    PlanProposalRow,
    PlanningContextRow,
    Project,
    ProjectBaselineRow,
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
        if "plan_decisions" in text:
            for row in self.added:
                if isinstance(row, PlanDecisionRow):
                    return row
            return None
        if "plan_confirmation_requests" in text and "pending" in text:
            for row in reversed(self.added):
                if isinstance(row, PlanConfirmationRequestRow) and row.status == "pending":
                    return row
            return None
        if "from runs" in text or ".id =" in text:
            # idempotency / lock / get patterns — return matching run if any
            for row in self.added:
                if isinstance(row, Run):
                    if "idempotency" in text and row.idempotency_key:
                        return row
            return None
        if "plans" in text:
            plans = [r for r in self.added if isinstance(r, Plan)]
            if not plans:
                return None
            return sorted(plans, key=lambda p: p.version, reverse=True)[0]
        return None

    def scalars(self, stmt: Any) -> _Result:
        text = str(stmt).lower()
        if "role_assignments" in text:
            return _Result([r for r in self.added if isinstance(r, RoleAssignmentRow)])
        if "plans" in text:
            return _Result([r for r in self.added if isinstance(r, Plan)])
        return _Result([])


def _seed(session: _MemSession) -> None:
    now = datetime.now(UTC)
    session.add(
        DirectoryGrantRow(
            id="gr_1",
            authorized_user="local",
            environment_id="env_local",
            normalized_root="D:/proj",
            directory_identity="dir_1",
            allowed_operations=["read"],
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
        ProjectBaselineRow(
            id="bl_1",
            project_id="prj_1",
            environment_id="env_local",
            dirty_policy="committed_only",
            status="ready",
            is_git=False,
            git_commit=None,
            git_dirty_summary={},
            manifest_hash=None,
            scope_strategy="authorized_root",
            notes=[],
            checked_at=now,
            created_at=now,
        )
    )


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> tuple[PlanningService, _MemSession]:
    session = _MemSession()
    _seed(session)
    monkeypatch.setattr(
        "agentforge.planning.service.require_active_grant",
        lambda _s, grant_id: session.get(DirectoryGrantRow, grant_id),
    )
    monkeypatch.setattr(
        "agentforge.planning.context.require_active_grant",
        lambda _s, grant_id: session.get(DirectoryGrantRow, grant_id),
    )
    return PlanningService(session), session  # type: ignore[arg-type]


def test_create_propose_assign_approve_flow(svc: tuple[PlanningService, _MemSession]) -> None:
    service, _session = svc
    accepted = service.create_run(
        RunCreateRequest.model_validate(
            {
                "projectId": "prj_1",
                "goal": "ship notes",
                "outputs": ["markdown"],
                "budget": {"maxTokens": 1000},
                "idempotencyKey": "idem-1",
            }
        )
    )
    again = service.create_run(
        RunCreateRequest.model_validate(
            {
                "projectId": "prj_1",
                "goal": "ship notes",
                "outputs": ["markdown"],
                "budget": {"maxTokens": 1000},
                "idempotencyKey": "idem-1",
            }
        )
    )
    assert again.run_id == accepted.run_id

    plan = service.propose(
        accepted.run_id,
        PlanProposalRequest.model_validate({"source": "rules"}),
    )
    assert plan.compilation is not None
    assert plan.compilation.status == "valid"
    assert plan.status == "proposed"

    run = service.get_run(accepted.run_id)
    items = service.put_assignments(
        accepted.run_id,
        plan_version=plan.plan_version,
        items=[RoleAssignmentPutItem.model_validate({"roleId": "role_writer", "agentProfileId": "aprof_1"})],
    )
    assert len(items) == 1
    plan2 = service.get_plan(accepted.run_id)
    assert plan2.confirmation is not None
    assert plan2.confirmation.status == "pending"

    run = service.get_run(accepted.run_id)
    decided = service.decide(
        accepted.run_id,
        PlanDecisionRequest.model_validate(
            {
                "decision": "approve",
                "confirmationRequestId": plan2.confirmation.request_id,
                "planVersion": plan2.plan_version,
                "planHash": plan2.plan_hash,
                "assignmentVersion": run.assignment_version,
                "gatePolicyVersion": plan2.gate_policy_version,
                "statusVersion": run.status_version,
            }
        ),
    )
    assert decided.status == "approved"
    assert service.get_run(accepted.run_id).status == "preparing"

    # Idempotent same decision
    again_decision = service.decide(
        accepted.run_id,
        PlanDecisionRequest.model_validate(
            {
                "decision": "approve",
                "confirmationRequestId": plan2.confirmation.request_id,
                "planVersion": plan2.plan_version,
                "planHash": plan2.plan_hash,
                "assignmentVersion": run.assignment_version,
                "gatePolicyVersion": plan2.gate_policy_version,
                "statusVersion": run.status_version,
            }
        ),
    )
    assert again_decision.status == "approved"


def test_conflicting_decision_rejected(svc: tuple[PlanningService, _MemSession]) -> None:
    service, session = svc
    accepted = service.create_run(
        RunCreateRequest.model_validate(
            {
                "projectId": "prj_1",
                "goal": "ship notes",
                "outputs": ["markdown"],
                "budget": {},
            }
        )
    )
    plan = service.propose(accepted.run_id, PlanProposalRequest.model_validate({"source": "rules"}))
    service.put_assignments(
        accepted.run_id,
        plan_version=plan.plan_version,
        items=[RoleAssignmentPutItem.model_validate({"roleId": "role_writer", "agentProfileId": "aprof_1"})],
    )
    plan2 = service.get_plan(accepted.run_id)
    run = service.get_run(accepted.run_id)
    body = {
        "decision": "approve",
        "confirmationRequestId": plan2.confirmation.request_id,
        "planVersion": plan2.plan_version,
        "planHash": plan2.plan_hash,
        "assignmentVersion": run.assignment_version,
        "gatePolicyVersion": plan2.gate_policy_version,
        "statusVersion": run.status_version,
    }
    service.decide(accepted.run_id, PlanDecisionRequest.model_validate(body))
    # Force a conflicting stored decision path: add opposite decision manually then call
    # with reject against same request should conflict via existing approve.
    with pytest.raises(PlanningError) as exc:
        service.decide(
            accepted.run_id,
            PlanDecisionRequest.model_validate({**body, "decision": "reject"}),
        )
    assert exc.value.code in {"decision_conflict", "confirmation_not_pending", "status_version_conflict"}

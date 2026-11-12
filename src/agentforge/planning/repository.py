"""Plan persistence helpers — no transaction ownership."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.storage.models import (
    Plan,
    PlanConfirmationRequestRow,
    PlanDecisionRow,
    PlanProposalRow,
    PlanningContextRow,
    RoleAssignmentRow,
    Run,
)


def _now() -> datetime:
    return datetime.now(UTC)


class PlanRepository:
    """Data access only. Callers own UoW / short transactions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_run(self, run_id: str) -> Run | None:
        return self.session.get(Run, run_id)

    def get_run_by_idempotency(self, project_id: str, key: str) -> Run | None:
        row: Run | None = None
        try:
            row = self.session.scalar(
                select(Run).where(Run.project_id == project_id, Run.idempotency_key == key)
            )
        except Exception:
            row = None
        if row is not None:
            return row
        # MemSession / limited dialects: scan loaded runs.
        for obj in getattr(self.session, "added", []):
            if (
                isinstance(obj, Run)
                and obj.project_id == project_id
                and obj.idempotency_key == key
            ):
                return obj
        return None

    def add_run(self, row: Run) -> Run:
        self.session.add(row)
        return row

    def get_plan(self, run_id: str, version: int) -> Plan | None:
        return self.session.get(Plan, (run_id, version))

    def latest_plan(self, run_id: str) -> Plan | None:
        return self.session.scalar(
            select(Plan)
            .where(Plan.run_id == run_id)
            .order_by(Plan.version.desc())
            .limit(1)
        )

    def add_plan(self, row: Plan) -> Plan:
        self.session.add(row)
        return row

    def list_plans(self, run_id: str) -> list[Plan]:
        return list(
            self.session.scalars(
                select(Plan).where(Plan.run_id == run_id).order_by(Plan.version.asc())
            ).all()
        )

    def add_context(self, row: PlanningContextRow) -> PlanningContextRow:
        self.session.add(row)
        return row

    def get_context(self, context_id: str) -> PlanningContextRow | None:
        return self.session.get(PlanningContextRow, context_id)

    def add_proposal(self, row: PlanProposalRow) -> PlanProposalRow:
        self.session.add(row)
        return row

    def get_proposal(self, proposal_id: str) -> PlanProposalRow | None:
        return self.session.get(PlanProposalRow, proposal_id)

    def add_confirmation_request(
        self, row: PlanConfirmationRequestRow
    ) -> PlanConfirmationRequestRow:
        self.session.add(row)
        return row

    def get_confirmation_request(
        self, request_id: str
    ) -> PlanConfirmationRequestRow | None:
        return self.session.get(PlanConfirmationRequestRow, request_id)

    def pending_confirmation_for_run(
        self, run_id: str
    ) -> PlanConfirmationRequestRow | None:
        return self.session.scalar(
            select(PlanConfirmationRequestRow).where(
                PlanConfirmationRequestRow.run_id == run_id,
                PlanConfirmationRequestRow.status == "pending",
            )
        )

    def add_decision(self, row: PlanDecisionRow) -> PlanDecisionRow:
        self.session.add(row)
        return row

    def get_decision_for_request(self, request_id: str) -> PlanDecisionRow | None:
        return self.session.scalar(
            select(PlanDecisionRow).where(PlanDecisionRow.request_id == request_id)
        )

    def list_assignments(
        self, run_id: str, assignment_version: int
    ) -> list[RoleAssignmentRow]:
        return list(
            self.session.scalars(
                select(RoleAssignmentRow).where(
                    RoleAssignmentRow.run_id == run_id,
                    RoleAssignmentRow.assignment_version == assignment_version,
                )
            ).all()
        )

    def add_assignment(self, row: RoleAssignmentRow) -> RoleAssignmentRow:
        self.session.add(row)
        return row

    def lock_run(self, run_id: str) -> Run | None:
        """Row lock when the dialect supports FOR UPDATE; MemSession falls back to get."""
        try:
            row = self.session.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if row is not None:
                return row
        except Exception:
            pass
        return self.session.get(Run, run_id)


def plan_row_from_spec(
    *,
    run_id: str,
    version: int,
    plan_hash: str,
    contract_hash: str,
    status: str,
    summary: str,
    spec: dict[str, Any],
    compilation: dict[str, Any] | None,
    context_id: str | None,
    baseline_id: str | None,
    input_ids: list[str],
    gate_policy_version: int,
    supersedes_version: int | None = None,
) -> Plan:
    return Plan(
        run_id=run_id,
        version=version,
        contract_hash=contract_hash,
        plan_hash=plan_hash,
        status=status,
        summary=summary,
        spec_json=spec,
        compilation_json=compilation,
        context_id=context_id,
        baseline_id=baseline_id,
        input_ids=list(input_ids),
        gate_policy_version=gate_policy_version,
        supersedes_version=supersedes_version,
        hash_algo_version="plan-hash-v1",
        created_at=_now(),
    )

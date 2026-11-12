"""D08 confirmation with real bound capability objects and optimistic scope."""

from datetime import UTC, datetime, timedelta

import pytest

from agentforge.contracts.agents import RoleAssignmentPutItem
from agentforge.contracts.plans import PlanDecisionRequest
from agentforge.contracts.runs import RunCreateRequest
from agentforge.planning.service import PlanningError, PlanningService
from agentforge.storage import models as m
from tests.support.collaboration import seed


@pytest.fixture
def setup(tmp_path, monkeypatch):
    s, spec = seed(tmp_path)
    for module in [
        "agentforge.planning.service",
        "agentforge.planning.context",
        "agentforge.projects.grants",
    ]:
        monkeypatch.setattr(
            module + ".require_active_grant",
            lambda session, grant_id: session.get(m.DirectoryGrantRow, grant_id),
        )
    return s, spec


def create(s, **updates):
    data = {
        "projectId": "project",
        "goal": "Write an integrity checked document",
        "outputs": ["markdown"],
        "budget": {"maxTokens": 1000},
        "idempotencyKey": "once",
    }
    data.update(updates)
    return PlanningService(s).create_run(RunCreateRequest.model_validate(data))


def draft(s, spec):
    run = s.get(m.Run, "run")
    run.status = "awaiting_confirmation"
    plan = s.get(m.Plan, ("run", 1))
    plan.status = "proposed"
    plan.compilation_json = {
        "status": "valid",
        "compilerVersion": "test",
        "issues": [],
        "warnings": [],
    }
    return PlanningService(s)


def confirm(service, s, spec):
    role = spec.roles[0]
    service.put_assignments(
        "run",
        plan_version=1,
        items=[
            RoleAssignmentPutItem(roleId=role.role_id, agentProfileId="profile-" + role.role_id)
        ],
    )
    run = s.get(m.Run, "run")
    req = s.get(m.PlanConfirmationRequestRow, run.current_confirmation_request_id)
    return PlanDecisionRequest.model_validate(
        {
            "decision": "approve",
            "confirmationRequestId": req.id,
            "planVersion": 1,
            "planHash": req.plan_hash,
            "assignmentVersion": req.assignment_version,
            "gatePolicyVersion": req.gate_policy_version,
            "statusVersion": run.status_version,
        }
    )


def test_same_key_same_payload_returns_original(setup):
    s, _ = setup
    assert create(s).run_id == create(s).run_id


def test_same_key_different_payload_conflicts(setup):
    s, _ = setup
    create(s)
    with pytest.raises(PlanningError, match="different request"):
        create(s, goal="Different goal")


def test_nonexistent_agent_never_confirmed(setup):
    s, spec = setup
    service = draft(s, spec)
    with pytest.raises(PlanningError, match="does not exist"):
        service.put_assignments(
            "run",
            plan_version=1,
            items=[RoleAssignmentPutItem(roleId=spec.roles[0].role_id, agentProfileId="missing")],
        )


@pytest.mark.parametrize("mutation", ["expired", "unknown", "config_changed"])
def test_assignment_checks_versioned_capability(setup, mutation):
    s, spec = setup
    service = draft(s, spec)
    check = next(r for r in s.added if isinstance(r, m.CapabilityCheckRow))
    profile = next(r for r in s.added if isinstance(r, m.AgentProfileRow))
    if mutation == "expired":
        check.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    if mutation == "unknown":
        check.results = [{**r, "support": "unknown"} for r in check.results]
    if mutation == "config_changed":
        profile.config_hash = "f" * 64
    with pytest.raises(PlanningError):
        confirm(service, s, spec)


def test_confirm_binds_current_capabilities_and_keeps_execution_separate(setup):
    s, spec = setup
    service = draft(s, spec)
    request = confirm(service, s, spec)
    result = service.decide("run", request)
    assert result.status == "approved"
    assert s.get(m.Run, "run").status == "preparing"
    assert not [r for r in s.added if isinstance(r, m.AgentInstanceRow)]


def test_profile_mutation_after_assignment_blocks_confirmation(setup):
    s, spec = setup
    service = draft(s, spec)
    request = confirm(service, s, spec)
    profile = next(r for r in s.added if isinstance(r, m.AgentProfileRow))
    profile.config_version = "2"
    with pytest.raises(PlanningError, match="changed since confirmation"):
        service.decide("run", request)


def test_confirmation_of_stale_assignment_rejected(setup):
    s, spec = setup
    service = draft(s, spec)
    request = confirm(service, s, spec)
    s.get(m.Run, "run").assignment_version += 1
    with pytest.raises(PlanningError, match="not current"):
        service.decide("run", request)

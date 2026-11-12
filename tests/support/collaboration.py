"""Deterministic unit-test repository. No production fallback or concurrency claim."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    False_,
    Null,
    True_,
)

from agentforge.storage import models as m


def value(expr, row):
    if isinstance(expr, BindParameter):
        return expr.value
    if isinstance(expr, Null):
        return None
    if isinstance(expr, True_):
        return True
    if isinstance(expr, False_):
        return False
    if isinstance(expr, BooleanClauseList):
        values = [value(x, row) for x in expr.clauses]
        return all(values) if expr.operator is operators.and_ else any(values)
    if isinstance(expr, BinaryExpression):
        a, b = value(expr.left, row), value(expr.right, row)
        op = expr.operator
        if op is operators.eq:
            return a == b
        if op is operators.ne:
            return a != b
        if op is operators.in_op:
            return a in b
        if op is operators.not_in_op:
            return a not in b
        if op is operators.is_:
            return a is b
        if op is operators.is_not:
            return a is not b
        if op is operators.le:
            return a is not None and a <= b
        if op is operators.lt:
            return a is not None and a < b
        if op is operators.ge:
            return a is not None and a >= b
        if op is operators.gt:
            return a is not None and a > b
        raise AssertionError(f"Unhandled test operator {op}")
    return getattr(row, expr.key, None)


class Ledger:
    def __init__(self):
        self.rows = {}
        self.added = []
        self.active = 0
        self.commits = 0

    def add(self, row):
        key = tuple(getattr(row, c.name) for c in row.__table__.primary_key)
        for c in row.__table__.columns:
            if getattr(row, c.name, None) is None and not c.nullable:
                if c.name.endswith("_at"):
                    setattr(row, c.name, datetime.now(UTC))
                elif c.name in {"last_sequence", "repair_round", "retry_count", "attempt_count"}:
                    setattr(row, c.name, 0)
                elif c.name in {"concurrency_version", "status_version", "active_generation"}:
                    setattr(row, c.name, 1)
        self.rows[(type(row), key[0] if len(key) == 1 else key)] = row
        if row not in self.added:
            self.added.append(row)

    def get(self, model, key, **kwargs):
        return self.rows.get((model, key))

    def flush(self):
        pass

    def refresh(self, row):
        pass

    def delete(self, row):
        self.added.remove(row)
        for key, r in list(self.rows.items()):
            if r is row:
                del self.rows[key]

    def scalars(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        rows = [r for r in self.added if type(r) is entity]
        for predicate in stmt._where_criteria:
            rows = [r for r in rows if value(predicate, r)]
        if stmt._limit_clause is not None:
            rows = rows[: stmt._limit_clause.value]
        return SimpleNamespace(all=lambda: rows)

    def scalar(self, stmt):
        rows = self.scalars(stmt).all()
        return rows[-1] if rows else None

    def execute(self, stmt, *args, **kwargs):
        if "pg_advisory_xact_lock" in str(stmt):
            return None
        raise AssertionError("Unit repository cannot execute SQL mutations")

    @contextmanager
    def transaction(self, engine):
        self.active += 1
        try:
            yield SimpleNamespace(session=self)
        finally:
            self.active -= 1
            self.commits += 1


def seed(root: Path):
    from agentforge.contracts.plans import PlanningContext
    from agentforge.planning.hashing import compute_contract_hash, compute_plan_hash
    from agentforge.planning.proposals import build_rules_draft

    s = Ledger()
    now = datetime.now(UTC)
    root.mkdir(exist_ok=True, parents=True)
    for row in [
        m.DirectoryGrantRow(
            id="grant",
            authorized_user="local",
            environment_id="env",
            normalized_root=str(root),
            directory_identity="directory",
            allowed_operations=["read", "write"],
            grant_version=1,
            status="active",
            created_at=now,
        ),
        m.Project(
            id="project",
            directory_grant_id="grant",
            root_path=str(root),
            workspace_root=str(root),
            grant_status="active",
            current_baseline_id="baseline",
            environment_id="env",
            grant_version=1,
            revision=1,
            trust_policy={},
        ),
        m.ProjectBaselineRow(
            id="baseline",
            project_id="project",
            environment_id="env",
            status="ready",
            dirty_policy="committed_only",
            is_git=False,
            scope_strategy="authorized_root",
            notes=[],
            git_dirty_summary={},
        ),
        m.Run(
            id="run",
            project_id="project",
            goal="Produce a file with verified integrity",
            mode="single",
            status="preparing",
            status_version=1,
            plan_version=1,
            assignment_version=1,
            gate_policy_version=1,
            active_generation=1,
            last_sequence=0,
            baseline_id="baseline",
            execution_profile="host_authorized",
            failure_policy="dependency-aware",
            budget={"maxTokens": 1000},
            outputs=["markdown"],
            input_refs=[],
            planning_provider_profile_id=None,
        ),
    ]:
        s.add(row)
    run = s.get(m.Run, "run")
    context = PlanningContext(
        contextId="context",
        projectId="project",
        runId="run",
        baselineId="baseline",
        inputIds=[],
        readScopes=[],
        summary="metadata",
        contentHash="a" * 64,
        createdAt=now,
    )
    spec = build_rules_draft(run=run, context=context)
    raw = spec.model_dump(mode="json", by_alias=True)
    raw["stages"][0]["gatePolicy"]["requireUserDecision"] = False
    wp = raw["workPackages"][0]
    wp["deliverables"][0]["validators"][0]["id"] = "sha256_presence"
    wp["deliverables"][0]["criteria"][0]["description"] = (
        "Immutable object matches its registered SHA256"
    )
    from agentforge.contracts.plans import PlanSpec

    spec = PlanSpec.model_validate(raw)
    plan = m.Plan(
        run_id="run",
        version=1,
        status="approved",
        summary="integrity only",
        spec_json=spec.model_dump(mode="json", by_alias=True),
        plan_hash=compute_plan_hash(spec),
        contract_hash=compute_contract_hash(
            [d for w in spec.work_packages for d in w.deliverables]
        ),
        gate_policy_version=1,
    )
    s.add(plan)
    for role in spec.roles:
        profile = "profile-" + role.role_id
        install = "install-" + role.role_id
        check = "check-" + role.role_id
        for row in [
            m.AgentInstallationRow(
                id=install,
                product_id="test-product",
                product_version="1",
                environment_id="env",
                location_ref="test",
                discovered_at=now,
            ),
            m.AgentProfileRow(
                id=profile,
                installation_id=install,
                adapter_id="test",
                adapter_version="1",
                config_version="1",
                config_hash="a" * 64,
                auth_status="authenticated",
                access_level="controlled_auto",
                last_validation_id=check,
            ),
            m.CapabilityCheckRow(
                id=check,
                profile_id=profile,
                product_version="1",
                adapter_id="test",
                adapter_version="1",
                environment_id="env",
                checked_at=now,
                valid_until=now + timedelta(hours=1),
                results=[
                    {"capability": c, "support": "supported"} for c in role.required_capabilities
                ],
                evidence_refs=["config:" + "a" * 64],
            ),
            m.RoleAssignmentRow(
                id="assignment-" + role.role_id,
                run_id="run",
                plan_version=1,
                assignment_version=1,
                role_id=role.role_id,
                agent_profile_id=profile,
                profile_config_version="1",
                capability_check_refs=[check],
                confirmed_at=now,
            ),
        ]:
            s.add(row)
        wsroot = root / role.role_id
        wsroot.mkdir()
        s.add(
            m.GitWorkspaceRow(
                id="workspace-" + role.role_id,
                project_id="project",
                baseline_id="baseline",
                strategy="commit_only",
                status="active",
                worktree_path=str(wsroot),
                source_root=str(root),
                role_id=role.role_id,
                restrictions=[],
                dirty_included=False,
            )
        )
    for definition in spec.stages:
        s.add(
            m.StageRow(
                id=f"run:p1:{definition.stage_id}",
                run_id="run",
                plan_version=1,
                assignment_version=1,
                gate_policy_version=1,
                gate_policy=definition.gate_policy.model_dump(mode="json", by_alias=True),
                status="ready",
                write_access_closed=False,
                concurrency_version=1,
                repair_round=0,
                required_work_packages=definition.required_work_packages,
                dependencies=[],
            )
        )
    return s, spec

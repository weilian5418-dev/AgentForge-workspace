"""Pure PlanCompiler — no disk, model, or network I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable

from agentforge.contracts.plans import (
    AssignmentSet,
    CompilationDiagnostic,
    CompilationReport,
    CompilationStatus,
    PlanSpec,
)


COMPILER_VERSION = "plan-compiler-v1"


@dataclass(frozen=True)
class CapabilityEvidenceFact:
    """Versioned capability fact passed in by a trusted service."""

    check_id: str
    profile_id: str
    capabilities: frozenset[str]
    expired: bool = False
    support_unknown: bool = False


@dataclass(frozen=True)
class CompilerFacts:
    """Trusted, already-loaded facts for compile-time checks."""

    known_validator_ids: frozenset[str] = field(default_factory=frozenset)
    capability_evidence: tuple[CapabilityEvidenceFact, ...] = ()
    assignment: AssignmentSet | None = None
    at: datetime | None = None


def _diag(
    code: str,
    message: str,
    *,
    severity: str = "error",
    entity_type: str | None = None,
    entity_id: str | None = None,
    field_path: str | None = None,
    suggested_resolution: str | None = None,
) -> CompilationDiagnostic:
    return CompilationDiagnostic(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        entity_type=entity_type,
        entity_id=entity_id,
        field_path=field_path,
        message=message,
        suggested_resolution=suggested_resolution,
    )


def _topo_has_cycle(nodes: Iterable[str], edges: dict[str, set[str]]) -> list[str]:
    """Return one cycle path if present, else empty list. Kahn's algorithm."""
    indeg: dict[str, int] = {n: 0 for n in nodes}
    for src, dsts in edges.items():
        for dst in dsts:
            if dst in indeg:
                indeg[dst] += 1
    queue = [n for n, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for dst in edges.get(n, ()):
            if dst not in indeg:
                continue
            indeg[dst] -= 1
            if indeg[dst] == 0:
                queue.append(dst)
    if seen == len(indeg):
        return []
    # Reconstruct a simple cycle among remaining nodes for diagnostics.
    remain = {n for n, d in indeg.items() if d > 0}
    if not remain:
        return ["<cycle>"]
    start = next(iter(remain))
    path = [start]
    cur = start
    visited = {start}
    while True:
        nxts = [d for d in edges.get(cur, ()) if d in remain]
        if not nxts:
            break
        nxt = nxts[0]
        path.append(nxt)
        if nxt in visited:
            return path
        visited.add(nxt)
        cur = nxt
    return path


def _paths_overlap(a: str, b: str) -> bool:
    left = a.rstrip("/")
    right = b.rstrip("/")
    if not left or not right:
        return True
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def compile_plan(spec: PlanSpec, facts: CompilerFacts | None = None) -> CompilationReport:
    """Validate and normalize judgment for a PlanSpec.

    Returns CompilationReport only — never mutates RunStatus.
    """
    facts = facts or CompilerFacts()
    errors: list[CompilationDiagnostic] = []
    warnings: list[CompilationDiagnostic] = []
    blocked: list[CompilationDiagnostic] = []
    needs_input: list[CompilationDiagnostic] = []

    # 1. Structure and identity
    if not spec.goal.strip():
        needs_input.append(
            _diag("missing_goal", "Plan goal is required", field_path="goal")
        )

    role_ids = {r.role_id for r in spec.roles}
    wp_by_id = {w.id: w for w in spec.work_packages}
    stage_by_id = {s.stage_id: s for s in spec.stages}

    # 2. Delivery integrity
    for wp in spec.work_packages:
        if not wp.deliverables:
            errors.append(
                _diag(
                    "missing_deliverables",
                    "Work package has no deliverables",
                    entity_type="work_package",
                    entity_id=wp.id,
                    field_path="deliverables",
                )
            )
            continue
        for art in wp.deliverables:
            if not art.criteria:
                needs_input.append(
                    _diag(
                        "missing_acceptance",
                        f"Deliverable {art.id} lacks acceptance criteria",
                        entity_type="deliverable",
                        entity_id=art.id,
                    )
                )
            for validator in art.validators:
                if facts.known_validator_ids and validator.id not in facts.known_validator_ids:
                    blocked.append(
                        _diag(
                            "unknown_validator",
                            f"Validator {validator.id} is not registered",
                            entity_type="validator",
                            entity_id=validator.id,
                            suggested_resolution="Register validator or remove requirement",
                        )
                    )

    # Producers: every required deliverable id should appear once as primary output
    produced: dict[str, str] = {}
    for wp in spec.work_packages:
        for art in wp.deliverables:
            if art.id in produced:
                errors.append(
                    _diag(
                        "duplicate_producer",
                        f"Deliverable {art.id} has multiple producers",
                        entity_type="deliverable",
                        entity_id=art.id,
                        suggested_resolution="Keep a single primary producer",
                    )
                )
            else:
                produced[art.id] = wp.id

    # 3. Work package graph
    wp_edges: dict[str, set[str]] = {w.id: set() for w in spec.work_packages}
    for wp in spec.work_packages:
        for dep in wp.depends_on:
            if dep not in wp_by_id:
                errors.append(
                    _diag(
                        "unknown_dependency",
                        f"Work package depends on unknown id {dep}",
                        entity_type="work_package",
                        entity_id=wp.id,
                        field_path="dependsOn",
                    )
                )
            else:
                # edge: dep → wp (dep must complete before wp)
                wp_edges[dep].add(wp.id)
    cycle = _topo_has_cycle(wp_by_id.keys(), wp_edges)
    if cycle:
        errors.append(
            _diag(
                "work_package_cycle",
                "Work package dependency cycle: " + " → ".join(cycle),
                entity_type="work_package",
                entity_id=cycle[0] if cycle else None,
            )
        )

    # 4. Stage graph
    stage_edges: dict[str, set[str]] = {s.stage_id: set() for s in spec.stages}
    for stage in spec.stages:
        for dep in stage.dependencies:
            if dep not in stage_by_id:
                errors.append(
                    _diag(
                        "unknown_stage_dependency",
                        f"Stage depends on unknown id {dep}",
                        entity_type="stage",
                        entity_id=stage.stage_id,
                        field_path="dependencies",
                    )
                )
            else:
                stage_edges[dep].add(stage.stage_id)
        for wp_id in stage.required_work_packages:
            if wp_id not in wp_by_id:
                errors.append(
                    _diag(
                        "unknown_stage_work_package",
                        f"Stage references unknown work package {wp_id}",
                        entity_type="stage",
                        entity_id=stage.stage_id,
                    )
                )
            elif wp_by_id[wp_id].stage_id != stage.stage_id:
                errors.append(
                    _diag(
                        "stage_work_package_mismatch",
                        f"Work package {wp_id} stageId does not match stage {stage.stage_id}",
                        entity_type="work_package",
                        entity_id=wp_id,
                    )
                )
    stage_cycle = _topo_has_cycle(stage_by_id.keys(), stage_edges)
    if stage_cycle:
        errors.append(
            _diag(
                "stage_cycle",
                "Stage dependency cycle: " + " → ".join(stage_cycle),
                entity_type="stage",
                entity_id=stage_cycle[0] if stage_cycle else None,
            )
        )

    # 5. Cross-graph: WP dependency must not invert stage order
    stage_index = {s.stage_id: i for i, s in enumerate(_stage_order(spec, stage_edges))}
    for wp in spec.work_packages:
        for dep in wp.depends_on:
            other = wp_by_id.get(dep)
            if other is None:
                continue
            a = stage_index.get(other.stage_id)
            b = stage_index.get(wp.stage_id)
            if a is not None and b is not None and a > b:
                errors.append(
                    _diag(
                        "cross_graph_deadlock",
                        f"Work package {wp.id} depends on {dep} in a later stage",
                        entity_type="work_package",
                        entity_id=wp.id,
                        suggested_resolution="Align work-package deps with stage order",
                    )
                )

    # Gate pitfall: review-required roles must not also be sole writers of under-review packages
    # (independence checked below). Distinguishing "candidate ready" vs "stage released"
    # is a D10 concern; here we only refuse review that depends on stage release of self.
    for stage in spec.stages:
        if stage.gate_policy.require_user_decision and not stage.gate_policy.required_review_roles:
            # User decision without named review roles is allowed; flag as needs_input if unclear.
            pass

    # 6. Roles and scopes
    for wp in spec.work_packages:
        if wp.owner_role_id not in role_ids:
            errors.append(
                _diag(
                    "unknown_owner_role",
                    f"Owner role {wp.owner_role_id} is not in plan roles",
                    entity_type="work_package",
                    entity_id=wp.id,
                    field_path="ownerRoleId",
                )
            )
        role = next((r for r in spec.roles if r.role_id == wp.owner_role_id), None)
        if role is not None:
            for scope in wp.write_scopes:
                if not any(_paths_overlap(scope, granted) for granted in role.write_scopes):
                    errors.append(
                        _diag(
                            "write_scope_outside_role",
                            f"Write scope {scope} exceeds role {role.role_id}",
                            entity_type="work_package",
                            entity_id=wp.id,
                            field_path="writeScopes",
                        )
                    )

    # Shared write conflicts
    for i, left in enumerate(spec.work_packages):
        for right in spec.work_packages[i + 1 :]:
            overlaps = [
                (a, b)
                for a in left.write_scopes
                for b in right.write_scopes
                if _paths_overlap(a, b)
            ]
            if overlaps and not (
                left.shared_write_coordination and right.shared_write_coordination
            ):
                errors.append(
                    _diag(
                        "shared_write_uncoordinated",
                        f"Shared write between {left.id} and {right.id} lacks coordination",
                        entity_type="work_package",
                        entity_id=left.id,
                        suggested_resolution="Set sharedWriteCoordination on both packages",
                    )
                )

    # 7. Audit rules — independent review roles must not own the package under review
    for stage in spec.stages:
        reviewers = set(stage.gate_policy.required_review_roles)
        for role_id in reviewers:
            if role_id not in role_ids:
                errors.append(
                    _diag(
                        "unknown_review_role",
                        f"Review role {role_id} is not in plan roles",
                        entity_type="stage",
                        entity_id=stage.stage_id,
                    )
                )
                continue
            role = next(r for r in spec.roles if r.role_id == role_id)
            if not role.independent_review:
                errors.append(
                    _diag(
                        "review_not_independent",
                        f"Review role {role_id} is not marked independentReview",
                        entity_type="role",
                        entity_id=role_id,
                    )
                )
            owners = {
                wp.owner_role_id
                for wp in spec.work_packages
                if wp.id in set(stage.required_work_packages)
            }
            if role_id in owners:
                errors.append(
                    _diag(
                        "reviewer_is_producer",
                        f"Review role {role_id} also owns work in stage {stage.stage_id}",
                        entity_type="stage",
                        entity_id=stage.stage_id,
                        suggested_resolution="Use a separate independent reviewer",
                    )
                )

    # 8. Budget / permission requirements expressible
    for role in spec.roles:
        if not role.required_capabilities:
            needs_input.append(
                _diag(
                    "missing_capabilities",
                    f"Role {role.role_id} has no requiredCapabilities",
                    entity_type="role",
                    entity_id=role.role_id,
                )
            )
    if spec.resource_estimate is None:
        warnings.append(
            _diag(
                "missing_estimate",
                "No resource estimate provided",
                severity="warning",
                field_path="resourceEstimate",
            )
        )
    elif not spec.resource_estimate.basis.strip():
        needs_input.append(
            _diag(
                "missing_estimate_basis",
                "Resource estimate lacks basis",
                field_path="resourceEstimate.basis",
            )
        )

    # 9. Allocation capability (only when assignment present)
    evidence_refs: list[str] = []
    if facts.assignment is not None:
        assigned_roles = {item.role_id for item in facts.assignment.items}
        missing_roles = role_ids - assigned_roles
        for role_id in sorted(missing_roles):
            blocked.append(
                _diag(
                    "role_unassigned",
                    f"Role {role_id} has no assignment",
                    entity_type="role",
                    entity_id=role_id,
                )
            )
        evidence_by_profile = {e.profile_id: e for e in facts.capability_evidence}
        for item in facts.assignment.items:
            role = next((r for r in spec.roles if r.role_id == item.role_id), None)
            if role is None:
                continue
            ev = evidence_by_profile.get(item.agent_profile_id)
            if ev is None:
                blocked.append(
                    _diag(
                        "capability_evidence_missing",
                        f"No capability evidence for profile {item.agent_profile_id}",
                        entity_type="assignment",
                        entity_id=item.assignment_id,
                    )
                )
                continue
            evidence_refs.append(ev.check_id)
            if ev.expired or ev.support_unknown:
                blocked.append(
                    _diag(
                        "capability_evidence_unusable",
                        f"Capability evidence {ev.check_id} expired or unknown",
                        entity_type="capability_check",
                        entity_id=ev.check_id,
                    )
                )
                continue
            missing_caps = [
                cap for cap in role.required_capabilities if cap not in ev.capabilities
            ]
            for cap in missing_caps:
                blocked.append(
                    _diag(
                        "capability_unmet",
                        f"Profile {item.agent_profile_id} lacks capability {cap}",
                        entity_type="assignment",
                        entity_id=item.assignment_id,
                    )
                )

    status = _aggregate_status(errors=errors, blocked=blocked, needs_input=needs_input)
    issues = [*errors, *blocked, *needs_input]
    return CompilationReport(
        status=status,
        compiler_version=COMPILER_VERSION,
        issues=issues,
        warnings=warnings,
        capability_evidence_refs=list(dict.fromkeys(evidence_refs)),
    )


def _stage_order(spec: PlanSpec, edges: dict[str, set[str]]) -> list[Any]:
    indeg = {s.stage_id: 0 for s in spec.stages}
    for _src, dsts in edges.items():
        for dst in dsts:
            if dst in indeg:
                indeg[dst] += 1
    ready = [s for s in spec.stages if indeg[s.stage_id] == 0]
    ordered: list[Any] = []
    seen: set[str] = set()
    while ready:
        stage = ready.pop(0)
        if stage.stage_id in seen:
            continue
        seen.add(stage.stage_id)
        ordered.append(stage)
        for dst in edges.get(stage.stage_id, ()):
            indeg[dst] -= 1
            if indeg[dst] == 0:
                nxt = next(s for s in spec.stages if s.stage_id == dst)
                ready.append(nxt)
    # Append any remaining (cyclic) stages in declared order for index lookup.
    for stage in spec.stages:
        if stage.stage_id not in seen:
            ordered.append(stage)
    return ordered


def _aggregate_status(
    *,
    errors: list[CompilationDiagnostic],
    blocked: list[CompilationDiagnostic],
    needs_input: list[CompilationDiagnostic],
) -> CompilationStatus:
    if errors:
        return "invalid"
    if blocked:
        return "blocked"
    if needs_input:
        return "needs_input"
    return "valid"

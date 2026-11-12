"""Trusted ExecutionContextLoader — binds from DB, never from caller path strings."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from agentforge.instances.errors import InstanceError
from agentforge.instances.repository import InstanceRepository
from agentforge.runtime.execution_context import ExecutionContext, bind_execution_context
from agentforge.storage.models import (
    AgentProfileRow,
    GitWorkspaceRow,
    Plan,
    Project,
    RoleAssignmentRow,
    Run,
)


class ExecutionContextLoader:
    """assignment → instance → session → attempt → workspace → env/permissions."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = InstanceRepository(session)

    def load(
        self,
        *,
        instance_id: str,
        operation_id: str,
        require_session: bool = False,
        attempt_id: str | None = None,
    ) -> ExecutionContext:
        instance = self.repo.get_instance(instance_id)
        if instance is None:
            raise InstanceError("instance_not_found", f"Instance not found: {instance_id}")

        run = self.session.get(Run, instance.run_id)
        if run is None:
            raise InstanceError("run_not_found", f"Run not found: {instance.run_id}")

        assignment = self.session.get(RoleAssignmentRow, instance.assignment_id)
        if assignment is None:
            raise InstanceError(
                "assignment_not_found", f"Assignment not found: {instance.assignment_id}"
            )
        if assignment.run_id != instance.run_id:
            raise InstanceError("assignment_mismatch", "Assignment does not belong to instance run")
        if (
            instance.assignment_version is not None
            and assignment.assignment_version != instance.assignment_version
        ):
            raise InstanceError(
                "assignment_version_mismatch",
                "Instance assignmentVersion does not match role assignment",
            )
        if run.plan_version is not None and assignment.plan_version != run.plan_version:
            raise InstanceError(
                "plan_version_mismatch",
                "Assignment planVersion is not the run's current plan",
            )

        plan = None
        if instance.plan_version is not None:
            plan = self.session.get(Plan, (instance.run_id, instance.plan_version))
            if plan is None:
                raise InstanceError("plan_not_found", "Bound plan version missing")
            if instance.plan_hash and plan.plan_hash and instance.plan_hash != plan.plan_hash:
                raise InstanceError("plan_hash_mismatch", "Plan hash no longer matches")

        profile = self.session.get(AgentProfileRow, instance.agent_profile_id)
        if profile is None:
            raise InstanceError("profile_not_found", "Agent profile missing")
        if profile.auth_status in ("unauthenticated", "expired"):
            raise InstanceError("auth_invalid", f"Profile auth status is {profile.auth_status}")

        native = None
        if instance.native_session_id:
            native = self.repo.get_native_session(instance.native_session_id)
            if native is None:
                raise InstanceError("session_not_found", "Bound native session missing")
            if native.instance_id != instance.id:
                raise InstanceError("session_mismatch", "Native session not bound to instance")
            if native.binding_status not in ("active", "needs_reconciliation"):
                raise InstanceError(
                    "session_inactive",
                    f"Native session binding_status={native.binding_status}",
                )

        resolved_attempt_id = attempt_id or instance.current_attempt_id
        attempt = None
        if resolved_attempt_id:
            attempt = self.repo.get_attempt(resolved_attempt_id)
            if attempt is None:
                raise InstanceError("attempt_not_found", "Attempt missing")
            if attempt.instance_id != instance.id:
                raise InstanceError("attempt_mismatch", "Attempt does not belong to instance")

        workspace_id = (
            (attempt.workspace_id if attempt else None)
            or (native.workspace_id if native else None)
            or instance.workspace_id
        )
        if not workspace_id:
            raise InstanceError("workspace_unbound", "No workspace bound to this execution")

        workspace = self.session.get(GitWorkspaceRow, workspace_id)
        project = self.session.get(Project, run.project_id)
        if workspace is not None:
            workspace_root = workspace.worktree_path
            if project is not None and workspace.project_id != project.id:
                raise InstanceError(
                    "workspace_project_mismatch",
                    "Workspace does not belong to the run project",
                )
        elif project is not None:
            # Fallback authorized project root — still not a caller-supplied absolute path.
            workspace_root = project.workspace_root or project.root_path
        else:
            raise InstanceError("workspace_unresolved", "Cannot resolve workspace root from DB")

        root = Path(workspace_root)
        if not root.is_absolute():
            raise InstanceError(
                "invalid_workspace_root",
                "Resolved workspace root must be absolute (platform query only)",
            )

        environment_id = instance.execution_environment
        generation = (
            (attempt.generation if attempt else None)
            or instance.generation
            or getattr(run, "active_generation", 1)
            or 1
        )
        session_ref = (
            (native.session_ref if native else None) or instance.session_ref
        )
        if require_session and not session_ref:
            raise InstanceError("session_required", "Active sessionRef required")

        try:
            return bind_execution_context(
                profile_id=instance.agent_profile_id,
                operation_id=operation_id,
                instance_id=instance.id,
                workspace_id=workspace_id,
                workspace_root=str(root),
                environment_id=environment_id,
                generation=int(generation),
                attempt_id=resolved_attempt_id,
                session_ref=session_ref,
                assignment_version=instance.assignment_version or assignment.assignment_version,
                baseline_id=getattr(run, "baseline_id", None) or getattr(project, "current_baseline_id", None),
                permission_scope_ref=instance.permission_scope_ref,
                budget_ref=instance.budget_ref,
                expected_workspace_id=workspace_id,
                expected_environment_id=environment_id,
                expected_attempt_id=resolved_attempt_id,
            )
        except Exception as exc:  # bind_execution_context raises ExecutionContextError
            from agentforge.runtime.execution_context import ExecutionContextError

            if isinstance(exc, ExecutionContextError):
                raise InstanceError(exc.code, exc.message) from exc
            raise

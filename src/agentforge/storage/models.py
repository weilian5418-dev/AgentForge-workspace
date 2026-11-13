"""SQLAlchemy models reconciled to the existing app schema baseline."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentforge.storage.base import Base


class ExecutionEnvironmentRow(Base):
    __tablename__ = "execution_environments"
    __table_args__ = (PrimaryKeyConstraint("id", name="execution_environments_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    wsl_distro: Mapped[str | None] = mapped_column(Text, nullable=True)
    connection_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unverified'")
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DirectoryGrantRow(Base):
    __tablename__ = "directory_grants"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="directory_grants_pkey"),
        CheckConstraint("grant_version >= 1", name="directory_grants_version_check"),
        Index("ix_directory_grants_user_status", "authorized_user", "status"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_user: Mapped[str] = mapped_column(Text, nullable=False)
    environment_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.execution_environments.id", name="directory_grants_environment_id_fkey"),
        nullable=False,
    )
    normalized_root: Mapped[str] = mapped_column(Text, nullable=False)
    directory_identity: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_operations: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    grant_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="projects_pkey"),
        CheckConstraint("revision >= 1", name="projects_revision_check"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    directory_grant_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_root: Mapped[str] = mapped_column(Text, nullable=False)
    trust_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    environment_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    grant_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_baseline_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    grant_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectBaselineRow(Base):
    __tablename__ = "project_baselines"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="project_baselines_pkey"),
        Index("ix_project_baselines_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.projects.id", name="project_baselines_project_id_fkey"),
        nullable=False,
    )
    environment_id: Mapped[str] = mapped_column(Text, nullable=False)
    dirty_policy: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    is_git: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    git_commit: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_dirty_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    manifest_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_strategy: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectInputRow(Base):
    __tablename__ = "project_inputs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="project_inputs_pkey"),
        UniqueConstraint(
            "project_id", "original_name", "input_version", name="uq_project_inputs_name_version"
        ),
        Index("ix_project_inputs_project_status", "project_id", "status"),
        CheckConstraint("input_version >= 1", name="project_inputs_version_check"),
        CheckConstraint("size_bytes >= 0", name="project_inputs_size_check"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.projects.id", name="project_inputs_project_id_fkey"), nullable=False
    )
    input_version: Mapped[int] = mapped_column(Integer, nullable=False)
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'staging'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EnvironmentPathBindingRow(Base):
    __tablename__ = "environment_path_bindings"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="environment_path_bindings_pkey"),
        CheckConstraint("binding_version >= 1", name="environment_path_bindings_version_check"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    source_environment_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_environment_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    target_path: Mapped[str] = mapped_column(Text, nullable=False)
    correspondence_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    binding_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="runs_pkey"),
        CheckConstraint("status_version >= 1", name="runs_status_version_check"),
        CheckConstraint("active_generation >= 1", name="runs_active_generation_check"),
        CheckConstraint("last_sequence >= 0", name="runs_last_sequence_check"),
        CheckConstraint(
            "plan_version IS NULL OR plan_version >= 1", name="runs_plan_version_check"
        ),
        CheckConstraint("char_length(btrim(goal)) > 0", name="runs_goal_check"),
        CheckConstraint(
            "mode = ANY (ARRAY['auto'::text, 'single'::text, 'team'::text])",
            name="runs_mode_check",
        ),
        CheckConstraint(
            "execution_profile = ANY (ARRAY['container'::text, 'host_authorized'::text])",
            name="runs_execution_profile_check",
        ),
        CheckConstraint(
            "failure_policy = ANY (ARRAY['dependency-aware'::text, 'layer-atomic'::text])",
            name="runs_failure_policy_check",
        ),
        CheckConstraint(
            "status = ANY (ARRAY["
            "'draft'::text, 'preflight'::text, 'routing'::text, 'planning'::text, "
            "'awaiting_approval'::text, 'awaiting_confirmation'::text, 'preparing'::text, "
            "'running'::text, 'awaiting_acceptance'::text, "
            "'completed'::text, 'pause_requested'::text, 'paused'::text, 'blocked'::text, "
            "'cancel_requested'::text, 'canceled'::text, 'failed'::text])",
            name="runs_status_check",
        ),
        Index("runs_project_id_idx", "project_id"),
        Index("runs_status_idx", "status"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.projects.id", name="runs_project_id_fkey"), nullable=False
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    status_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Execution generation: bump only when explicitly invalidating prior attempts.
    active_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    execution_profile: Mapped[str] = mapped_column(Text, nullable=False)
    failure_policy: Mapped[str] = mapped_column(Text, nullable=False)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Planning model profile only — not role executors (see agent_instances).
    # Nullable: rules-only planning must not invent a fake provider profile.
    planning_provider_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_confirmation_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    outputs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Collaboration progress (D11) — does not replace Run.status.
    collaboration_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'idle'")
    )
    collaboration_ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Plan(Base):
    """Immutable plan revision. Full definition lives in ``spec_json`` (source of truth)."""

    __tablename__ = "plans"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "version", name="plans_pkey"),
        CheckConstraint("version >= 1", name="plans_version_check"),
        CheckConstraint(
            "status = ANY (ARRAY['proposed'::text, 'approved'::text, 'rejected'::text, 'superseded'::text])",
            name="plans_status_check",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.runs.id", name="plans_run_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    plan_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    spec_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    compilation_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    context_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    gate_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supersedes_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hash_algo_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'plan-hash-v1'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlanningContextRow(Base):
    __tablename__ = "planning_contexts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="planning_contexts_pkey"),
        Index("ix_planning_contexts_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    baseline_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    read_scopes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    context_manifest_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlanProposalRow(Base):
    __tablename__ = "plan_proposals"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="plan_proposals_pkey"),
        Index("ix_plan_proposals_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    context_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    model_call_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    diagnostics_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlanConfirmationRequestRow(Base):
    __tablename__ = "plan_confirmation_requests"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="plan_confirmation_requests_pkey"),
        Index("ix_plan_confirmation_requests_run", "run_id", "status"),
        ForeignKeyConstraint(
            ["run_id", "plan_version"],
            ["app.plans.run_id", "app.plans.version"],
            name="plan_confirmation_requests_plan_fk",
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_hash: Mapped[str] = mapped_column(Text, nullable=False)
    assignment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    gate_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status_version: Mapped[int] = mapped_column(Integer, nullable=False)
    compilation_report_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    capability_check_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlanDecisionRow(Base):
    __tablename__ = "plan_decisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="plan_decisions_pkey"),
        UniqueConstraint("request_id", name="plan_decisions_request_unique"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.plan_confirmation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkPackage(Base):
    __tablename__ = "work_packages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="work_packages_pkey"),
        UniqueConstraint("id", "run_id", name="work_packages_id_run_id_key"),
        ForeignKeyConstraint(
            ["run_id", "plan_version"],
            ["app.plans.run_id", "app.plans.version"],
            name="work_packages_plan_fk",
        ),
        CheckConstraint("plan_version >= 1", name="work_packages_plan_version_check"),
        CheckConstraint("repair_count >= 0", name="work_packages_repair_count_check"),
        CheckConstraint(
            "status = ANY (ARRAY["
            "'pending'::text, 'ready'::text, 'running'::text, 'validating'::text, "
            "'published'::text, 'repairing'::text, 'failed'::text, 'blocked'::text, "
            "'blocked_dependency'::text])",
            name="work_packages_status_check",
        ),
        Index("work_packages_run_plan_idx", "run_id", "plan_version"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_role: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # legacy text; prefer owner_role_id
    owner_role_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    repair_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    write_scopes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="task_attempts_pkey"),
        ForeignKeyConstraint(
            ["work_package_id", "run_id"],
            ["app.work_packages.id", "app.work_packages.run_id"],
            name="task_attempts_work_package_run_fk",
            ondelete="CASCADE",
        ),
        CheckConstraint("run_generation >= 1", name="task_attempts_run_generation_check"),
        CheckConstraint(
            "status = ANY (ARRAY["
            "'leased'::text, 'running'::text, 'succeeded'::text, 'failed'::text, "
            "'rejected'::text, 'awaiting_stop_verification'::text])",
            name="task_attempts_status_check",
        ),
        Index("task_attempts_work_package_idx", "work_package_id"),
        Index(
            "task_attempts_active_unique",
            "work_package_id",
            unique=True,
            postgresql_where=text(
                "status = ANY (ARRAY['leased'::text, 'running'::text, 'awaiting_stop_verification'::text])"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    work_package_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.runs.id", name="task_attempts_run_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    run_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_instance_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token: Mapped[str] = mapped_column(Text, nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    worker_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    retry_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Event(Base):
    """Business event row. Public envelope maps created_at -> timestamp."""

    __tablename__ = "events"
    __table_args__ = (
        PrimaryKeyConstraint("event_id", name="events_pkey"),
        UniqueConstraint("run_id", "sequence", name="events_run_id_sequence_key"),
        CheckConstraint("sequence >= 1", name="events_sequence_check"),
        CheckConstraint("schema_version = '1'", name="events_schema_version_check"),
        Index("events_run_sequence_idx", "run_id", "sequence"),
    )

    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.runs.id", name="events_run_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'1'"))
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="outbox_pkey"),
        UniqueConstraint("idempotency_key", name="outbox_idempotency_key_key"),
        CheckConstraint("attempt_count >= 0", name="outbox_attempt_count_check"),
        CheckConstraint(
            "delivery_status = ANY (ARRAY["
            "'pending'::text, 'claimed'::text, 'dispatched'::text, 'failed'::text, 'dead'::text])",
            name="outbox_delivery_status_check",
        ),
        Index(
            "outbox_pending_idx",
            "delivery_status",
            "next_retry_at",
            postgresql_where=text("delivery_status = ANY (ARRAY['pending'::text, 'failed'::text])"),
        ),
        Index(
            "outbox_claimed_expired_idx",
            "claim_expires_at",
            postgresql_where=text("delivery_status = 'claimed'"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("app.runs.id", name="outbox_run_id_fkey", ondelete="CASCADE"),
        nullable=True,
    )
    event_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("app.events.event_id", name="outbox_event_id_fkey", ondelete="SET NULL"),
        nullable=True,
    )
    intent_type: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_status: Mapped[str] = mapped_column(Text, nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunLease(Base):
    """Driver control lease. `generation` increments on each successful re-acquire."""

    __tablename__ = "run_leases"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", name="run_leases_pkey"),
        CheckConstraint("generation >= 1", name="run_leases_generation_check"),
    )

    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.runs.id", name="run_leases_run_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyRequest(Base):
    __tablename__ = "idempotency_requests"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="idempotency_requests_pkey"),
        UniqueConstraint(
            "identity_scope",
            "operation_type",
            "request_key",
            name="idempotency_requests_identity_scope_operation_type_request__key",
        ),
        CheckConstraint(
            "state = ANY (ARRAY['processing'::text, 'completed'::text])",
            name="idempotency_requests_state_check",
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    identity_scope: Mapped[str] = mapped_column(Text, nullable=False)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    request_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'completed'"))
    processing_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SchemaMigration(Base):
    """Legacy pre-Alembic tracker retained for audit; new changes use Alembic only."""

    __tablename__ = "schema_migrations"
    __table_args__ = (PrimaryKeyConstraint("version", name="schema_migrations_pkey"),)

    version: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentProductRow(Base):
    __tablename__ = "agent_products"
    __table_args__ = (PrimaryKeyConstraint("id", name="agent_products_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentInstallationRow(Base):
    __tablename__ = "agent_installations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="agent_installations_pkey"),
        Index("agent_installations_product_idx", "product_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    product_version: Mapped[str] = mapped_column(Text, nullable=False)
    environment_id: Mapped[str] = mapped_column(Text, nullable=False)
    location_ref: Mapped[str] = mapped_column(Text, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentProfileRow(Base):
    __tablename__ = "agent_profiles"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="agent_profiles_pkey"),
        Index("agent_profiles_installation_idx", "installation_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    installation_id: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_id: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    auth_status: Mapped[str] = mapped_column(Text, nullable=False)
    access_level: Mapped[str] = mapped_column(Text, nullable=False)
    last_validation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RoleAssignmentRow(Base):
    __tablename__ = "role_assignments"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="role_assignments_pkey"),
        UniqueConstraint(
            "run_id",
            "assignment_version",
            "role_id",
            name="role_assignments_run_version_role_key",
        ),
        Index("role_assignments_run_idx", "run_id", "assignment_version"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    role_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    capability_check_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    profile_config_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentInstanceRow(Base):
    """Role execution instance — distinct from platform API PlatformInstance."""

    __tablename__ = "agent_instances"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="agent_instances_pkey"),
        Index("agent_instances_run_idx", "run_id"),
        Index("agent_instances_status_idx", "status"),
        Index("agent_instances_lifecycle_idx", "lifecycle", "activity"),
        UniqueConstraint(
            "run_id",
            "assignment_id",
            "role_id",
            name="agent_instances_run_assignment_role_uidx",
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    assignment_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    assignment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    gate_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    native_session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_work_package_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'created'"))
    activity: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'idle'"))
    execution_environment: Mapped[str] = mapped_column(Text, nullable=False)
    current_attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_scope_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NativeSessionRow(Base):
    __tablename__ = "native_sessions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="native_sessions_pkey"),
        Index("native_sessions_instance_idx", "instance_id", "created_at"),
        UniqueConstraint("create_operation_id", name="native_sessions_create_op_uidx"),
        Index(
            "native_sessions_active_per_instance_uidx",
            "instance_id",
            unique=True,
            postgresql_where=text("binding_status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    instance_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.agent_instances.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    environment_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    create_operation_id: Mapped[str] = mapped_column(Text, nullable=False)
    binding_status: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstanceOperationRow(Base):
    __tablename__ = "instance_operations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="instance_operations_pkey"),
        Index(
            "instance_operations_claim_idx",
            "status",
            "claim_expires_at",
            postgresql_where=text("status IN ('prepared', 'claimed')"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    instance_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.agent_instances.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    native_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstanceAttemptRow(Base):
    __tablename__ = "instance_attempts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="instance_attempts_pkey"),
        Index("instance_attempts_instance_idx", "instance_id", "created_at"),
        Index(
            "instance_attempts_active_wp_uidx",
            "run_id",
            "work_package_id",
            unique=True,
            postgresql_where=text(
                "status = ANY (ARRAY["
                "'scheduled'::text, 'dispatching'::text, 'running'::text, "
                "'submitted'::text, 'awaiting_stop_verification'::text, 'unknown'::text])"
            ),
        ),
        Index(
            "instance_attempts_active_instance_uidx",
            "instance_id",
            unique=True,
            postgresql_where=text(
                "status = ANY (ARRAY["
                "'scheduled'::text, 'dispatching'::text, 'running'::text, "
                "'submitted'::text, 'awaiting_stop_verification'::text, 'unknown'::text])"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    instance_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.agent_instances.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    work_package_id: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    native_session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'scheduled'"))
    dispatch_delivery_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduler_operation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submission_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExecutionRequestRow(Base):
    __tablename__ = "execution_requests"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="execution_requests_pkey"),
        UniqueConstraint(
            "run_id", "idempotency_key", name="execution_requests_run_idempotency_uidx"
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    plan_hash: Mapped[str] = mapped_column(Text, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    gate_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StageRow(Base):
    __tablename__ = "stages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="stages_pkey"),
        Index("stages_run_idx", "run_id", "plan_version"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    waiting_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'none'"))
    concurrency_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    repair_round: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    current_candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_review_cycle_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_candidate_set_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_work_packages: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    dependencies: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    write_access_closed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GateDecisionRow(Base):
    __tablename__ = "gate_decisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="gate_decisions_pkey"),
        Index("gate_decisions_stage_idx", "stage_id"),
        ForeignKeyConstraint(
            ["stage_id"],
            ["app.stages.id"],
            name="gate_decisions_stage_id_fkey",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[str] = mapped_column(Text, nullable=False)
    review_cycle_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_set_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    gate_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    review_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    user_decision_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_consume_work_package_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    result: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CollaborationMessageRow(Base):
    __tablename__ = "collaboration_messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="collaboration_messages_pkey"),
        Index("collaboration_messages_run_idx", "run_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_package_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_type: Mapped[str] = mapped_column(Text, nullable=False)
    sender_ref: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    artifact_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reply_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    derived_from_gate_decision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MessageDeliveryRow(Base):
    __tablename__ = "message_deliveries"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="message_deliveries_pkey"),
        UniqueConstraint(
            "message_id",
            "recipient_instance_id",
            "attempt_id",
            name="message_deliveries_dedupe_key",
        ),
        ForeignKeyConstraint(
            ["message_id"],
            ["app.collaboration_messages.id"],
            name="message_deliveries_message_id_fkey",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["recipient_instance_id"],
            ["app.agent_instances.id"],
            name="message_deliveries_recipient_instance_id_fkey",
            ondelete="CASCADE",
        ),
        Index(
            "message_deliveries_claim_idx",
            "transmission_status",
            "claim_expires_at",
            postgresql_where=text("transmission_status IN ('pending', 'claimed')"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_instance_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Delivery-ledger attempt identity (not task_attempts FK).
    attempt_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    transmission_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    reply_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_required'")
    )
    delivery_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    native_receipt_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assignment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeliveryAttemptRow(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="delivery_attempts_pkey"),
        Index("delivery_attempts_delivery_idx", "delivery_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.message_deliveries.id", ondelete="CASCADE"), nullable=False
    )
    operation_id: Mapped[str] = mapped_column(Text, nullable=False)
    transmission_status: Mapped[str] = mapped_column(Text, nullable=False)
    native_receipt_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProtocolRequestRow(Base):
    __tablename__ = "protocol_requests"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="protocol_requests_pkey"),
        Index("protocol_requests_instance_idx", "instance_id", "status"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_response_types: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    instance_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.agent_instances.id", ondelete="CASCADE"), nullable=False
    )
    native_session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    initiated_by: Mapped[str] = mapped_column(Text, nullable=False)
    legal_responder_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    matched_response_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_response_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NativeEventReceiptRow(Base):
    __tablename__ = "native_event_receipts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="native_event_receipts_pkey"),
        UniqueConstraint(
            "instance_id",
            "source",
            "dedupe_key",
            name="native_event_receipts_dedupe_uidx",
        ),
        Index("native_event_receipts_cursor_idx", "instance_id", "source", "cursor"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    instance_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.agent_instances.id", ondelete="CASCADE"), nullable=False
    )
    native_session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    authentic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalMappingRow(Base):
    __tablename__ = "approval_mappings"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="approval_mappings_pkey"),
        Index("approval_mappings_instance_idx", "instance_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    instance_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_digest: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    native_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    user_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    native_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    mapping_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CapabilityCheckRow(Base):
    __tablename__ = "capability_checks"
    __table_args__ = (PrimaryKeyConstraint("id", name="capability_checks_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    profile_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.agent_profiles.id", ondelete="CASCADE"), nullable=False
    )
    product_version: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_id: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_version: Mapped[str] = mapped_column(Text, nullable=False)
    environment_id: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    results: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StageSubmissionRow(Base):
    __tablename__ = "stage_submissions"
    __table_args__ = (PrimaryKeyConstraint("id", name="stage_submissions_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.stages.id", ondelete="CASCADE"), nullable=False
    )
    submitter_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_package_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    attempt_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    output_location_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    candidate_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    stop_evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CandidateSetRow(Base):
    __tablename__ = "candidate_sets"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="candidate_sets_pkey"),
        Index("candidate_sets_stage_idx", "stage_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.stages.id", ondelete="CASCADE"), nullable=False
    )
    submission_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    members: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    required_work_packages_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    upstream_input_versions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    baseline_or_integration_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewCycleRow(Base):
    __tablename__ = "review_cycles"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="review_cycles_pkey"),
        UniqueConstraint("submission_id", name="review_cycles_submission_unique"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.stages.id", ondelete="CASCADE"), nullable=False
    )
    submission_id: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_set_id: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_set_hash: Mapped[str] = mapped_column(Text, nullable=False)
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    gate_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    repair_round: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewRequestRow(Base):
    __tablename__ = "review_requests"
    __table_args__ = (PrimaryKeyConstraint("id", name="review_requests_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    review_cycle_id: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_set_id: Mapped[str] = mapped_column(Text, nullable=False)
    review_role_id: Mapped[str] = mapped_column(Text, nullable=False)
    legal_responder_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewReportRow(Base):
    __tablename__ = "review_reports"
    __table_args__ = (PrimaryKeyConstraint("id", name="review_reports_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    review_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_cycle_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_instance_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.agent_instances.id"), nullable=False
    )
    stage_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.stages.id", ondelete="CASCADE"), nullable=False
    )
    candidate_set_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StageUserDecisionRow(Base):
    __tablename__ = "stage_user_decisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="stage_user_decisions_pkey"),
        UniqueConstraint("request_id", name="stage_user_decisions_request_unique"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.stages.id", ondelete="CASCADE"), nullable=False
    )
    review_cycle_id: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_set_hash: Mapped[str] = mapped_column(Text, nullable=False)
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    gate_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReworkOrderRow(Base):
    __tablename__ = "rework_orders"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="rework_orders_pkey"),
        UniqueConstraint(
            "source_review_cycle_id",
            "problem_digest",
            name="rework_orders_cycle_digest_unique",
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.stages.id", ondelete="CASCADE"), nullable=False
    )
    source_review_cycle_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_candidate_set_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_work_package_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_role_id: Mapped[str] = mapped_column(Text, nullable=False)
    problem_digest: Mapped[str] = mapped_column(Text, nullable=False)
    problems: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    allowed_modify_scopes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    checks_to_rerun: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    remaining_rounds: Mapped[int] = mapped_column(Integer, nullable=False)
    involves_plan_or_interface_change: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReleaseRecordRow(Base):
    __tablename__ = "release_records"
    __table_args__ = (PrimaryKeyConstraint("id", name="release_records_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    stage_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.stages.id", ondelete="CASCADE"), nullable=False
    )
    review_cycle_id: Mapped[str] = mapped_column(Text, nullable=False)
    gate_decision_id: Mapped[str] = mapped_column(Text, nullable=False)
    scope_kind: Mapped[str] = mapped_column(Text, nullable=False)
    work_package_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    candidate_versions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    dependency_satisfied_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    shared_interface_still_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvidenceFingerprintRow(Base):
    __tablename__ = "evidence_fingerprints"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="evidence_fingerprints_pkey"),
        UniqueConstraint(
            "content_hash",
            "contract_hash",
            "check_definition_hash",
            "environment_fingerprint",
            "input_versions_hash",
            name="evidence_fingerprints_unique",
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    check_definition_hash: Mapped[str] = mapped_column(Text, nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    input_versions_hash: Mapped[str] = mapped_column(Text, nullable=False)
    reuse_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DriverThreadRow(Base):
    __tablename__ = "driver_threads"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="driver_threads_pkey"),
        UniqueConstraint("thread_id", name="driver_threads_thread_id_unique"),
        UniqueConstraint(
            "run_id",
            "plan_version",
            "assignment_version",
            "gate_policy_version",
            "execution_generation",
            "graph_version",
            "state_schema_version",
            "checkpoint_epoch",
            name="driver_threads_binding_unique",
        ),
        Index(
            "driver_threads_active_run_uidx",
            "run_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    gate_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    graph_version: Mapped[str] = mapped_column(Text, nullable=False)
    state_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    binding_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    latest_checkpoint_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_observed_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SchedulerOperationRow(Base):
    __tablename__ = "scheduler_operations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="scheduler_operations_pkey"),
        UniqueConstraint("run_id", "operation_key", name="scheduler_operations_run_key_unique"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    binding_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunWaitRow(Base):
    __tablename__ = "run_waits"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="run_waits_pkey"),
        UniqueConstraint("run_id", "wait_key", name="run_waits_run_key_unique"),
        Index("run_waits_run_status_deadline_idx", "run_id", "status", "deadline_at"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    wait_key: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scope_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    predicate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    after_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunWakeSignalRow(Base):
    __tablename__ = "run_wake_signals"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="run_wake_signals_pkey"),
        UniqueConstraint(
            "run_id",
            "source_kind",
            "source_ref",
            name="run_wake_signals_source_unique",
        ),
        Index("run_wake_signals_claim_idx", "status", "priority", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    binding_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RecoveryCaseRow(Base):
    __tablename__ = "recovery_cases"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="recovery_cases_pkey"),
        Index(
            "recovery_cases_open_request_uidx",
            "request_operation_id",
            unique=True,
            postgresql_where=text(
                "request_operation_id IS NOT NULL AND status = ANY (ARRAY["
                "'open'::text, 'observing'::text, 'awaiting_decision'::text, "
                "'applying'::text, 'blocked'::text])"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    request_operation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    case_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocker_codes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignment_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RecoveryObservationRow(Base):
    __tablename__ = "recovery_observations"
    __table_args__ = (PrimaryKeyConstraint("id", name="recovery_observations_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.recovery_cases.id", ondelete="CASCADE"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_ref: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    support: Mapped[str] = mapped_column(Text, nullable=False)
    result_code: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    binding_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RecoveryDecisionRow(Base):
    __tablename__ = "recovery_decisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="recovery_decisions_pkey"),
        UniqueConstraint("case_id", "decision_key", name="recovery_decisions_case_key_unique"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.recovery_cases.id", ondelete="CASCADE"), nullable=False
    )
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_key: Mapped[str] = mapped_column(Text, nullable=False)
    context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlatformAdmissionRow(Base):
    __tablename__ = "platform_admission"
    __table_args__ = (
        PrimaryKeyConstraint("environment_id", name="platform_admission_pkey"),
        CheckConstraint(
            "mode = ANY (ARRAY['open'::text, 'draining'::text, 'stopped'::text])",
            name="platform_admission_mode_check",
        ),
        CheckConstraint("version >= 1", name="platform_admission_version_check"),
    )

    environment_id: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IntegrationVersionRow(Base):
    __tablename__ = "integration_versions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="integration_versions_pkey"),
        UniqueConstraint("run_id", "version_number", name="integration_versions_run_number_unique"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    input_candidate_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    commit_or_tree: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    environment_ref: Mapped[str] = mapped_column(Text, nullable=False)
    # Legacy compatibility — read-only transition fields.
    validation_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    review_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    selection_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_integration_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    tree_manifest_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_versions_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    build_environment_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EventReplayWindowRow(Base):
    __tablename__ = "event_replay_windows"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", name="event_replay_windows_pkey"),
        CheckConstraint("replay_floor_sequence >= 0", name="event_replay_windows_floor_check"),
        CheckConstraint("last_archived_sequence >= 0", name="event_replay_windows_archived_check"),
        CheckConstraint(
            "last_archived_sequence <= replay_floor_sequence",
            name="event_replay_windows_order_check",
        ),
        CheckConstraint("retention_version >= 1", name="event_replay_windows_retention_check"),
    )

    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    replay_floor_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_archived_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    retention_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IntegrationSelectionRow(Base):
    __tablename__ = "integration_selections"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="integration_selections_pkey"),
        UniqueConstraint("run_id", "request_key", name="integration_selections_run_key_unique"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    handoff_ref: Mapped[str] = mapped_column(Text, nullable=False)
    handoff_hash: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_tree_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_bindings: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    merge_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    selection_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IntegrationBuildRow(Base):
    __tablename__ = "integration_builds"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="integration_builds_pkey"),
        UniqueConstraint("run_id", "operation_key", name="integration_builds_run_key_unique"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    selection_id: Mapped[str] = mapped_column(Text, nullable=False)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IntegrationValidationCycleRow(Base):
    __tablename__ = "integration_validation_cycles"
    __table_args__ = (PrimaryKeyConstraint("id", name="integration_validation_cycles_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    version_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.integration_versions.id", ondelete="CASCADE"), nullable=False
    )
    policy_hash: Mapped[str] = mapped_column(Text, nullable=False)
    environment_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    required_checks: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    required_review_roles: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IntegrationCheckRunRow(Base):
    __tablename__ = "integration_check_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="integration_check_runs_pkey"),
        UniqueConstraint(
            "cycle_id",
            "check_id",
            "check_version",
            "config_hash",
            "attempt_no",
            name="integration_check_runs_attempt_unique",
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    cycle_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.integration_validation_cycles.id", ondelete="CASCADE"),
        nullable=False,
    )
    check_id: Mapped[str] = mapped_column(Text, nullable=False)
    check_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    operation_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationCheckReportRow(Base):
    __tablename__ = "integration_check_reports"
    __table_args__ = (PrimaryKeyConstraint("id", name="integration_check_reports_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    check_run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.integration_check_runs.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.integration_versions.id", ondelete="CASCADE"), nullable=False
    )
    tree_hash: Mapped[str] = mapped_column(Text, nullable=False)
    policy_hash: Mapped[str] = mapped_column(Text, nullable=False)
    environment_hash: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    structured_result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    report_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IntegrationReviewRequestRow(Base):
    __tablename__ = "integration_review_requests"
    __table_args__ = (PrimaryKeyConstraint("id", name="integration_review_requests_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    cycle_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.integration_validation_cycles.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.integration_versions.id", ondelete="CASCADE"), nullable=False
    )
    tree_hash: Mapped[str] = mapped_column(Text, nullable=False)
    selection_hash: Mapped[str] = mapped_column(Text, nullable=False)
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    policy_hash: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_binding: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IntegrationReviewReportRow(Base):
    __tablename__ = "integration_review_reports"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="integration_review_reports_pkey"),
        UniqueConstraint(
            "request_id", "response_key", name="integration_review_reports_request_key_unique"
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.integration_review_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    cycle_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.integration_validation_cycles.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.integration_versions.id", ondelete="CASCADE"), nullable=False
    )
    tree_hash: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_identity_ref: Mapped[str] = mapped_column(Text, nullable=False)
    response_key: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeliveryManifestRow(Base):
    __tablename__ = "delivery_manifests"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="delivery_manifests_pkey"),
        UniqueConstraint("run_id", "revision", name="delivery_manifests_run_revision_unique"),
        UniqueConstraint("manifest_hash", name="delivery_manifests_hash_unique"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[str] = mapped_column(Text, nullable=False)
    cycle_id: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    payload_ref: Mapped[str] = mapped_column(Text, nullable=False)
    technical_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunDeliveryRow(Base):
    __tablename__ = "run_deliveries"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", name="run_deliveries_pkey"),
        CheckConstraint("delivery_revision >= 1", name="run_deliveries_revision_check"),
    )

    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    current_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_manifest_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    technical_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_started'")
    )
    acceptance_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'none'")
    )
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AcceptanceDecisionRow(Base):
    __tablename__ = "acceptance_decisions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="acceptance_decisions_pkey"),
        UniqueConstraint(
            "run_id", "actor_ref", "request_key", name="acceptance_decisions_actor_key_unique"
        ),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    manifest_id: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    version_id: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_ref: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_member_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    rejected_member_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request_key: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunStatusReconciliationBatch(Base):
    __tablename__ = "run_status_reconciliation_batches"
    __table_args__ = (PrimaryKeyConstraint("id", name="run_status_reconciliation_batches_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    source_revision: Mapped[str] = mapped_column(Text, nullable=False)


class RunStatusReconciliation(Base):
    __tablename__ = "run_status_reconciliations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="run_status_reconciliations_pkey"),
        CheckConstraint(
            "resolution_status = ANY (ARRAY['pending'::text, 'resolved'::text, 'dismissed'::text])",
            name="run_status_reconciliations_resolution_check",
        ),
        Index("run_status_reconciliations_pending_idx", "resolution_status", "run_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    batch_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("app.run_status_reconciliation_batches.id"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.runs.id", ondelete="CASCADE"), nullable=False
    )
    observed_status: Mapped[str] = mapped_column(Text, nullable=False)
    previous_status_version: Mapped[int] = mapped_column(Integer, nullable=False)
    isolation_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'blocked'")
    )
    resolution_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProviderProfileRow(Base):
    __tablename__ = "provider_profiles"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="provider_profiles_pkey"),
        CheckConstraint("config_version >= 1", name="provider_profiles_version_check"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_type: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    auth_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'none'"))
    credential_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    network_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'enabled'"))
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelAuthorizationGrantRow(Base):
    __tablename__ = "model_authorization_grants"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="model_authorization_grants_pkey"),
        Index("ix_model_auth_grants_user_status", "authorized_user", "status"),
        CheckConstraint("grant_version >= 1", name="model_auth_grants_version_check"),
        CheckConstraint("max_calls >= 1", name="model_auth_grants_max_calls_check"),
        CheckConstraint("calls_used >= 0", name="model_auth_grants_calls_used_check"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_user: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.provider_profiles.id"), nullable=False
    )
    provider_config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_input_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    allowed_baseline_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    grant_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContextManifestRow(Base):
    __tablename__ = "context_manifests"
    __table_args__ = (PrimaryKeyConstraint("id", name="context_manifests_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    grant_id: Mapped[str] = mapped_column(Text, nullable=False)
    approved_input_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    approved_baseline_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    fragment_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    content_hashes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    summary_strategy: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'refs_only'")
    )
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BudgetAccountRow(Base):
    __tablename__ = "budget_accounts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="budget_accounts_pkey"),
        CheckConstraint("account_version >= 1", name="budget_accounts_version_check"),
        UniqueConstraint("scope_type", "scope_id", "currency", name="uq_budget_accounts_scope"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    soft_limit: Mapped[str | None] = mapped_column(Text, nullable=True)
    hard_limit: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settled_amount: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'0'"))
    reserved_amount: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'0'"))
    settled_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    hard_cost_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    price_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BudgetReservationRow(Base):
    __tablename__ = "budget_reservations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="budget_reservations_pkey"),
        Index("ix_budget_reservations_call", "call_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.budget_accounts.id"), nullable=False
    )
    call_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    account_version_at_reserve: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelCallRow(Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="model_calls_pkey"),
        UniqueConstraint("idempotency_key", name="uq_model_calls_idempotency"),
        Index("ix_model_calls_provider_status", "provider_id", "status"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.provider_profiles.id"), nullable=False
    )
    provider_config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    grant_id: Mapped[str] = mapped_column(Text, nullable=False)
    grant_version: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    settlement_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'none'")
    )
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    context_manifest_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    reservation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelAttemptRow(Base):
    __tablename__ = "model_attempts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="model_attempts_pkey"),
        UniqueConstraint("call_id", "attempt_no", name="uq_model_attempts_call_no"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    call_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.model_calls.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageRecordRow(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="usage_records_pkey"),
        Index("ix_usage_records_call", "call_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    call_id: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_amount: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_estimated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_provider_usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SettlementRecordRow(Base):
    __tablename__ = "settlement_records"
    __table_args__ = (PrimaryKeyConstraint("id", name="settlement_records_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    reservation_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.budget_reservations.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProviderCapabilityCheckRow(Base):
    """Model-provider capability evidence (distinct from agent-profile checks)."""

    __tablename__ = "provider_capability_checks"
    __table_args__ = (PrimaryKeyConstraint("id", name="provider_capability_checks_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[str] = mapped_column(
        Text, ForeignKey("app.provider_profiles.id"), nullable=False
    )
    provider_config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    adapter_type: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    results: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GitDirtySnapshotRow(Base):
    """Explicit dirty-path capture for managed_dirty_snapshot strategy (D07)."""

    __tablename__ = "git_dirty_snapshots"
    __table_args__ = (PrimaryKeyConstraint("id", name="git_dirty_snapshots_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_id: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    path_count: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_paths: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GitWorkspaceRow(Base):
    """Platform-managed git worktree binding (D07)."""

    __tablename__ = "git_workspaces"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="git_workspaces_pkey"),
        Index("ix_git_workspaces_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_id: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    worktree_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_root: Mapped[str] = mapped_column(Text, nullable=False)
    head_commit: Mapped[str | None] = mapped_column(Text, nullable=True)
    dirty_included: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    dirty_snapshot_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    restrictions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GitWorkspaceSnapshotRow(Base):
    __tablename__ = "git_workspace_snapshots"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="git_workspace_snapshots_pkey"),
        Index("ix_git_workspace_snapshots_workspace", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    head_commit: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_operation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GitIntegrationRow(Base):
    """Integration merge result. stage_released stays false until D10."""

    __tablename__ = "git_integrations"
    __table_args__ = (PrimaryKeyConstraint("id", name="git_integrations_pkey"),)

    id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    worktree_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_workspace_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    conflict_paths: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_released: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentObjectRow(Base):
    """Content-addressed immutable blob metadata (D06)."""

    __tablename__ = "content_objects"
    __table_args__ = (PrimaryKeyConstraint("content_hash", name="content_objects_pkey"),)

    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    gc_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gc_protect_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ArtifactCandidateRow(Base):
    """Fixed candidate snapshot — verify only against frozen hashes (D06)."""

    __tablename__ = "artifact_candidates"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="artifact_candidates_pkey"),
        Index("ix_artifact_candidates_project_state", "project_id", "state"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    contract_id: Mapped[str] = mapped_column(Text, nullable=False)
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    environment_hash: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArtifactValidationReportRow(Base):
    __tablename__ = "artifact_validation_reports"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="artifact_validation_reports_pkey"),
        Index("ix_artifact_validation_reports_candidate", "candidate_id"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_id: Mapped[str] = mapped_column(Text, nullable=False)
    validator_id: Mapped[str] = mapped_column(Text, nullable=False)
    validator_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    contract_hash: Mapped[str] = mapped_column(Text, nullable=False)
    environment_hash: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArtifactPublicationRow(Base):
    """Verified publication record. stage_released stays false until D10."""

    __tablename__ = "artifact_publications"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="artifact_publications_pkey"),
        UniqueConstraint("idempotency_key", name="uq_artifact_publications_idem"),
        Index("ix_artifact_publications_artifact", "artifact_id", "version"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_id: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    stage_released: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunnerOperationRow(Base):
    """Controlled Runner ticket / operation ledger (D05)."""

    __tablename__ = "runner_operations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="runner_operations_pkey"),
        Index("ix_runner_operations_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_root: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    action_digest: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    process_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    require_effective_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AsyncOperationRow(Base):
    """Durable async operation status (D02 probes/calls) — queryable after 202."""

    __tablename__ = "async_operations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="async_operations_pkey"),
        Index("ix_async_operations_type_status", "operation_type", "status"),
    )

    id: Mapped[str] = mapped_column(Text, nullable=False)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'accepted'"))
    run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    grant_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    outbox_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertain_side_effects: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    replay_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


APP_BASELINE_TABLES = (
    "projects",
    "runs",
    "plans",
    "work_packages",
    "task_attempts",
    "events",
    "outbox",
    "run_leases",
    "idempotency_requests",
    "schema_migrations",
)

APP_V14_TABLES = (
    "planning_contexts",
    "plan_proposals",
    "plan_confirmation_requests",
    "plan_decisions",
    "agent_products",
    "agent_installations",
    "agent_profiles",
    "role_assignments",
    "agent_instances",
    "native_sessions",
    "instance_operations",
    "instance_attempts",
    "execution_requests",
    "stages",
    "gate_decisions",
    "collaboration_messages",
    "message_deliveries",
    "delivery_attempts",
    "protocol_requests",
    "native_event_receipts",
    "approval_mappings",
    "capability_checks",
    "stage_submissions",
    "candidate_sets",
    "review_cycles",
    "review_requests",
    "review_reports",
    "stage_user_decisions",
    "rework_orders",
    "release_records",
    "evidence_fingerprints",
    "driver_threads",
    "scheduler_operations",
    "run_waits",
    "run_wake_signals",
    "recovery_cases",
    "recovery_observations",
    "recovery_decisions",
    "platform_admission",
    "event_replay_windows",
    "integration_selections",
    "integration_builds",
    "integration_versions",
    "integration_validation_cycles",
    "integration_check_runs",
    "integration_check_reports",
    "integration_review_requests",
    "integration_review_reports",
    "delivery_manifests",
    "run_deliveries",
    "acceptance_decisions",
    "run_status_reconciliation_batches",
    "run_status_reconciliations",
    "execution_environments",
    "directory_grants",
    "project_baselines",
    "project_inputs",
    "environment_path_bindings",
    "provider_profiles",
    "model_authorization_grants",
    "context_manifests",
    "budget_accounts",
    "budget_reservations",
    "model_calls",
    "model_attempts",
    "usage_records",
    "settlement_records",
    "provider_capability_checks",
    "async_operations",
    "runner_operations",
    "content_objects",
    "artifact_candidates",
    "artifact_validation_reports",
    "artifact_publications",
    "git_dirty_snapshots",
    "git_workspaces",
    "git_workspace_snapshots",
    "git_integrations",
)

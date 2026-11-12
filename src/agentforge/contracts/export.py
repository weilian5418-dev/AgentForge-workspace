"""Generate JSON Schema / OpenAPI artifacts from live Pydantic models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentforge.api.app import create_app
from agentforge.contracts.agents import (
    AgentInstance,
    AgentProfile,
    ExecutionEligibility,
    ExecutionRequest,
    InstanceOperation,
    NativeSession,
    RoleAssignment,
    RoleSpec,
)
from agentforge.contracts.artifacts import (
    AcceptanceRequest,
    ArtifactContract,
    ArtifactRef,
    DeliveryManifest,
    WorkPackage,
)
from agentforge.contracts.errors import ErrorResponse
from agentforge.contracts.events import EventEnvelope, event_payload_json_schema
from agentforge.contracts.git_workspaces import (
    CandidateSnapshotRef,
    CaptureDirtySnapshotRequest,
    CleanupWorktreeRequest,
    DirtySnapshotView,
    FreezeWorkspaceSnapshotRequest,
    GitStopEvidence,
    IntegrateRequest,
    IntegrationView,
    PrepareWorktreeRequest,
    WorkspaceSnapshotView,
    WorktreeView,
)
from agentforge.contracts.messages import (
    CollaborationMessage,
    DeliveryAttempt,
    MessageCreateRequest,
    MessageDelivery,
    NativeEventReceipt,
    ProtocolRequest,
)
from agentforge.contracts.plans import (
    ApprovalSummary,
    AssignmentSet,
    CompilationReport,
    ConfirmationRequest,
    PlanDecisionRequest,
    PlanProposal,
    PlanProposalRequest,
    PlanResponse,
    PlanRevision,
    PlanRevisionRequest,
    PlanSpec,
    PlanningContext,
    RoutingDecision,
)
from agentforge.contracts.projects import ProjectCreateRequest, ProjectResponse
from agentforge.contracts.providers import (
    BudgetAccountCreateRequest,
    ModelAuthorizationGrantCreateRequest,
    ModelCallCreateRequest,
    ProviderCreateRequest,
    ProviderProfile,
)
from agentforge.contracts.runs import RunCreateRequest, RunSnapshotResponse
from agentforge.contracts.stages import GateDecision, IntegrationVersion, ReviewReport, Stage
from agentforge.contracts.system import CapabilityResponse, HealthResponse, ReadinessResponse
from agentforge.contracts.templates import TemplateDocument, TemplateImportRequest
from agentforge.contracts.validations import ValidationReport
from agentforge.core.config import Settings

SCHEMA_MODELS: dict[str, type[Any]] = {
    "artifact-ref.schema.json": ArtifactRef,
    "artifact-contract.schema.json": ArtifactContract,
    "work-package.schema.json": WorkPackage,
    "delivery-manifest.schema.json": DeliveryManifest,
    "acceptance-request.schema.json": AcceptanceRequest,
    "validation-report.schema.json": ValidationReport,
    "project-create-request.schema.json": ProjectCreateRequest,
    "project-response.schema.json": ProjectResponse,
    "provider-create-request.schema.json": ProviderCreateRequest,
    "provider-response.schema.json": ProviderProfile,
    "provider-profile.schema.json": ProviderProfile,
    "model-authorization-grant-create-request.schema.json": ModelAuthorizationGrantCreateRequest,
    "model-call-create-request.schema.json": ModelCallCreateRequest,
    "budget-account-create-request.schema.json": BudgetAccountCreateRequest,
    "run-create-request.schema.json": RunCreateRequest,
    "run-snapshot-response.schema.json": RunSnapshotResponse,
    "plan-response.schema.json": PlanResponse,
    "plan-spec.schema.json": PlanSpec,
    "plan-proposal.schema.json": PlanProposal,
    "plan-proposal-request.schema.json": PlanProposalRequest,
    "plan-revision.schema.json": PlanRevision,
    "plan-revision-request.schema.json": PlanRevisionRequest,
    "plan-decision-request.schema.json": PlanDecisionRequest,
    "planning-context.schema.json": PlanningContext,
    "compilation-report.schema.json": CompilationReport,
    "assignment-set.schema.json": AssignmentSet,
    "confirmation-request.schema.json": ConfirmationRequest,
    "routing-decision.schema.json": RoutingDecision,
    "role-spec.schema.json": RoleSpec,
    "role-assignment.schema.json": RoleAssignment,
    "agent-profile.schema.json": AgentProfile,
    "agent-instance.schema.json": AgentInstance,
    "native-session.schema.json": NativeSession,
    "instance-operation.schema.json": InstanceOperation,
    "execution-eligibility.schema.json": ExecutionEligibility,
    "execution-request.schema.json": ExecutionRequest,
    "stage.schema.json": Stage,
    "gate-decision.schema.json": GateDecision,
    "review-report.schema.json": ReviewReport,
    "integration-version.schema.json": IntegrationVersion,
    "approval-summary.schema.json": ApprovalSummary,
    "message-create-request.schema.json": MessageCreateRequest,
    "collaboration-message.schema.json": CollaborationMessage,
    "message-delivery.schema.json": MessageDelivery,
    "delivery-attempt.schema.json": DeliveryAttempt,
    "protocol-request.schema.json": ProtocolRequest,
    "native-event-receipt.schema.json": NativeEventReceipt,
    "template-document.schema.json": TemplateDocument,
    "template-import-request.schema.json": TemplateImportRequest,
    "error-response.schema.json": ErrorResponse,
    "event-envelope.schema.json": EventEnvelope,
    "health-response.schema.json": HealthResponse,
    "readiness-response.schema.json": ReadinessResponse,
    "capability-response.schema.json": CapabilityResponse,
    "git-capture-dirty-snapshot-request.schema.json": CaptureDirtySnapshotRequest,
    "git-dirty-snapshot-view.schema.json": DirtySnapshotView,
    "git-prepare-worktree-request.schema.json": PrepareWorktreeRequest,
    "git-worktree-view.schema.json": WorktreeView,
    "git-stop-evidence.schema.json": GitStopEvidence,
    "git-freeze-workspace-snapshot-request.schema.json": FreezeWorkspaceSnapshotRequest,
    "git-workspace-snapshot-view.schema.json": WorkspaceSnapshotView,
    "git-candidate-snapshot-ref.schema.json": CandidateSnapshotRef,
    "git-integrate-request.schema.json": IntegrateRequest,
    "git-integration-view.schema.json": IntegrationView,
    "git-cleanup-worktree-request.schema.json": CleanupWorktreeRequest,
}

EVENT_PAYLOAD_TYPES = (
    "routing.decided",
    "plan.proposed",
    "run.status_changed",
    "task.started",
    "task.blocked",
    "artifact.candidate_created",
    "validation.completed",
    "artifact.published",
    "approval.requested",
    "usage.updated",
    "run.delivery_ready",
    "resync_required",
    "agent.discovery_completed",
    "agent.capabilities_validated",
    "assignment.confirmed",
    "instance.started",
    "instance.stop_requested",
    "instance.stopped",
    "instance.needs_reconciliation",
    "message.delivered",
    "message.delivery_unknown",
    "protocol.request_matched",
    "protocol.request_conflict",
    "native_event.ingested",
    "native_approval.requested",
    "native_approval.confirmed",
    "stage.candidate_frozen",
    "stage.rework_requested",
    "stage.released",
    "integration.validated",
)


def build_contract_documents() -> dict[str, Any]:
    schema_app = create_app(Settings.model_construct())
    documents: dict[str, Any] = {"openapi.json": schema_app.openapi()}
    for filename, model in SCHEMA_MODELS.items():
        documents[filename] = model.model_json_schema(by_alias=True)
    documents["event-payloads.schema.json"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AgentForgeEventPayloads",
        "description": "Per-type event payload schemas; SSE transport is BE-15.",
        "properties": {
            event_type: event_payload_json_schema(event_type)  # type: ignore[arg-type]
            for event_type in EVENT_PAYLOAD_TYPES
        },
    }
    return documents


def export_contract_documents(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    documents = build_contract_documents()
    for name, data in documents.items():
        (output / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return len(documents)

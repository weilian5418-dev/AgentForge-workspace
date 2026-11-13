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
from agentforge.contracts.collaboration import (
    DriverResult,
    DriverStatusResponse,
    GraphBinding,
    IntegrationHandoff,
    PlatformAdmission,
    RunControlRequest,
    RunWait,
    RunWaitListResponse,
    WakeSignal,
)
from agentforge.contracts.delivery import (
    AcceptanceDecisionRequest,
    AcceptanceResult,
    DeliveryManifestPayload,
    DeliveryManifestView,
    DeliveryPointerView,
)
from agentforge.contracts.errors import ErrorResponse
from agentforge.contracts.event_stream import (
    EventBatch,
    PublicEventEnvelope,
    ResolvedCursor,
    SseControlFrame,
)
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
from agentforge.contracts.integrations import (
    CheckPolicy,
    FinalReviewResponseRequest,
    IntegrationEvaluation,
    IntegrationSelection,
    IntegrationSelectionRequest,
    IntegrationVersionView,
    ValidationCycleRequest,
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
    PlanningContext,
    PlanProposal,
    PlanProposalRequest,
    PlanResponse,
    PlanRevision,
    PlanRevisionRequest,
    PlanSpec,
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
from agentforge.contracts.recovery import (
    HandoffManifest,
    RecoveryCase,
    RecoveryCaseListResponse,
    RecoveryDecisionRequest,
    RecoveryReport,
)
from agentforge.contracts.runs import RunActionRequest, RunCreateRequest, RunSnapshotResponse
from agentforge.contracts.snapshots import (
    AgentCounts,
    AgentSummaryResponse,
    RunSnapshot,
)
from agentforge.contracts.stages import (
    CandidateSet,
    GateDecision,
    GateEvaluationInput,
    GateEvaluationResult,
    IntegrationVersion,
    ReleaseRecord,
    ReviewCycle,
    ReviewReport,
    ReviewRequest,
    ReworkOrder,
    Stage,
    StageUserDecision,
)
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
    "run-action-request.schema.json": RunActionRequest,
    "run-snapshot-response.schema.json": RunSnapshotResponse,
    "graph-binding.schema.json": GraphBinding,
    "wake-signal.schema.json": WakeSignal,
    "run-wait.schema.json": RunWait,
    "run-wait-list-response.schema.json": RunWaitListResponse,
    "driver-result.schema.json": DriverResult,
    "driver-status-response.schema.json": DriverStatusResponse,
    "run-control-request.schema.json": RunControlRequest,
    "platform-admission.schema.json": PlatformAdmission,
    "integration-handoff.schema.json": IntegrationHandoff,
    "recovery-case.schema.json": RecoveryCase,
    "recovery-case-list-response.schema.json": RecoveryCaseListResponse,
    "recovery-report.schema.json": RecoveryReport,
    "recovery-decision-request.schema.json": RecoveryDecisionRequest,
    "handoff-manifest.schema.json": HandoffManifest,
    "run-snapshot.schema.json": RunSnapshot,
    "agent-counts.schema.json": AgentCounts,
    "agent-summary-response.schema.json": AgentSummaryResponse,
    "sse-control-frame.schema.json": SseControlFrame,
    "resolved-cursor.schema.json": ResolvedCursor,
    "public-event-envelope.schema.json": PublicEventEnvelope,
    "event-batch.schema.json": EventBatch,
    "integration-selection-request.schema.json": IntegrationSelectionRequest,
    "integration-selection.schema.json": IntegrationSelection,
    "integration-version-view.schema.json": IntegrationVersionView,
    "check-policy.schema.json": CheckPolicy,
    "validation-cycle-request.schema.json": ValidationCycleRequest,
    "final-review-response-request.schema.json": FinalReviewResponseRequest,
    "integration-evaluation.schema.json": IntegrationEvaluation,
    "delivery-manifest-payload.schema.json": DeliveryManifestPayload,
    "delivery-manifest-view.schema.json": DeliveryManifestView,
    "delivery-pointer-view.schema.json": DeliveryPointerView,
    "acceptance-decision-request.schema.json": AcceptanceDecisionRequest,
    "acceptance-result.schema.json": AcceptanceResult,
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
    "candidate-set.schema.json": CandidateSet,
    "review-cycle.schema.json": ReviewCycle,
    "review-request.schema.json": ReviewRequest,
    "stage-user-decision.schema.json": StageUserDecision,
    "gate-evaluation-input.schema.json": GateEvaluationInput,
    "gate-evaluation-result.schema.json": GateEvaluationResult,
    "gate-decision.schema.json": GateDecision,
    "rework-order.schema.json": ReworkOrder,
    "release-record.schema.json": ReleaseRecord,
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
    "driver.wait_registered",
    "driver.wake_recorded",
    "run.pause_requested",
    "run.paused",
    "run.resume_requested",
    "run.cancel_requested",
    "run.canceled",
    "recovery.started",
    "recovery.observed",
    "recovery.blocked",
    "recovery.applied",
    "run.collaboration_ready",
    "integration.build_requested",
    "integration.built",
    "integration.conflicted",
    "integration.check_completed",
    "integration.review_completed",
    "delivery.manifest_published",
    "delivery.manifest_invalidated",
    "acceptance.decided",
    "state.invalidated",
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

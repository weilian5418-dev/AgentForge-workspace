"""Proposal generation — rules or model; never writes an effective approved plan."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from agentforge.contracts.plans import (
    CompilationDiagnostic,
    PlanningContext,
    PlanProposal,
    PlanSpec,
    PlanStageDefinition,
    PlanWorkPackageSpec,
    ProposalSource,
    RoutingDecision,
)
from agentforge.contracts.stages import StageGatePolicy
from agentforge.storage.models import Run

ProposalSourceChoice = Literal["rules", "model", "auto"]


class ProposalError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _artifact_for_kind(kind: str, index: int) -> dict[str, Any]:
    path = {
        "markdown": f"outputs/notes_{index}.md",
        "code": f"src/module_{index}.py",
        "pptx": f"outputs/deck_{index}.pptx",
    }.get(kind, f"outputs/artifact_{index}.bin")
    validator = {
        "markdown": "markdown",
        "code": "syntax",
        "pptx": "pptx_structure",
    }.get(kind, "generic")
    return {
        "id": f"out_{kind}_{index}",
        "kind": kind if kind in {"code", "markdown", "pptx"} else "markdown",
        "relativePath": path,
        "required": True,
        "criteria": [
            {
                "id": "acceptance",
                "description": f"Acceptable {kind} deliverable",
                "required": True,
            }
        ],
        "validators": [
            {
                "id": validator,
                "version": "1",
                "criterionIds": ["acceptance"],
                "required": True,
                "config": {},
            }
        ],
        "independentReview": False,
    }


def build_rules_draft(*, run: Run, context: PlanningContext) -> PlanSpec:
    """Deterministic single-writer draft from run goal/outputs. Suggestion only."""
    kinds = list(run.outputs or ["markdown"])
    deliverables = [_artifact_for_kind(str(kind), i) for i, kind in enumerate(kinds)]
    write_scopes = sorted({str(d["relativePath"]).rsplit("/", 1)[0] + "/" for d in deliverables})
    if not write_scopes:
        write_scopes = ["outputs/"]
    payload = {
        "goal": run.goal,
        "constraints": ["planning_readonly"],
        "baselineId": context.baseline_id,
        "inputIds": list(context.input_ids),
        "routing": {
            "mode": "single" if run.mode in {"auto", "single"} else "team",
            "reason": "rules: default single writer until user revises",
            "ruleVersion": "rules-v1",
            "modelReviewCount": 0,
        },
        "roles": [
            {
                "roleId": "role_writer",
                "responsibilities": "Produce required deliverables",
                "requiredCapabilities": ["write_files"],
                "readScopes": ["src/", "inputs/"],
                "writeScopes": write_scopes,
                "budget": {},
                "independentReview": False,
                "requireUserDecisionOnGate": False,
            }
        ],
        "workPackages": [
            {
                "id": "wp_deliver",
                "stageId": "stg_impl",
                "objective": run.goal,
                "ownerRoleId": "role_writer",
                "dependsOn": [],
                "inputs": [],
                "deliverables": deliverables,
                "readScopes": ["src/", "inputs/"],
                "writeScopes": write_scopes,
                "toolPolicyId": "restricted",
                "maxRepairRounds": 3,
            }
        ],
        "stages": [
            {
                "stageId": "stg_impl",
                "dependencies": [],
                "requiredWorkPackages": ["wp_deliver"],
                "gatePolicy": {
                    "policy": "stage_atomic",
                    "version": 1,
                    "requiredReviewRoles": [],
                    "requireUserDecision": True,
                    "maxRepairRounds": 3,
                },
            }
        ],
        "gatePolicyVersion": 1,
        "resourceEstimate": {
            "basis": "rules-heuristic",
            "uncertainties": ["model_not_used"],
        },
    }
    return PlanSpec.model_validate(payload)


class ProposalGenerator:
    """Produces PlanProposal candidates. Cannot approve, escalate capabilities, or start agents."""

    def __init__(
        self,
        *,
        model_invoke: Callable[..., Any] | None = None,
    ) -> None:
        self._model_invoke = model_invoke

    def generate(
        self,
        *,
        run: Run,
        context: PlanningContext,
        source: ProposalSourceChoice = "auto",
    ) -> PlanProposal:
        resolved = self._resolve_source(run, source)
        if resolved == "model":
            return self._generate_model(run=run, context=context)
        draft = build_rules_draft(run=run, context=context)
        return PlanProposal(
            proposal_id=f"pprop_{uuid.uuid4().hex[:20]}",
            run_id=run.id,
            context_id=context.context_id,
            source="rules",
            model_call_id=None,
            draft=draft,
            diagnostics=[],
            created_at=_now(),
        )

    def _resolve_source(self, run: Run, source: ProposalSourceChoice) -> ProposalSource:
        if source == "rules":
            return "rules"
        if source == "model":
            if not run.planning_provider_profile_id:
                raise ProposalError(
                    "planning_provider_required",
                    "Model planning requested but planningProviderProfileId is not configured",
                )
            if self._model_invoke is None:
                raise ProposalError(
                    "model_gateway_unavailable",
                    "Model planning requested but ModelGateway is not wired",
                )
            return "model"
        # auto: prefer rules; only use model when provider + gateway are both present
        if run.planning_provider_profile_id and self._model_invoke is not None:
            return "model"
        return "rules"

    def _generate_model(self, *, run: Run, context: PlanningContext) -> PlanProposal:
        assert self._model_invoke is not None
        # Caller must have built a real ContextManifest before setting context_manifest_id.
        if context.context_manifest_id is None:
            raise ProposalError(
                "context_manifest_required",
                "Model planning requires a real ContextManifest; refusing to fake one",
            )
        result = self._model_invoke(run=run, context=context)
        model_call_id = None
        draft: PlanSpec
        diagnostics: list[CompilationDiagnostic] = []
        if isinstance(result, PlanSpec):
            draft = result
        elif isinstance(result, dict):
            model_call_id = result.get("modelCallId") or result.get("model_call_id")
            raw_draft = result.get("draft") or result.get("planSpec") or result
            try:
                draft = PlanSpec.model_validate(raw_draft)
            except Exception as exc:
                # Limited structural repair: fall back to rules draft + diagnostic.
                draft = build_rules_draft(run=run, context=context)
                diagnostics.append(
                    CompilationDiagnostic(
                        code="model_structure_invalid",
                        severity="error",
                        message=f"Model draft invalid; rules fallback used ({exc})",
                        suggested_resolution="Revise manually or retry with budget",
                    )
                )
        else:
            raise ProposalError("model_result_invalid", "Model invoke returned unsupported type")
        # Ensure routing records that a model pass occurred when successful parse.
        if not diagnostics:
            routing = draft.routing.model_copy(
                update={"model_review_count": max(1, draft.routing.model_review_count)}
            )
            draft = draft.model_copy(update={"routing": routing})
        return PlanProposal(
            proposal_id=f"pprop_{uuid.uuid4().hex[:20]}",
            run_id=run.id,
            context_id=context.context_id,
            source="model",
            model_call_id=str(model_call_id) if model_call_id else None,
            draft=draft,
            diagnostics=diagnostics,
            created_at=_now(),
        )


# Re-export symbols used by tests / callers for fixture building
__all__ = [
    "ProposalError",
    "ProposalGenerator",
    "ProposalSourceChoice",
    "build_rules_draft",
    "PlanSpec",
    "PlanStageDefinition",
    "PlanWorkPackageSpec",
    "RoutingDecision",
    "StageGatePolicy",
]

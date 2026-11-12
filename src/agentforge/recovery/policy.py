"""Pure recovery policy — no I/O, no Run mutation, no adapter calls (D11.9)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentforge.contracts.recovery import (
    NativeObservation,
    OperationObservation,
    RecoveryObservation,
    RecoveryRecommendation,
    WorkspaceObservation,
)


@dataclass(frozen=True)
class RecoveryContext:
    """Frozen case context for decide_recovery."""

    run_id: str
    case_id: str
    context_hash: str
    plan_version: int | None
    assignment_version: int | None
    generation: int
    status_version: int
    reason: str | None = None
    work_package_id: str | None = None
    extras: dict[str, Any] | None = None


def _obs_by_type(
    observations: tuple[RecoveryObservation, ...], subject_type: str
) -> list[RecoveryObservation]:
    return [o for o in observations if o.subject_type == subject_type]


def decide_recovery(
    context: RecoveryContext,
    observations: tuple[RecoveryObservation, ...],
    *,
    native: NativeObservation | None = None,
    workspace: WorkspaceObservation | None = None,
    operations: tuple[OperationObservation, ...] = (),
) -> RecoveryRecommendation:
    """Deterministic recommendation from fixed context + observations."""
    _ = context  # context pins versions; observations carry evidence

    # 1) Unknown native side effects occupy the slot.
    for op in operations:
        if op.possibly_executed and op.support in {"unknown", "partial"}:
            return RecoveryRecommendation(
                action="await_reconciliation",
                reason_codes=["pending_side_effects", "native_state_unknown"],
                required_evidence=["native_operation_receipt"],
                requires_user_decision=False,
                detail=f"operation {op.operation_id} possibly executed",
            )
        if op.ledger_status == "unknown":
            return RecoveryRecommendation(
                action="await_reconciliation",
                reason_codes=["pending_side_effects"],
                required_evidence=["operation_reconciliation"],
                requires_user_decision=False,
                detail=f"ledger unknown for {op.operation_id}",
            )

    # 2) Authorization / login.
    for o in _obs_by_type(observations, "authorization"):
        if o.result_code in {"login_expired", "grant_revoked", "unauthorized"}:
            return RecoveryRecommendation(
                action="wait_authorization",
                reason_codes=["authorization_invalid", "login_expired"],
                required_evidence=["reauthentication"],
                requires_user_decision=True,
                detail=o.summary or o.result_code,
            )

    # 3) Workspace mismatch — never auto reset.
    if workspace is not None:
        if workspace.support == "unsupported":
            return RecoveryRecommendation(
                action="block_or_rework",
                reason_codes=["workspace_mismatch"],
                required_evidence=["workspace_identity"],
                requires_user_decision=True,
                detail="workspace probe unsupported",
            )
        if workspace.identity_matched is False:
            return RecoveryRecommendation(
                action="block_or_rework",
                reason_codes=["workspace_mismatch"],
                required_evidence=["workspace_reconcile"],
                requires_user_decision=True,
                detail="workspace identity mismatch",
            )
        if workspace.support == "unknown" or workspace.identity_matched is None:
            return RecoveryRecommendation(
                action="await_reconciliation",
                reason_codes=["workspace_mismatch", "native_state_unknown"],
                required_evidence=["workspace_probe"],
                requires_user_decision=False,
                detail="workspace identity not verified",
            )

    # 4) Candidate / stage hash drift.
    for o in _obs_by_type(observations, "candidate"):
        if o.result_code in {"hash_mismatch", "candidate_hash_mismatch", "stale_candidate"}:
            return RecoveryRecommendation(
                action="block_or_rework",
                reason_codes=["candidate_hash_mismatch"],
                required_evidence=["fresh_candidate_set"],
                requires_user_decision=True,
                detail=o.summary or o.result_code,
            )

    # 5) Budget unknown — never auto-release.
    for o in _obs_by_type(observations, "budget"):
        if o.result_code in {"budget_unknown", "settlement_unknown"} or o.support == "unknown":
            return RecoveryRecommendation(
                action="await_reconciliation",
                reason_codes=["budget_unknown"],
                required_evidence=["settlement_receipt"],
                requires_user_decision=False,
                detail=o.summary or "budget unsettled",
            )

    # 6) Native execution facts.
    if native is None:
        return RecoveryRecommendation(
            action="await_reconciliation",
            reason_codes=["native_state_unknown"],
            required_evidence=["native_observation"],
            requires_user_decision=False,
            detail="no native observation",
        )

    if native.support == "unsupported":
        return RecoveryRecommendation(
            action="handoff_new_session",
            reason_codes=["resume_unsupported", "handoff_confirmation_required"],
            required_evidence=["stop_proof", "user_handoff_confirm"],
            requires_user_decision=True,
            detail="native resume unsupported",
        )

    if native.support in {"unknown", "partial"}:
        return RecoveryRecommendation(
            action="await_reconciliation",
            reason_codes=["native_state_unknown"],
            required_evidence=["native_observation"],
            requires_user_decision=False,
            detail="native probe inconclusive",
        )

    # support == supported with required safety fields (enforced by contract).
    if native.children_running is True or (
        native.running is False and native.writes_quiesced is False
    ):
        return RecoveryRecommendation(
            action="await_stop_verification",
            reason_codes=["pending_stop_verification"],
            required_evidence=["stop_proof"],
            requires_user_decision=False,
            detail="children or writes still active",
        )

    if native.running is True:
        return RecoveryRecommendation(
            action="observe_existing",
            reason_codes=[],
            required_evidence=[],
            requires_user_decision=False,
            detail="native task still running under current binding",
        )

    # Verified stopped.
    if (
        native.running is False
        and native.children_running is False
        and native.writes_quiesced is True
    ):
        attempt_status = (context.extras or {}).get("attemptStatus")
        if attempt_status in {"submitted", "awaiting_stop_verification", "stopped"}:
            return RecoveryRecommendation(
                action="continue_stage_processing",
                reason_codes=[],
                required_evidence=[],
                requires_user_decision=False,
                detail="stopped with existing submission path",
            )
        # Session present and stopped — resume may be offered; user confirms if needed.
        if native.session_ref and native.native_session_id:
            return RecoveryRecommendation(
                action="resume_existing",
                reason_codes=[],
                required_evidence=["resume_capability"],
                requires_user_decision=False,
                detail="session stopped and bound; resume_existing allowed",
            )
        return RecoveryRecommendation(
            action="handoff_new_session",
            reason_codes=["handoff_confirmation_required"],
            required_evidence=["stop_proof", "user_handoff_confirm"],
            requires_user_decision=True,
            detail="stopped but session binding incomplete",
        )

    return RecoveryRecommendation(
        action="await_reconciliation",
        reason_codes=["native_state_unknown"],
        required_evidence=["native_observation"],
        requires_user_decision=False,
        detail="unclassified native state",
    )
